import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from google.genai import types
from voice_support.agent import TextAgent
from voice_support.deepgram import TranscriptBuffer
from voice_support.orchestrator import VoiceOrchestrator
from voice_support.settings import Settings
from voice_support.deepgram import DeepgramVoice
import httpx


def result(text="", final=False, endpoint=False):
    return dict(type="Results", is_final=final, speech_final=endpoint,
                channel={"alternatives": [{"transcript": text}]})


def test_segments_wait_for_endpoint_and_flush_once():
    buffer = TranscriptBuffer()
    assert buffer.consume(result("interim")) is None
    assert buffer.consume(result("my channel", final=True)) is None
    assert buffer.consume(result("is missing", final=True, endpoint=True)) == "my channel is missing"
    assert buffer.consume({"type": "UtteranceEnd"}) is None
    assert buffer.consume(result("hello", final=True)) is None
    assert buffer.consume({"type": "UtteranceEnd"}) == "hello"


def response(parts):
    return types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role="model", parts=parts))])


def test_tool_result_is_sent_back_and_context_is_bounded():
    async def run():
        search = AsyncMock(return_value="Verified plan costs 100")
        model = AsyncMock(side_effect=[
            response([types.Part.from_function_call(name="search_company_knowledge_base", args={"query": "plan"})]),
            response([types.Part.from_text(text="The plan costs 100.")])])
        agent = TextAgent(SimpleNamespace(models=SimpleNamespace(generate_content=model)), "text-model", search)
        assert await agent.reply("plan price?") == "The plan costs 100."
        search.assert_awaited_once_with("plan")
        sent = model.call_args.kwargs["contents"]
        assert sent[-1].parts[0].function_response.response["result"] == "Verified plan costs 100"
        model.side_effect = None
        model.return_value = response([types.Part.from_text(text="Hello")])
        for _ in range(10):
            await agent.reply("hello")
        assert len(agent.history) == 12
    asyncio.run(run())


def test_barge_in_cancels_generation_and_clears_playback():
    async def run():
        started = asyncio.Event()
        async def reply(text):
            started.set()
            await asyncio.Event().wait()
        agent = SimpleNamespace(reply=reply)
        audio = SimpleNamespace(clear_playback_buffer=lambda: cleared.append(True))
        voice = SimpleNamespace(speak=AsyncMock())
        cleared = []
        runner = VoiceOrchestrator(audio, agent, voice)
        task = asyncio.create_task(runner.respond("hello"))
        runner.response_task = task
        await started.wait()
        await runner.interrupt()
        assert task.cancelled()
        assert cleared == [True]
        voice.speak.assert_not_awaited()
    asyncio.run(run())


def test_tts_requests_raw_pcm_and_preserves_audio():
    async def run():
        pcm = b"\x01\x02" * 1001
        def handler(request):
            assert request.url.params["container"] == "none"
            assert request.url.params["sample_rate"] == "24000"
            assert request.headers["Authorization"] == "Token fake-deepgram"
            return httpx.Response(200, content=pcm)
        voice = DeepgramVoice(Settings("fake-gemini", "fake-deepgram"))
        await voice.http.aclose()
        voice.http = httpx.AsyncClient(transport=httpx.MockTransport(handler), headers=voice.headers)
        audio = SimpleNamespace(audio_queue=asyncio.Queue(), generation=[3])
        try:
            await voice.speak("Hello", audio)
            chunks = []
            while not audio.audio_queue.empty():
                generation, chunk = audio.audio_queue.get_nowait()
                assert generation == 3 and len(chunk) % 2 == 0
                chunks.append(chunk)
            assert b"".join(chunks) == pcm
        finally:
            await voice.close()
    asyncio.run(run())
