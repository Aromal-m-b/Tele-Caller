import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from telecom_support.speech_input import SpeechSettings, SpokenTurn, process_turns
from telecom_support.speech_output import DeepgramSpeechOutput, SpeechOutputError, speech_text, text_chunks
from telecom_support.tool_agent import speech_safe_result


class FakeSpeakerDevice:
    def __init__(self, **kwargs):
        self.chunks, self.closed, self.drained = [], False, False
    async def __aenter__(self):
        return self
    async def write(self, data):
        self.chunks.append(data)
    async def drain(self):
        self.drained = True
    async def __aexit__(self, *args):
        self.closed = True


def test_aura_socket_reuses_connection_and_flushes_each_sentence():
    from telecom_support.tts_transport import AuraSocket
    async def run():
        class Socket:
            def __init__(self): self.messages, self.sent, self.closed = [], [], False
            async def send(self, text):
                message = json.loads(text)
                self.sent.append(message)
                if message['type'] == 'Flush':
                    self.messages += [b'\x00\x01' * 480, json.dumps({'type': 'Flushed'})]
            async def recv(self): return self.messages.pop(0)
            async def close(self): self.closed = True
        socket, connections, accepted = Socket(), [], []
        async def connect(url, **kwargs):
            connections.append(url)
            assert 'aura-2-andromeda-en' in url
            assert kwargs['additional_headers']['Authorization'] == 'Token fake'
            return socket
        transport = AuraSocket(SpeechSettings('fake'), connect)
        await transport.open()
        for text in ['First sentence.', 'Second sentence.']:
            assert b''.join([piece async for piece in transport.audio(text, accepted.append)])
        assert len(connections) == 1
        assert len(accepted) == 2
        assert [m['type'] for m in socket.sent] == ['Speak', 'Flush', 'Speak', 'Flush']
        await transport.close()
        assert socket.closed
    asyncio.run(run())


def test_aura_interruption_clears_and_closes_socket_before_next_turn():
    from telecom_support.tts_transport import AuraSocket
    async def run():
        class Socket:
            closed = False
            sent = []
            async def send(self, text): self.sent.append(json.loads(text)['type'])
            async def recv(self): return b'\x00\x01' * 480
            async def close(self): self.closed = True
        socket = Socket()
        transport = AuraSocket(SpeechSettings('fake'))
        transport.socket = socket
        stream = transport.audio('Interrupted reply.')
        await stream.__anext__()
        await stream.aclose()
        assert 'Clear' in socket.sent and socket.closed
        assert transport.socket is None
    asyncio.run(run())


def test_socket_failure_before_pcm_falls_back_to_http():
    async def run():
        class Transport:
            closed = False
            async def audio(self, text, accepted=None):
                raise ConnectionError('private-provider-error')
                yield b''
            async def close(self): self.closed = True
        transport = Transport()
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b'\x00\x01'*480)))
        speaker = DeepgramSpeechOutput(SpeechSettings('fake'), client=client, device_factory=FakeSpeakerDevice)
        async with speaker:
            speaker.socket_transport = transport
            await speaker.speak('Public reply.')
            assert transport.closed and speaker.device.chunks
            assert speaker.socket_transport is None
    asyncio.run(run())


def test_socket_failure_after_pcm_does_not_repeat_reply_via_http():
    async def run():
        class Transport:
            async def audio(self, text, accepted=None):
                yield b'\x00\x01'*480
                raise ConnectionError('private-provider-error')
            async def close(self): pass
        def unexpected(_): raise AssertionError('Must not replay audio')
        client = httpx.AsyncClient(transport=httpx.MockTransport(unexpected))
        speaker = DeepgramSpeechOutput(SpeechSettings('fake'), client=client, device_factory=FakeSpeakerDevice)
        async with speaker:
            speaker.socket_transport = Transport()
            with pytest.raises(SpeechOutputError):
                await speaker.speak('Public reply.')
            assert len(speaker.device.chunks) == 1
    asyncio.run(run())


def test_natural_speech_format_preserves_facts_and_caveats():
    text = "## Options\n* **Family**: INR 259.50/month.\n* Expires 2026-11-11.\nVisit https://www.sundirect.in/\nPrices are illustrative, not official."
    spoken = speech_text(text)
    assert "259 rupees and 50 paise per month" in spoken
    assert "11 November 2026" in spoken
    assert "Sun Direct dot in" in spoken
    assert "illustrative, not official" in spoken
    assert "**" not in spoken and "##" not in spoken


def test_long_text_is_split_without_dropping_words():
    text = "A complete sentence. " * 250
    chunks = list(text_chunks(text.strip()))
    assert all(len(chunk) <= 1500 for chunk in chunks)
    assert " ".join(chunks) == text.strip()


def test_female_voice_streams_all_frame_aligned_audio_and_drains():
    async def run():
        pcm = b"\x01\x02" * 5001
        device = FakeSpeakerDevice()
        now = [100]
        speaker = None
        def handler(request):
            assert speaker.speaking and speaker.mute_microphone
            assert request.url.params["model"] == "aura-2-andromeda-en"
            assert request.url.params["container"] == "none"
            assert request.url.params["sample_rate"] == "24000"
            assert json.loads(request.content)["text"] == "Your package costs 149 rupees."
            return httpx.Response(200, content=pcm)
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        speaker = DeepgramSpeechOutput(SpeechSettings("fake"), device_factory=lambda **k: device,
                                       client=client, clock=lambda: now[0])
        async with speaker:
            await speaker.speak("Your package costs INR 149.00.")
            assert b"".join(device.chunks) == pcm
            assert all(len(chunk) % 2 == 0 for chunk in device.chunks)
            assert device.drained and speaker.epoch == 1 and not speaker.speaking
            assert speaker.mute_microphone
            now[0] += .3
            assert not speaker.mute_microphone
        assert device.closed and client.is_closed
    asyncio.run(run())


def test_provider_error_does_not_leak_raw_body_and_releases_resources():
    async def run():
        device = FakeSpeakerDevice()
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(403, text="private-provider-payload")))
        speaker = DeepgramSpeechOutput(SpeechSettings("secret"), device_factory=lambda **k: device, client=client)
        with pytest.raises(SpeechOutputError) as error:
            async with speaker:
                await speaker.speak("A public support answer.")
        assert "private-provider-payload" not in str(error.value)
        assert not speaker.speaking and device.closed
    asyncio.run(run())


def test_repeated_reply_replays_session_audio_without_another_provider_request():
    async def run():
        calls = []
        pcm = b"\x01\x02" * 2400
        def handler(request):
            calls.append(request)
            return httpx.Response(200, content=pcm)
        device = FakeSpeakerDevice()
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        output = DeepgramSpeechOutput(SpeechSettings("fake"), client=client, device_factory=lambda **_: device)
        async with output:
            await output.speak("A freshly generated public answer.")
            first = b"".join(device.chunks)
            device.chunks.clear()
            await output.speak("A freshly generated public answer.")
            assert b"".join(device.chunks) == first == pcm
            assert len(calls) == 1
            await output.speak("A different answer must be synthesized.")
            assert len(calls) == 2
        assert not output._cached_audio and output._cached_text is None
    asyncio.run(run())


def test_private_tool_values_are_not_in_external_speech_payload():
    for name in ["get_customer_profile", "get_active_plan", "get_active_channels", "get_payment_history",
                 "check_payment_status", "get_previous_recharge", "confirm_recharge_choice"]:
        # Private successful results are replaced with fixed application prompts,
        # independently of their particular database field values.
        payload = speech_safe_result(name, {"ok": True, "private": "Synthetic Person 149.00 TX-SECRET"})
        assert "Synthetic Person" not in payload and "149.00" not in payload and "TX-SECRET" not in payload


def test_processor_displays_private_answer_but_speaks_only_safe_prompt():
    async def run():
        application = SimpleNamespace(generate=lambda _: "Private Name, payment 123.45, TX-PRIVATE",
            last_spoken_response="Your payment details are shown on the screen.")
        speaker = SimpleNamespace(speak=AsyncMock())
        turns = asyncio.Queue()
        turns.put_nowait(SpokenTurn("Show my payments", 0))
        turns.put_nowait(None)
        output = []
        await process_turns(application, turns, write=output.append, speaker=speaker)
        assert any("TX-PRIVATE" in line for line in output)
        speaker.speak.assert_awaited_once_with("Your payment details are shown on the screen.")
    asyncio.run(run())


def test_speech_failure_keeps_text_answer_and_session_alive():
    async def run():
        speaker = SimpleNamespace(speak=AsyncMock(side_effect=SpeechOutputError("offline")))
        turns = asyncio.Queue()
        turns.put_nowait(SpokenTurn("help with recharge", 0))
        turns.put_nowait(None)
        output = []
        await process_turns(SimpleNamespace(generate=lambda _: "Use the official portal."), turns,
                            write=output.append, speaker=speaker)
        assert "A: Use the official portal." in output
        assert any("Speech playback is unavailable" in line for line in output)
    asyncio.run(run())


def test_microphone_mode_can_speak_public_answers_and_close_speaker():
    from tests.test_speech_input import FakeSocket, FakeMicrophone, result
    from telecom_support.speech_input import run_microphone
    async def run():
        class Output:
            mute_microphone, epoch, closed = False, 0, False
            def __init__(self):
                self.speak = AsyncMock()
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                self.closed = True
        output = Output()
        socket = FakeSocket([result("How do I recharge?", final=True, endpoint=True)])
        microphone = FakeMicrophone()
        await run_microphone(SimpleNamespace(generate=lambda _: "Use the official portal."),
            SpeechSettings("fake", audio_output=True), speaker_factory=lambda _: output,
            microphone_factory=lambda **k: microphone, connect_factory=lambda *a, **k: socket,
            write=lambda _: None)
        output.speak.assert_awaited_once_with("Use the official portal.")
        assert output.closed and microphone.closed and socket.closed
    asyncio.run(run())


def test_echo_frames_are_discarded_while_output_is_playing():
    from telecom_support.speech_input import run_microphone
    async def run():
        closed = asyncio.Event()
        sent = []
        class Socket:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def send(self, data):
                sent.append(data)
                if isinstance(data, str) and json.loads(data)["type"] == "CloseStream":
                    closed.set()
            async def __aiter__(self):
                await closed.wait()
                if False:
                    yield None
        class Mic:
            frames = 2
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def read(self):
                await asyncio.sleep(0)
                self.frames -= 1
                return b"echoed speaker audio" if self.frames >= 0 else b""
        class Output:
            mute_microphone, epoch = True, 1
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
        await run_microphone(SimpleNamespace(generate=lambda _: "unused"), SpeechSettings("fake", audio_output=True),
            speaker_factory=lambda _: Output(), microphone_factory=lambda **k: Mic(),
            connect_factory=lambda *a, **k: Socket(), write=lambda _: None)
        assert all(isinstance(item, str) for item in sent)
        assert any(json.loads(item)["type"] == "KeepAlive" for item in sent)
    asyncio.run(run())


def test_delayed_echo_results_after_playback_need_a_fresh_speech_start():
    from tests.test_speech_input import FakeMicrophone, result
    from telecom_support.speech_input import run_microphone
    async def run():
        spoke = asyncio.Event()
        class Output:
            mute_microphone, epoch = False, 0
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def speak(self, text):
                self.epoch += 1
                spoke.set()
        class Socket:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def send(self, data):
                pass
            async def __aiter__(self):
                yield json.dumps(result("First question", final=True, endpoint=True))
                await spoke.wait()
                yield json.dumps(result("Delayed speaker echo", final=True, endpoint=True))
                yield json.dumps({"type": "SpeechStarted"})
                yield json.dumps(result("Second question", final=True, endpoint=True))
        calls = []
        await run_microphone(SimpleNamespace(generate=lambda text: calls.append(text) or "Helpful answer"),
            SpeechSettings("fake", audio_output=True), speaker_factory=lambda _: Output(),
            microphone_factory=lambda **k: FakeMicrophone(), connect_factory=lambda *a, **k: Socket(),
            write=lambda _: None)
        assert calls == ["First question", "Second question"]
    asyncio.run(run())
