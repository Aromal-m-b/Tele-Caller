import asyncio
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from telecom_support.speech_input import (
    MicrophoneSource, SpeechInputError, SpeechSettings, SpokenTurn,
    process_turns, run_microphone)
from voice_support.transcripts import TranscriptBuffer


def result(text, *, final=False, endpoint=False):
    return {"type": "Results", "channel": {"alternatives": [{"transcript": text}]},
            "is_final": final, "speech_final": endpoint}


def test_connected_assistant_speaks_generated_opening_before_any_user_turn():
    from unittest.mock import AsyncMock
    async def run():
        connected, spoken = [], []
        class Socket(FakeSocket):
            async def __aenter__(self):
                connected.append("socket")
                return self
            async def __aiter__(self):
                await greeted.wait()
                if False: yield None
        class Mic(FakeMicrophone):
            async def __aenter__(self):
                connected.append("mic")
                return self
        greeted = asyncio.Event()
        class Speaker:
            epoch, mute_microphone = 0, False
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def speak(self, answer):
                spoken.append(answer)
                greeted.set()
        def opening():
            assert connected == ["socket", "mic"]
            return "Hi, I'm Maya from Sun Direct. How can I help?"
        app = SimpleNamespace(start_conversation=Mock(side_effect=opening), generate=Mock())
        await run_microphone(app, SpeechSettings("fake", audio_output=True),
            speaker_factory=lambda _: Speaker(), microphone_factory=lambda **k: Mic(),
            connect_factory=lambda *a, **k: Socket([]), write=lambda _: None)
        assert len(spoken) == 1 and "Maya" in spoken[0]
        app.start_conversation.assert_called_once()
        app.generate.assert_not_called()
    asyncio.run(run())


def test_transcripts_are_complete_and_processed_once():
    buffer = TranscriptBuffer()
    assert buffer.consume(result("changing interim")) is None
    assert buffer.consume(result("I want Malayalam", final=True)) is None
    assert buffer.consume(result("movies", final=True, endpoint=True)) == "I want Malayalam movies"
    assert buffer.consume({"type": "UtteranceEnd"}) is None
    assert buffer.consume(result("", final=True, endpoint=True)) is None


def test_settings_support_existing_and_reference_key_names(monkeypatch):
    monkeypatch.setattr("telecom_support.speech_input.load_dotenv", lambda *a: None)
    monkeypatch.setenv("DEEPGRAM", "primary-secret")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "fallback-secret")
    settings = SpeechSettings.load()
    assert settings.api_key == "primary-secret" and "primary-secret" not in repr(settings)
    assert "sample_rate=16000" in settings.url and "endpointing=1500" in settings.url
    assert "utterance_end_ms=2000" in settings.url
    monkeypatch.delenv("DEEPGRAM")
    assert SpeechSettings.load().api_key == "fallback-secret"
    monkeypatch.delenv("DEEPGRAM_API_KEY")
    with pytest.raises(SpeechInputError, match="Set DEEPGRAM"):
        SpeechSettings.load()


class FakeMicrophone:
    def __init__(self, **kwargs):
        self.closed = False
    async def __aenter__(self):
        return self
    async def read(self):
        await asyncio.Event().wait()
    async def __aexit__(self, *args):
        self.closed = True


class FakeSocket:
    def __init__(self, events):
        self.events, self.sent, self.closed = events, [], False
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        self.closed = True
    async def send(self, data):
        self.sent.append(data)
    async def __aiter__(self):
        for event in self.events:
            await asyncio.sleep(0)
            yield json.dumps(event)


def test_socket_turns_reach_same_application_in_order_and_release_resources():
    async def run():
        socket = FakeSocket([result("interim"), result("How do I recharge", final=True),
                             result("my account?", final=True, endpoint=True),
                             {"type": "UtteranceEnd"}, result("Thank you", final=True, endpoint=True)])
        microphone = FakeMicrophone()
        calls, output = [], []
        application = SimpleNamespace(generate=lambda text: calls.append(text) or "Helpful answer")
        connect = Mock(return_value=socket)
        await run_microphone(application, SpeechSettings("fake-key"),
            microphone_factory=lambda **k: microphone, connect_factory=connect, write=output.append)
        assert calls == ["How do I recharge my account?", "Thank you"]
        assert socket.closed and microphone.closed
        assert connect.call_args.kwargs["additional_headers"] == {"Authorization": "Token fake-key"}
        assert output.count("A: Helpful answer") == 2
    asyncio.run(run())


def test_processing_is_serial_and_does_not_block_audio_event_loop():
    import threading
    async def run():
        entered, release = threading.Event(), threading.Event()
        active, maximum = [0], [0]
        def generate(text):
            active[0] += 1
            maximum[0] = max(maximum[0], active[0])
            entered.set()
            assert release.wait(timeout=3)
            active[0] -= 1
            return text
        turns = asyncio.Queue()
        for text in ["first", "second"]:
            turns.put_nowait(SpokenTurn(text, 0))
        turns.put_nowait(None)
        task = asyncio.create_task(process_turns(SimpleNamespace(generate=generate), turns, write=lambda _: None))
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(.01)
        assert entered.is_set() and not task.done()
        release.set()
        await task
        assert maximum[0] == 1
    asyncio.run(run())


def test_voice_stop_command_does_not_become_customer_request():
    async def run():
        turns = asyncio.Queue()
        turns.put_nowait(SpokenTurn("Stop listening.", 0))
        application = SimpleNamespace(generate=Mock())
        await process_turns(application, turns, write=lambda _: None)
        application.generate.assert_not_called()
    asyncio.run(run())


def test_spoken_stop_closes_stream_and_microphone_without_backend_request():
    async def run():
        socket = FakeSocket([result("Stop listening.", final=True, endpoint=True)])
        microphone = FakeMicrophone()
        application = SimpleNamespace(generate=Mock())
        await run_microphone(application, SpeechSettings("fake"),
            microphone_factory=lambda **k: microphone, connect_factory=lambda *a, **k: socket,
            write=lambda _: None)
        application.generate.assert_not_called()
        assert socket.closed and microphone.closed
    asyncio.run(run())


def test_cancellation_waits_for_inflight_backend_before_returning():
    import threading
    async def run():
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        def generate(text):
            entered.set()
            release.wait(timeout=3)
            finished.set()
            return "Done"
        turns = asyncio.Queue()
        turns.put_nowait(SpokenTurn("my request", 0))
        task = asyncio.create_task(process_turns(SimpleNamespace(generate=generate), turns, write=lambda _: None))
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(.01)
        assert entered.is_set()
        task.cancel()
        await asyncio.sleep(.01)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set()
    asyncio.run(run())


def test_early_spoken_confirmation_is_not_applied_to_unseen_package():
    async def run():
        service = SimpleNamespace(pending_recharge=None)
        calls, output = [], []
        def generate(text):
            calls.append(text)
            service.pending_recharge = {"example": True}
            return "Package details. Please confirm."
        application = SimpleNamespace(generate=generate, account_agent=SimpleNamespace(service=service))
        turns = asyncio.Queue()
        turns.put_nowait(SpokenTurn("Get my previous recharge", 0))
        turns.put_nowait(SpokenTurn("Yes, please.", 1))
        turns.put_nowait(None)
        await process_turns(application, turns, write=output.append, clock=lambda: 10)
        assert calls == ["Get my previous recharge"]
        assert any("confirm again" in line for line in output)
    asyncio.run(run())


def test_speech_error_releases_socket_and_microphone():
    async def run():
        socket = FakeSocket([{"type": "Error", "description": "do not expose raw provider payload"}])
        microphone = FakeMicrophone()
        with pytest.raises(SpeechInputError, match="transcription error"):
            await run_microphone(SimpleNamespace(generate=Mock()), SpeechSettings("secret"),
                microphone_factory=lambda **k: microphone, connect_factory=lambda *a, **k: socket,
                write=lambda _: None)
        assert socket.closed and microphone.closed
    asyncio.run(run())


def test_microphone_overflow_is_reported_instead_of_dropping_audio():
    async def run():
        source = MicrophoneSource()
        for _ in range(65):
            source._feed(b"pcm", 0)
        with pytest.raises(SpeechInputError, match="overflowed"):
            await source.read()
    asyncio.run(run())


def test_cli_defaults_to_audio_and_text_mode_is_explicit(monkeypatch):
    import telecom_support.cli as cli
    import telecom_support.rag as rag
    import telecom_support.speech_input as speech
    application = SimpleNamespace(generate=Mock(), close=Mock())
    monkeypatch.setattr(cli, "database_service", lambda *a: (None, None))
    monkeypatch.setattr(cli.Settings, "load", lambda: SimpleNamespace(model=cli.Settings.model))
    monkeypatch.setattr(rag, "build_application", lambda *a, **k: application)
    options = []
    def settings(**kwargs):
        options.append(kwargs)
        return SpeechSettings("fake")
    monkeypatch.setattr(speech.SpeechSettings, "load", settings)
    called = []
    async def microphone(app, settings):
        called.append(app)
    menu = Mock()
    monkeypatch.setattr(speech, "run_microphone", microphone)
    monkeypatch.setattr(cli, "run_menu", menu)
    cli.main([])
    assert called == [application]
    assert options[-1]["audio_output"] is True
    menu.assert_not_called()
    cli.main(["--silent"])
    assert options[-1]["audio_output"] is False
    cli.main(["--text"])
    menu.assert_called_once_with(application)
