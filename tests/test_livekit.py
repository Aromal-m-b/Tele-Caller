import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock

import pytest
pytest.importorskip('livekit.rtc')
pytest.importorskip('livekit.api')
from fastapi.testclient import TestClient
import jwt

from telecom_support.livekit_server import WebRTCConfig,create_app
from telecom_support.livekit_audio import LiveKitAudio


def config():
    return WebRTCConfig('wss://fixture.example.invalid','fixture-api-key','s'*40,
        'fixture-private-access-code-1234','+910000000001')


def runtime(): return SimpleNamespace(prepare=AsyncMock(),close=Mock())


def test_tokens_are_short_lived_scoped_microphone_only_and_anonymous():
    cfg=config()
    raw=cfg.token('fixture-room','fixture-caller')
    claims=jwt.decode(raw,cfg.api_secret,algorithms=['HS256'],issuer=cfg.api_key)
    assert claims['video']['room']=='fixture-room'
    assert claims['sub']=='fixture-caller'
    assert claims['video']['canPublishSources']==['microphone']
    assert claims['video']['canPublishData'] is False
    assert not claims['video'].get('roomAdmin')
    assert claims['exp']-claims['nbf']<=120
    assert cfg.caller_phone not in str(claims)
    assert cfg.api_secret not in repr(cfg)


def test_browser_api_requires_access_and_cleans_up_isolated_room():
    cfg,rt,deleted=config(),runtime(),AsyncMock()
    async def runner(url,token,caller,prepare,stop,update):
        update('connected')
        await stop.wait()
    app=create_app(config=cfg,runtime=rt,runner=runner,delete_room=deleted)
    headers={'X-Call-Access':cfg.access_code}
    with TestClient(app) as client:
        assert client.get('/').status_code==200
        assert cfg.api_secret not in client.get('/').text
        assert client.get('/health').json()['backend']=='deepgram-agent'
        assert client.post('/api/sessions').status_code==401
        assert client.post('/api/sessions',headers={'X-Call-Access':'wrong'}).status_code==401
        response=client.post('/api/sessions',headers=headers)
        assert response.status_code==200
        body=response.json()
        assert set(body)=={'session_id','url','token'}
        assert cfg.caller_phone not in str(body)
        assert cfg.api_secret not in str(body)
        assert response.headers['cache-control']=='no-store'
        assert client.post('/api/sessions',headers=headers).status_code==409
        sid=body['session_id']
        assert client.get('/api/sessions/'+sid).status_code==401
        assert client.get('/api/sessions/'+sid,headers=headers).status_code==200
        assert client.delete('/api/sessions/'+sid,headers=headers).status_code==200
        assert client.get('/api/sessions/'+sid,headers=headers).json()['status']=='ended'
        deleted.assert_awaited_once()
    rt.close.assert_called_once()


@pytest.mark.parametrize('changes',[{'url':'https://example.invalid'},{'api_secret':''},
    {'access_code':'short'},{'caller_phone':''},{'max_calls':0}])
def test_invalid_webrtc_configuration_is_rejected(changes):
    with pytest.raises(ValueError): replace(config(),**changes)


def test_livekit_audio_preserves_agent_pcm_and_clears_queued_playback():
    async def run():
        source=SimpleNamespace(capture_frame=AsyncMock(),wait_for_playout=AsyncMock(),clear_queue=Mock())
        audio=LiveKitAudio(source)
        pcm=b'\0'*960
        await audio.write(pcm)
        frame=source.capture_frame.call_args.args[0]
        assert frame.sample_rate==24000 and frame.num_channels==1
        assert frame.samples_per_channel==480 and bytes(frame.data)==pcm
        await audio.drain()
        source.wait_for_playout.assert_awaited_once()
        audio.interrupt()
        source.clear_queue.assert_called_once()
    asyncio.run(run())


def test_livekit_input_is_resampled_to_agent_rate_and_bounded(monkeypatch):
    from telecom_support import livekit_audio
    class Stream:
        def __init__(self,*args,**kwargs):
            assert kwargs['sample_rate']==16000 and kwargs['num_channels']==1
            assert kwargs['capacity']==100
            self.aclose=AsyncMock()
        def __aiter__(self): return self
        async def __anext__(self):
            if getattr(self,'sent',False): raise StopAsyncIteration
            self.sent=True
            return SimpleNamespace(frame=SimpleNamespace(data=b'\0'*640))
    monkeypatch.setattr(livekit_audio.rtc,'AudioStream',Stream)
    async def run():
        audio=LiveKitAudio(SimpleNamespace())
        read=asyncio.create_task(audio.read())
        await asyncio.sleep(0)
        await audio.ingest(object())
        assert await read==b'\0'*640
        for _ in range(250): audio.queue.put_nowait(b'frame')
        with pytest.raises(RuntimeError,match='overflowed'): await audio.ingest(object())
    asyncio.run(run())


def test_startup_frames_do_not_fill_queue_before_agent_is_listening(monkeypatch):
    from telecom_support import livekit_audio
    class StartupStream:
        def __init__(self,*args,**kwargs): self.left=600; self.aclose=AsyncMock()
        def __aiter__(self): return self
        async def __anext__(self):
            if not self.left: raise StopAsyncIteration
            self.left-=1
            return SimpleNamespace(frame=SimpleNamespace(data=b'\0'*640))
    monkeypatch.setattr(livekit_audio.rtc,'AudioStream',StartupStream)
    async def run():
        audio=LiveKitAudio(SimpleNamespace())
        await audio.ingest(object())
        assert audio.queue.empty()
        assert not audio.listening
    asyncio.run(run())


def test_shutdown_ends_active_session_and_deletes_room():
    deleted=AsyncMock()
    async def runner(url,token,caller,prepare,stop,update): await stop.wait()
    app=create_app(config=config(),runtime=runtime(),runner=runner,delete_room=deleted)
    with TestClient(app) as client:
        client.post('/api/sessions',headers={'X-Call-Access':config().access_code})
    deleted.assert_awaited_once()


def test_numeric_dial_code_is_private_and_original_access_code_still_works():
    cfg=replace(config(),dial_code='12345678')
    assert cfg.dial_code not in repr(cfg)
    async def runner(url,token,caller,prepare,stop,update): await stop.wait()
    app=create_app(config=cfg,runtime=runtime(),runner=runner,delete_room=AsyncMock())
    with TestClient(app) as client:
        response=client.post('/api/sessions',headers={'X-Call-Access':cfg.dial_code})
        assert response.status_code==200
        assert cfg.dial_code not in response.text
        sid=response.json()['session_id']
        assert client.get('/api/sessions/'+sid,headers={'X-Call-Access':cfg.access_code}).status_code==200
        assert client.delete('/api/sessions/'+sid,headers={'X-Call-Access':cfg.access_code}).status_code==200


@pytest.mark.parametrize('code',['1234','1234567a','１２３４５６７８','1234567890123'])
def test_invalid_numeric_dial_codes_are_rejected(code):
    with pytest.raises(ValueError): replace(config(),dial_code=code)


def test_dial_code_guessing_is_rate_limited():
    cfg=config()
    app=create_app(config=cfg,runtime=runtime())
    with TestClient(app) as client:
        for _ in range(5):
            assert client.get('/api/sessions/unknown',headers={'X-Call-Access':'wrong'}).status_code==401
        assert client.get('/api/sessions/unknown',headers={'X-Call-Access':cfg.access_code}).status_code==429
        app.state.auth_failures.clear()
        assert client.get('/api/sessions/unknown',headers={'X-Call-Access':cfg.access_code}).status_code==404
