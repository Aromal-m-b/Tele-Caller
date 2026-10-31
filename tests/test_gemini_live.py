import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

from google.genai import types
from telecom_support.gemini_live import LiveConfig, run_gemini_live


def test_setup_uses_audio_transcripts_and_only_scoped_read_tools():
    config = LiveConfig('fixture-secret')
    parsed = types.LiveConnectConfig(**config.setup())
    assert parsed.response_modalities == [types.Modality.AUDIO]
    assert parsed.input_audio_transcription is not None
    assert parsed.output_audio_transcription is not None
    assert [f.name for f in parsed.tools[0].function_declarations] == ['end_call','diagnose_channel_access']
    assert 'fixture-secret' not in repr(config)
    assert 'Malayalam' in parsed.system_instruction
    declaration = config.setup()['tools'][0]['function_declarations'][1]
    assert set(declaration['parameters'])=={'type','properties','required'}


def test_persistent_receive_streams_audio_interrupts_and_closes(monkeypatch):
    class Context:
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
    class Audio(Context):
        def __init__(self):
            self.read = AsyncMock(side_effect=self.wait)
            self.write = AsyncMock()
            self.drain = AsyncMock()
            self.interrupted = 0
        async def wait(self):
            await asyncio.Event().wait()
        def interrupt(self): self.interrupted += 1
    class Session(Context):
        def __init__(self):
            self.turn = 0
            self.send_client_content = AsyncMock()
            self.lookup_received = None
            self.send_tool_response = AsyncMock(side_effect=self.result)
        async def result(self, **kwargs):
            response = kwargs['function_responses'][0]
            if response.name=='diagnose_channel_access':
                self.lookup_received = response.response
        async def receive(self):
            self.turn += 1
            if self.turn == 1:
                yield types.LiveServerMessage(tool_call=types.LiveServerToolCall(function_calls=[
                    types.FunctionCall(id='lookup-1',name='diagnose_channel_access',args={'channel':'Sports'})]))
                while self.lookup_received is None: await asyncio.sleep(0.001)
                yield types.LiveServerMessage(server_content=types.LiveServerContent(
                    interrupted=True,input_transcription=types.Transcription(text='ഹലോ')))
                yield types.LiveServerMessage(server_content=types.LiveServerContent(
                    model_turn=types.Content(parts=[types.Part(inline_data=types.Blob(
                        data=b'\x01\x00'*480,mime_type='audio/pcm;rate=24000'))]),turn_complete=True))
                await asyncio.sleep(0)
            else:
                yield types.LiveServerMessage(tool_call=types.LiveServerToolCall(
                    function_calls=[types.FunctionCall(id='call-1',name='end_call',args={})]))
                yield types.LiveServerMessage(server_content=types.LiveServerContent(turn_complete=True))
    session, audio = Session(), Audio()
    close = AsyncMock()
    account = object()
    lookup = Mock(return_value={'ok':True,'state':'already_entitled'})
    monkeypatch.setattr('telecom_support.gemini_live.channel_execute',lookup)
    monkeypatch.setattr('telecom_support.gemini_live.genai.Client',lambda **kwargs:NS(
        aio=NS(live=NS(connect=lambda **kwargs:session),aclose=close)))
    logged=[]
    asyncio.run(run_gemini_live(NS(channel_account=account),None,config=LiveConfig('fixture'),write=logged.append,
        microphone_factory=lambda:audio,device_factory=lambda:audio))
    assert session.turn == 2
    assert audio.interrupted == 1
    audio.write.assert_awaited()
    assert session.send_tool_response.await_count == 2
    lookup.assert_called_once_with(account,{'channel':'Sports'})
    assert session.lookup_received['state']=='already_entitled'
    close.assert_awaited_once()
    assert any('ഹലോ' in line for line in logged)


def test_runtime_does_not_expose_unknown_or_real_customer(monkeypatch):
    from telecom_support.gemini_live import GeminiRuntime
    monkeypatch.setattr(LiveConfig,'load',classmethod(lambda cls:LiveConfig('fixture')))
    audit = NS(close=Mock(),write=Mock(),client=None)
    monkeypatch.setattr('telecom_support.gemini_live.LiveAudit',lambda _:audit)
    account = object()
    client = Mock()
    monkeypatch.setattr('telecom_support.cli.database_service',lambda:(account,client))
    monkeypatch.setattr('telecom_support.caller.CallerSession',lambda *a,**kw:NS(customer=None))
    app,_,_ = GeminiRuntime().new_call('+910000000001')
    assert app.channel_account is None
    monkeypatch.setattr('telecom_support.caller.CallerSession',Mock(side_effect=RuntimeError('private connection payload')))
    app,_,_ = GeminiRuntime().new_call('+910000000001')
    assert app.channel_account is None
    assert 'private connection payload' not in str(audit.write.call_args_list)
    client.close.assert_called_once()
    monkeypatch.setattr('telecom_support.caller.CallerSession',lambda *a,**kw:NS(customer={'data_type':'REAL'}))
    app,_,_ = GeminiRuntime().new_call('+910000000001')
    assert app.channel_account is None
