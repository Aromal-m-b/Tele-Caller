import asyncio
import base64
from types import SimpleNamespace
from unittest.mock import Mock
import xml.etree.ElementTree as ET

import pytest

pytest.importorskip('fastapi')
pytest.importorskip('twilio')
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from twilio.request_validator import RequestValidator

from telecom_support.phone_server import PhoneConfig, CallTickets, create_app
from telecom_support.phone_audio import TwilioAudio, run_phone_call

ACCOUNT, CALL, STREAM = 'AC'+'1'*32, 'CA'+'2'*32, 'MZ'+'3'*32
CALLER = '+910000000001'


def config(): return PhoneConfig('https://phone.example.invalid',ACCOUNT,'fixture-auth-token',frozenset({CALLER}))


def runtime(): return SimpleNamespace(base=None,close=Mock(),prepare=Mock())


def request_call(client, cfg, **changes):
    fields = dict(AccountSid=ACCOUNT,CallSid=CALL,From=CALLER)
    fields.update(changes)
    signature = RequestValidator(cfg.auth_token).compute_signature(cfg.public_url+'/voice',fields)
    return client.post('/voice',data=fields,headers={'X-Twilio-Signature':signature})


def start_packet(token, **changes):
    start = dict(accountSid=ACCOUNT,callSid=CALL,streamSid=STREAM,
        mediaFormat=dict(encoding='audio/x-mulaw',sampleRate=8000,channels=1),
        customParameters=dict(session_token=token))
    start.update(changes)
    return dict(event='start',streamSid=STREAM,start=start)


def test_signed_webhook_returns_bidirectional_stream_and_single_use_binding():
    cfg, rt = config(), runtime()
    app = create_app(config=cfg,runtime=rt)
    with TestClient(app) as client:
        response = request_call(client,cfg)
        assert response.status_code == 200
        xml = ET.fromstring(response.text)
        stream = xml.find('Connect/Stream')
        assert stream.attrib['url'] == cfg.media_url
        assert xml.find('Hangup') is not None
        assert CALLER not in response.text and cfg.auth_token not in response.text
        token = stream.find('Parameter').attrib['value']
        assert app.state.tickets.consume(token,CALL)['caller'] == CALLER
        assert app.state.tickets.consume(token,CALL) is None
    rt.close.assert_called_once()


def test_webhook_rejects_unsigned_wrong_account_and_unapproved_callers():
    cfg = config()
    with TestClient(create_app(config=cfg,runtime=runtime())) as client:
        assert client.post('/voice',data={'From':CALLER}).status_code == 403
        assert request_call(client,cfg,AccountSid='AC'+'4'*32).status_code == 403
        assert request_call(client,cfg,From='+910000000002').status_code == 403
        assert client.post('/voice',json={}).status_code == 415
        assert client.get('/health').json()['knowledge_loaded'] is False


def test_call_capacity_expiry_and_retry_do_not_duplicate_sessions():
    clock = [100]
    tickets = CallTickets(1,clock=lambda:clock[0])
    token = tickets.issue(CALL,CALLER)
    assert tickets.issue(CALL,CALLER) == token
    assert tickets.consume(token,'CA'+'9'*32) is None
    from fastapi import HTTPException
    with pytest.raises(HTTPException): tickets.issue('CA'+'9'*32,CALLER)
    clock[0] += 91
    assert tickets.consume(token,CALL) is None
    assert tickets.issue(CALL,CALLER) != token


def test_websocket_requires_signature_and_matching_signed_call_binding():
    cfg, calls = config(), []
    async def runner(socket,start,prepare): calls.append(start['callSid'])
    app = create_app(config=cfg,runtime=runtime(),phone_runner=runner)
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect('/media-stream'): pass
        response = request_call(client,cfg)
        token = ET.fromstring(response.text).find('Connect/Stream/Parameter').attrib['value']
        signature = RequestValidator(cfg.auth_token).compute_signature(cfg.media_url,{})
        with client.websocket_connect('/media-stream',headers={'X-Twilio-Signature':signature}) as socket:
            socket.send_json(dict(event='connected',protocol='Call',version='1.0.0'))
            socket.send_json(start_packet(token))
            with pytest.raises(WebSocketDisconnect): socket.receive_json()
        assert calls == [CALL]
        assert not app.state.tickets.active
        # A replay of the same start token never reaches the agent.
        with client.websocket_connect('/media-stream',headers={'X-Twilio-Signature':signature}) as socket:
            socket.send_json(dict(event='connected',protocol='Call'))
            socket.send_json(start_packet(token))
            with pytest.raises(WebSocketDisconnect): socket.receive_json()
        assert calls == [CALL]


def test_invalid_stream_cannot_release_an_active_call_reservation():
    cfg, app = config(), create_app(config=config(),runtime=runtime())
    app.state.tickets.active.add(CALL)
    signature = RequestValidator(cfg.auth_token).compute_signature(cfg.media_url,{})
    with TestClient(app) as client:
        with client.websocket_connect('/media-stream',headers={'X-Twilio-Signature':signature}) as socket:
            socket.send_json(dict(event='connected',protocol='Call'))
            socket.send_json(start_packet('invalid-token'))
            with pytest.raises(WebSocketDisconnect): socket.receive_json()
        assert CALL in app.state.tickets.active


def test_phone_audio_converts_frames_and_waits_for_actual_playback_mark():
    async def run():
        sent = []
        async def send_json(value): sent.append(value)
        audio = TwilioAudio(SimpleNamespace(send_json=send_json),STREAM)
        audio.accept_media(base64.b64encode(b'\xff'*160).decode())
        pcm = await audio.read()
        assert 630 <= len(pcm) <= 640 and len(pcm)%2 == 0
        await audio.write(b'\x00\x00'*480)
        assert sent[0]['event'] == 'media'
        assert len(base64.b64decode(sent[0]['media']['payload'])) == 160
        drain = asyncio.create_task(audio.drain())
        await asyncio.sleep(0)
        assert not drain.done()
        audio.acknowledge(sent[-1]['mark']['name'])
        await drain
        audio.interrupt()
        await audio.clear_pending
        assert sent[-1]['event'] == 'clear'
        await audio.close()
    asyncio.run(run())


def test_clear_does_not_arm_consent_for_discarded_audio():
    from telecom_support.deepgram_agent import AgentPlayback
    async def run():
        sent = []
        async def send_json(value): sent.append(value)
        audio = TwilioAudio(SimpleNamespace(send_json=send_json),STREAM)
        done = Mock()
        playback = AgentPlayback(audio,lambda:None,done)
        playback.put(b'\0'*960)
        playback.put(None)
        task = asyncio.create_task(playback.run())
        while not any(item['event']=='mark' for item in sent): await asyncio.sleep(.001)
        playback.interrupt()
        await asyncio.sleep(.01)
        done.assert_not_called()
        task.cancel()
        await asyncio.gather(task,return_exceptions=True)
        await audio.close()
    asyncio.run(run())


def test_phone_runner_uses_network_audio_and_closes_audit_after_stop():
    async def run():
        incoming, sent = asyncio.Queue(), []
        async def receive_json(): return await incoming.get()
        async def send_json(packet):
            sent.append(packet)
            if packet['event'] == 'mark':
                await incoming.put(dict(event='mark',streamSid=STREAM,mark=packet['mark']))
        audit = SimpleNamespace(write=Mock(),close=Mock())
        async def prepare(): return SimpleNamespace(audit=audit),None,None
        async def runner(app,speech,**kwargs):
            async with kwargs['device_factory']() as device:
                async with kwargs['microphone_factory']() as source:
                    frame = await source.read()
                    assert frame
                    await device.write(b'\0'*960)
                    await device.drain()
        await incoming.put(dict(event='media',streamSid=STREAM,media=dict(payload=base64.b64encode(b'\xff'*160).decode())))
        await asyncio.wait_for(run_phone_call(SimpleNamespace(receive_json=receive_json,send_json=send_json),
            start_packet('fixture')['start'],prepare,runner=runner),2)
        assert [packet['event'] for packet in sent] == ['media','mark']
        audit.close.assert_called_once()
    asyncio.run(run())


def test_full_agent_bridge_drains_goodbye_then_ends_without_a_microphone():
    from functools import partial
    import json
    from telecom_support.deepgram_agent import run_voice_agent, AgentConfig
    from telecom_support.speech_input import SpeechSettings
    async def run():
        inbound, provider, sent = asyncio.Queue(), asyncio.Queue(), []
        await provider.put(json.dumps({'type':'Welcome'}))
        await provider.put(json.dumps({'type':'SettingsApplied'}))
        class Provider:
            async def __aenter__(self): return self
            async def __aexit__(self,*_): pass
            async def recv(self): return await provider.get()
            def __aiter__(self): return self
            async def __anext__(self): return await provider.get()
            async def send(self,message):
                if isinstance(message,bytes): return
                packet = json.loads(message)
                if packet['type'] == 'InjectUserMessage':
                    await provider.put(json.dumps(dict(type='ConversationText',role='assistant',content="Hi, I'm Maya from Sun Direct.")))
                    await provider.put(b'\0'*960)
                    await provider.put(json.dumps(dict(type='AgentAudioDone')))
        async def receive_json(): return await inbound.get()
        async def send_json(packet):
            sent.append(packet)
            if packet['event'] == 'mark':
                await inbound.put(dict(event='mark',streamSid=STREAM,mark=packet['mark']))
                if sum(item['event']=='mark' for item in sent) == 1:
                    for event in [dict(type='UserStartedSpeaking'),
                        dict(type='ConversationText',role='user',content='Please end the call.'),
                        dict(type='ConversationText',role='assistant',content='Thank you for calling Sun Direct. Goodbye!')]:
                        await provider.put(json.dumps(event))
                    await provider.put(b'\0'*960)
                    await provider.put(json.dumps(dict(type='AgentAudioDone')))
        audit = Mock()
        app = SimpleNamespace(audit=audit,customer_services=None)
        async def prepare(): return app,SpeechSettings('fake',audio_output=True,idle_timeout_seconds=0),AgentConfig()
        await inbound.put(dict(event='media',streamSid=STREAM,media=dict(payload=base64.b64encode(b'\xff'*160).decode())))
        await asyncio.wait_for(run_phone_call(SimpleNamespace(receive_json=receive_json,send_json=send_json),
            start_packet('fixture')['start'],prepare,
            runner=partial(run_voice_agent,connect_factory=lambda *a,**kw:Provider())),3)
        assert sum(item['event']=='mark' for item in sent) == 2
        assert any(item['event']=='clear' for item in sent)
        audit.stt_connected.assert_called_once()
        audit.close.assert_called_once()
    asyncio.run(run())
