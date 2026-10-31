import asyncio
import json
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import numpy as np
import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from telecom_support.barge_in import BargeInDetector
from telecom_support.guardrails import SupportScopeGuard
from telecom_support.rag import SupportRAG
from telecom_support.speech_input import SpeechSettings
from telecom_support.speech_output import DeepgramSpeechOutput
from tests.test_speech_output import FakeSpeakerDevice


@pytest.mark.parametrize("query", ["Hello? Can you hear me?", "Okay. Can you hear me now?",
    "Are you there?", "Is my microphone working?", "Hey, are you listening?"])
def test_audio_checks_use_model_without_retrieval(query):
    retriever = SimpleNamespace(invoke=Mock(side_effect=AssertionError("No retrieval")))
    app = SupportRAG(retriever, FakeListChatModel(responses=['{"intent":"reply","text":"Yes, I can hear you. Go ahead."}']))
    assert app.generate(query) == "Yes, I can hear you. Go ahead."
    assert SupportScopeGuard().evaluate(query).allowed
    retriever.invoke.assert_not_called()
    assert not SupportScopeGuard().evaluate(query + " Generate Python code.").allowed


def test_sustained_speech_triggers_with_preroll_but_silence_and_clicks_do_not():
    detector = BargeInDetector(vad=SimpleNamespace(is_speech=lambda *_: True))
    frame = (np.ones(320) * 1000).astype("<i2").tobytes()
    assert detector.feed(frame) is None  # A click cannot interrupt.
    assert detector.feed(bytes(640)) is None
    for _ in range(5):
        assert detector.feed(frame) is None
    buffered = detector.feed(frame)
    assert buffered.endswith(frame * 6)
    assert not detector.voiced


def test_direct_playback_echo_is_rejected_even_when_loud():
    detector = BargeInDetector(vad=SimpleNamespace(is_speech=lambda *_: True))
    rng = np.random.default_rng(42)
    reference = rng.integers(-10000, 10000, 16000).astype(np.float64)
    detector.reference = reference
    for pos in range(0, 6400, 320):
        echo = (reference[pos:pos + 320] * .7).astype("<i2").tobytes()
        assert detector.feed(echo) is None


def test_interrupt_cancels_network_and_remaining_audio_without_cancelling_session():
    async def run():
        started = asyncio.Event()
        class Device(FakeSpeakerDevice):
            async def write(self, data):
                self.chunks.append(data)
                started.set()
                await asyncio.sleep(.01)
        device = Device()
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"\x01\x00" * 240000)))
        async with DeepgramSpeechOutput(SpeechSettings("fake"), device_factory=lambda **_: device, client=client) as output:
            playback = asyncio.create_task(output.speak("A long public reply."))
            await started.wait()
            output.interrupt()
            await asyncio.wait_for(playback, 1)
            assert output.interrupted and not output.speaking and not output.mute_microphone
            assert not device.drained and len(device.chunks) < 5
            assert not asyncio.current_task().cancelled()
            # The next answer must start normally, not inherit cancellation.
            client._transport = httpx.MockTransport(lambda _: httpx.Response(200, content=b"\x01\x00" * 480))
            await output.speak("Go ahead.")
            assert device.drained and not output.interrupted
        assert device.closed and client.is_closed
    asyncio.run(run())


def test_detected_interruption_forwards_first_words_to_stt():
    from telecom_support.speech_input import run_microphone
    async def run():
        done = asyncio.Event()
        sent = []
        class Output:
            mute_microphone, speaking, epoch = True, True, 1
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            def observe_microphone(self, data):
                self.mute_microphone = self.speaking = False
                return b"first words" + data
        class Mic:
            count = 0
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def read(self):
                self.count += 1
                return b"current frame" if self.count == 1 else b""
        class Socket:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def send(self, data):
                sent.append(data)
                if isinstance(data, str) and json.loads(data)["type"] == "CloseStream": done.set()
            async def __aiter__(self):
                await done.wait()
                if False: yield None
        await run_microphone(SimpleNamespace(generate=lambda _: "unused"), SpeechSettings("fake", audio_output=True),
            speaker_factory=lambda _: Output(), microphone_factory=lambda **_: Mic(),
            connect_factory=lambda *a, **k: Socket(), write=lambda _: None)
        assert sent[0] == b"first wordscurrent frame"
    asyncio.run(run())


def test_session_cancellation_propagates_during_playback():
    async def run():
        started = asyncio.Event()
        class Device(FakeSpeakerDevice):
            async def write(self, data):
                started.set()
                await asyncio.Event().wait()
        device = Device()
        client = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda _: httpx.Response(200, content=b"\x01\x00" * 2400)))
        async with DeepgramSpeechOutput(SpeechSettings("fake"), device_factory=lambda **_: device, client=client) as speaker:
            playback = asyncio.create_task(speaker.speak("Public greeting."))
            await started.wait()
            playback.cancel()
            with pytest.raises(asyncio.CancelledError):
                await playback
            assert not speaker.speaking and speaker._playback_task is None
        assert device.closed and client.is_closed
    asyncio.run(run())


@pytest.mark.parametrize("text,confidence,expected", [
    ("", .99, False), (" ", .99, False), ("huh", .99, False),
    ("Thank you", .2, False), ("How can I help you today", .99, False),
    ("Wait I have a question", .95, True), ("stop", .99, True),
    ("Hello", .99, True), ("I need to recharge", .90, True),
])
def test_interruption_needs_confident_words_and_rejects_playback_echo(text, confidence, expected):
    output = DeepgramSpeechOutput(SpeechSettings("fake"))
    output.current_text = "I'm Maya from Tech Five. How can I help you today?"
    event = {"type": "Results", "channel": {"alternatives": [
        {"transcript": text, "confidence": confidence}]}}
    assert output.confirms_interruption(event) is expected
    assert not output.confirms_interruption({"type": "SpeechStarted"})


def test_local_vad_candidate_cannot_cancel_playback_on_its_own():
    output = DeepgramSpeechOutput(SpeechSettings("fake"))
    output.speaking = True
    output.detector = SimpleNamespace(feed=lambda _: b"breath candidate")
    output.interrupt = Mock(side_effect=AssertionError("VAD must not stop playback"))
    assert output.observe_microphone(b"microphone activity") == b"breath candidate"
    assert output.speaking
    output.interrupt.assert_not_called()


def test_breath_candidate_keeps_playing_until_stt_confirms_customer_words():
    from telecom_support.speech_input import run_microphone
    from tests.test_speech_input import result
    async def run():
        candidate = asyncio.Event()
        interrupted = asyncio.Event()
        calls = []
        class Output:
            epoch, speaking, mute_microphone = 0, False, False
            current_text = "I'm Maya from Tech Five. How can I help you today?"
            confirms_interruption = DeepgramSpeechOutput.confirms_interruption
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def speak(self, text):
                if not self.epoch:
                    self.epoch = 1
                    self.speaking = self.mute_microphone = True
                    await interrupted.wait()
            def observe_microphone(self, data): return b"candidate audio"
            def interrupt(self):
                self.speaking = self.mute_microphone = False
                interrupted.set()
        output = Output()
        class Mic:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def read(self):
                while not output.speaking:
                    await asyncio.sleep(0)
                if not candidate.is_set(): return b"breath"
                await asyncio.Event().wait()
        class Socket:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def send(self, data):
                if data == b"candidate audio": candidate.set()
            async def __aiter__(self):
                await candidate.wait()
                yield json.dumps({"type": "SpeechStarted"})
                yield json.dumps(result("", final=True, endpoint=True))
                assert not interrupted.is_set() and output.speaking
                echo = result("How can I help you today", final=True, endpoint=True)
                echo["channel"]["alternatives"][0]["confidence"] = .99
                yield json.dumps(echo)
                assert not interrupted.is_set() and output.speaking
                speech = result("Wait I need help", final=True, endpoint=True)
                speech["channel"]["alternatives"][0]["confidence"] = .99
                yield json.dumps(speech)
                assert interrupted.is_set()
        application = SimpleNamespace(start_conversation=lambda: output.current_text,
            generate=lambda text: calls.append(text) or "Go ahead.")
        await asyncio.wait_for(run_microphone(application, SpeechSettings("fake", audio_output=True),
            speaker_factory=lambda _: output, microphone_factory=lambda **_: Mic(),
            connect_factory=lambda *a, **k: Socket(), write=lambda _: None), 3)
        assert calls == ["Wait I need help"]
    asyncio.run(run())
