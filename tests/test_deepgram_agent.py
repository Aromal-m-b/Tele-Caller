import asyncio
import json
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from langchain_core.documents import Document

from telecom_support.deepgram_agent import AgentConfig, AgentSession, AgentPlayback, agent_settings, run_voice_agent, AGENT_URL
from telecom_support.speech_input import SpeechSettings, SpeechInputError


def test_current_settings_schema_and_no_customer_or_key_in_payload():
    payload = agent_settings(SpeechSettings('private-key', audio_output=True), AgentConfig())
    assert payload['type'] == 'Settings'
    assert payload['audio']['output']['sample_rate'] == 24000
    assert payload['agent']['think']['provider'] == {'type':'open_ai','model':'gpt-4o-mini'}
    assert 'private-key' not in json.dumps(payload)
    assert payload['agent']['think']['functions'][1]['defer_until_eot'] is True
    assert 'greeting' not in payload['agent']


def make_session(retrieve=lambda _: [Document(page_content='Fixture: recharge via website')]):
    sent = []
    async def send(text): sent.append(json.loads(text))
    device = SimpleNamespace(write=AsyncMock(), drain=AsyncMock())
    session = AgentSession(SimpleNamespace(retriever=SimpleNamespace(invoke=retrieve)),
        SpeechSettings('fake', audio_output=True), AgentConfig(), SimpleNamespace(send=send), device, write=lambda _:None)
    return session, sent


def test_rag_function_returns_content_and_preserves_signature():
    async def run():
        session, sent = make_session()
        await session.function(dict(id='fc1', name='query_rag', arguments='{"query":"How can I recharge online?"}', thought_signature='sig'))
        assert sent[0]['type'] == 'FunctionCallResponse'
        assert sent[0]['id'] == 'fc1' and sent[0]['thought_signature'] == 'sig'
        assert 'Fixture' in sent[0]['content'] and 'output' not in sent[0]
    asyncio.run(run())


@pytest.mark.parametrize('arguments', ['{"query":"write Python code"}', '{"query":"recharge","customer_id":"other"}', '{invalid'])
def test_tool_guard_and_argument_schema_block_unsupported_calls(arguments):
    async def run():
        retrieve = Mock(side_effect=AssertionError('No retrieval permitted'))
        session, sent = make_session(retrieve)
        await session.function(dict(id='fc1',name='query_rag',arguments=arguments))
        retrieve.assert_not_called()
        assert sent
    asyncio.run(run())


def test_cancelled_lookup_never_sends_late_response():
    async def run():
        entered, release = threading.Event(), threading.Event()
        def retrieve(_):
            entered.set()
            release.wait(2)
            return [Document(page_content='Old result')]
        session, sent = make_session(retrieve)
        await session.event(dict(type='FunctionCallRequest', functions=[dict(id='fc1',name='query_rag',client_side=True,arguments='{"query":"How can I recharge online?"}')]))
        assert await asyncio.to_thread(entered.wait,1)
        await session.event(dict(type='FunctionCallCancelled',functions=[dict(id='fc1')]))
        release.set()
        await asyncio.gather(*session.calls.values())
        await asyncio.gather(*session.workers)
        assert not sent
    asyncio.run(run())


def test_end_function_cannot_authorize_itself_or_access_accounts():
    async def run():
        session, sent = make_session()
        await session.function(dict(id='fc1',name='end_call',arguments='{}'))
        assert 'Not authorized' in sent[-1]['content'] and not session.ending
        await session.function(dict(id='fc2',name='get_customer_profile',arguments='{}'))
        assert 'Unknown function' in sent[-1]['content']
    asyncio.run(run())


def test_end_question_only_arms_confirmation_after_playback():
    async def run():
        session, sent = make_session()
        await session.event(dict(type='ConversationText',role='assistant',content='Would you like me to end the call?'))
        assert not session.control.pending
        session.audio_done()
        assert session.control.pending
        await session.event(dict(type='UserStartedSpeaking'))
        await session.event(dict(type='ConversationText',role='user',content='Yes, please. Yes.'))
        assert session.ending
    asyncio.run(run())


def test_playback_interrupt_discards_pending_frames():
    async def run():
        frames = []
        async def write(data):
            frames.append(data)
            playback.interrupt()
        playback = AgentPlayback(SimpleNamespace(write=write, drain=AsyncMock()), lambda:None, lambda:None)
        playback.put(b'\x00\x01'*4800)
        task = asyncio.create_task(playback.run())
        await asyncio.sleep(.01)
        task.cancel()
        await asyncio.gather(task,return_exceptions=True)
        assert len(frames) == 1 and len(frames[0]) == 960
    asyncio.run(run())


def test_split_confirmation_question_is_recognised_after_audio_drains():
    async def run():
        session, sent = make_session()
        await session.event(dict(type='ConversationText',role='assistant',content='Would you like me to'))
        await session.event(dict(type='ConversationText',role='assistant',content='end the call now?'))
        assert not session.control.pending
        session.audio_done()
        assert session.control.pending
    asyncio.run(run())


def test_injected_control_notice_is_not_a_real_user_or_hangup_consent():
    async def run():
        session, sent = make_session()
        text = 'Call-control notice: give a goodbye as the idle call closes.'
        session.quiet, session.idle_phase = 45, 2
        await session.notice(text)
        await session.event(dict(type='UserStartedSpeaking'))
        await session.event(dict(type='ConversationText',role='user',content=text))
        assert not session.history and not session.ending
        assert session.quiet == 45 and session.idle_phase == 2
    asyncio.run(run())


def test_handshake_greeting_and_direct_hangup_close_all_resources():
    async def run():
        closed, sent, frames = [], [], []
        drained = asyncio.Event()
        class Device:
            async def __aenter__(self): return self
            async def write(self,data): frames.append(data)
            async def drain(self): drained.set()
            async def __aexit__(self,*_): closed.append('speaker')
        class Mic:
            async def __aenter__(self): return self
            async def read(self): await asyncio.Event().wait()
            async def __aexit__(self,*_): closed.append('mic')
        class Socket:
            count = 0
            async def __aenter__(self): return self
            async def __aexit__(self,*_): closed.append('socket')
            async def recv(self):
                self.count += 1
                return json.dumps({'type':'Welcome' if self.count == 1 else 'SettingsApplied'})
            async def send(self,text): sent.append(json.loads(text))
            async def __aiter__(self):
                for payload in [dict(type='ConversationText',role='assistant',content="I'm Maya from Sun Direct."),b'\x00\x01'*480,dict(type='AgentAudioDone')]:
                    yield payload if isinstance(payload,bytes) else json.dumps(payload)
                await drained.wait()
                drained.clear()
                for payload in [dict(type='UserStartedSpeaking'),dict(type='ConversationText',role='user',content='Thank you. End this call now.'),dict(type='ConversationText',role='assistant',content='Take care. Goodbye!'),b'\x00\x02'*480,dict(type='AgentAudioDone')]:
                    yield payload if isinstance(payload,bytes) else json.dumps(payload)
                await asyncio.Event().wait()
        def connect(url,**kwargs):
            assert url == AGENT_URL
            assert kwargs['additional_headers']['Authorization'] == 'Token fake'
            return Socket()
        await asyncio.wait_for(run_voice_agent(SimpleNamespace(),SpeechSettings('fake',audio_output=True),
            config=AgentConfig(),connect_factory=connect,microphone_factory=lambda **_:Mic(),device_factory=lambda **_:Device(),write=lambda _:None),2)
        assert sorted(closed) == ['mic','socket','speaker']
        assert sent[0]['type'] == 'Settings'
        assert sent[1]['type'] == 'InjectUserMessage'
        assert len(frames) == 2
    asyncio.run(run())


def test_managed_agent_audit_uses_connection_cost_and_unknown_tokens(tmp_path):
    from telecom_support.call_audit import CallAudit
    now = [0]
    audit = CallAudit(tmp_path,clock=lambda:now[0])
    audit.backend, audit.agent_rate_per_minute = 'deepgram-agent', .075
    audit.stt_connected()
    now[0] = 120
    audit.stt_disconnected()
    audit.close()
    summary = json.loads(audit.path.read_text(encoding='utf-8').split('USAGE AND COST\n')[1])
    assert summary['estimated_total_usd'] == pytest.approx(.15)
    assert summary['total_tokens'] is None
    assert 'stt_conservative_estimate_usd' not in summary


@pytest.mark.parametrize('extra,disabled', [([],False),(['--no-database'],True)])
def test_cli_agent_respects_database_selection(monkeypatch,tmp_path,extra,disabled):
    import telecom_support.cli as cli
    import telecom_support.deepgram_agent as agent
    import telecom_support.rag as rag
    from telecom_support.settings import Settings
    monkeypatch.setattr('telecom_support.settings.ROOT',tmp_path)
    monkeypatch.setattr(cli.Settings,'load',lambda:Settings('fake'))
    monkeypatch.setattr(SpeechSettings,'load',lambda **_:SpeechSettings('fake',audio_output=True))
    db = Mock(return_value=(None,None))
    monkeypatch.setattr(cli,'database_service',db)
    app = SimpleNamespace(close=Mock())
    monkeypatch.setattr(rag,'build_application',Mock(return_value=app))
    run = AsyncMock()
    monkeypatch.setattr(agent,'run_voice_agent',run)
    cli.main(['--backend','deepgram-agent',*extra])
    assert db.call_args.args == (None,disabled)
    run.assert_awaited_once()
    app.close.assert_called_once()


def test_provider_errors_are_sanitized_and_do_not_leak_payload():
    async def run():
        session, sent = make_session()
        with pytest.raises(SpeechInputError,match='FAILED_TO_THINK') as result:
            await session.event(dict(type='Error',code='FAILED_TO_THINK',description='private-provider-content'))
        assert 'private-provider-content' not in str(result.value)
    asyncio.run(run())


def test_server_side_function_is_not_executed_locally():
    async def run():
        session, sent = make_session()
        await session.event(dict(type='FunctionCallRequest',functions=[dict(id='fc1',name='query_rag',client_side=False,arguments='{}')]))
        assert not session.calls and not sent
    asyncio.run(run())


def test_operation_notice_preserves_original_caller_response_timer():
    from telecom_support.latency import TurnTiming
    async def run():
        session, sent = make_session()
        output = []
        timer = TurnTiming(5,0,1,write=output.append)
        session.timer = timer
        text = 'Client operation result: request committed.'
        await session.notice(text,response_timer=timer)
        await session.event(dict(type='UserStartedSpeaking'))
        await session.event(dict(type='ConversationText',role='user',content=text))
        assert session.timer is timer
        assert not output
    asyncio.run(run())


def test_service_facts_are_not_forwarded_as_model_rewritten_embedding_query():
    async def run():
        retrieve = Mock(return_value=[])
        session, sent = make_session(retrieve)
        session.business_data_seen = True
        session.history = ['How can I recharge online?']
        await session.search({'query':'Sun Direct recharge for PRIVATE-DELIVERY-ADDRESS'})
        retrieve.assert_called_once_with('How can I recharge online?')
    asyncio.run(run())
