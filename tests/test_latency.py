import asyncio
from types import SimpleNamespace

from telecom_support.latency import AudioTimeline, TurnTiming, active_turn, measure, mark


def test_timer_reports_first_audio_once_and_separates_speaking_from_waiting():
    now, output, audit = [10.0], [], []
    timer = TurnTiming(2, 1, 8, last_speech_at=7, clock=lambda: now[0], write=output.append,
                       audit=SimpleNamespace(event=audit.append))
    token = active_turn.set(timer)
    try:
        with measure('retrieval'):
            now[0] += .2
        mark('llm_start')
        now[0] += .4
        mark('llm_first_token')
        mark('tts_start')
        now[0] += .3
        mark('first_pcm')
        timer.first_audio()
        now[0] += 5
        timer.first_audio()
        timer.finish()
    finally:
        active_turn.reset(token)
    assert len(output) == 1 and audit == output
    assert 'final transcript->audio 2.900s' in output[0]
    assert 'estimated speech end->audio 3.900s' in output[0]
    assert 'RAG total 200.0ms' in output[0]
    assert 'LLM first token 400.0ms' in output[0]
    assert 'TTS first PCM 300.0ms' in output[0]


def test_audio_timeline_respects_muted_wall_time_gap():
    timeline = AudioTimeline()
    timeline.sent(32000, 10)
    timeline.sent(32000, 20)
    assert timeline.wall_time(.5) == 9.5
    assert timeline.wall_time(1.5) == 19.5
    assert timeline.wall_time(20) is None
    assert timeline.wall_time(None) is None


def test_turn_context_propagates_to_backend_thread():
    async def run():
        timer = TurnTiming(1, 0, 0, write=lambda _: None)
        token = active_turn.set(timer)
        try:
            await asyncio.to_thread(mark, 'worker_started')
            assert 'worker_started' in timer.points
        finally:
            active_turn.reset(token)
        assert active_turn.get() is None
    asyncio.run(run())


def test_missing_audio_report_does_not_invent_latency():
    output = []
    timer = TurnTiming(1, 0, 1, write=output.append)
    timer.finish()
    assert 'no reply audio was written' in output[0]


def test_failed_timer_console_does_not_break_audio():
    def failed(_): raise UnicodeEncodeError('ascii', 'x', 0, 1, 'test')
    timer = TurnTiming(1, 0, 0, write=failed)
    timer.first_audio()
    assert timer.reported


def test_first_pcm_is_reported_on_device_write_not_after_full_synthesis():
    from telecom_support.speech_input import SpeechSettings
    from telecom_support.speech_output import DeepgramSpeechOutput
    from tests.test_speech_output import FakeSpeakerDevice
    import httpx
    async def run():
        output = []
        device = FakeSpeakerDevice()
        timer = TurnTiming(1, 0, 0, write=output.append)
        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'\x00\x01' * 480
                assert len(output) == 1 and len(device.chunks) == 1
                yield b'\x00\x01' * 480
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=Stream())))
        token = active_turn.set(timer)
        try:
            async with DeepgramSpeechOutput(SpeechSettings('fake'), client=client, device_factory=lambda **_: device) as speaker:
                await speaker.speak('A spoken response.')
            assert len(output) == 1 and len(device.chunks) == 2
        finally:
            active_turn.reset(token)
    asyncio.run(run())
