import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

from google.genai import types
from telecom_support.gemini_live import LiveConfig, run_gemini_live


def test_setup_uses_audio_transcripts_and_no_customer_tools():
    config = LiveConfig('fixture-secret')
    parsed = types.LiveConnectConfig(**config.setup())
    assert parsed.response_modalities == [types.Modality.AUDIO]
    assert parsed.input_audio_transcription is not None
    assert parsed.output_audio_transcription is not None
    assert [f.name for f in parsed.tools[0].function_declarations] == ['end_call']
    assert 'fixture-secret' not in repr(config)
    assert 'Malayalam' in parsed.system_instruction


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
            self.send_tool_response = AsyncMock()
        async def receive(self):
            self.turn += 1
            if self.turn == 1:
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
    monkeypatch.setattr('telecom_support.gemini_live.genai.Client',lambda **kwargs:NS(
        aio=NS(live=NS(connect=lambda **kwargs:session),aclose=close)))
    logged=[]
    asyncio.run(run_gemini_live(NS(),None,config=LiveConfig('fixture'),write=logged.append,
        microphone_factory=lambda:audio,device_factory=lambda:audio))
    assert session.turn == 2
    assert audio.interrupted == 1
    audio.write.assert_awaited()
    session.send_tool_response.assert_awaited_once()
    close.assert_awaited_once()
    assert any('ഹലോ' in line for line in logged)
