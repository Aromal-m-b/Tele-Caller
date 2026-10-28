"""Natural female Deepgram Aura-2 output, streamed as raw PCM to the speaker.

Private account values are excluded upstream by the account tool's speech payload.
"""
import asyncio
from contextlib import suppress
from datetime import date
from decimal import Decimal
import html
import re
import time

import httpx


class SpeechOutputError(RuntimeError):
    pass


def speech_text(text):
    """Format the same meaning for natural reading; never invent or summarize facts."""
    text = html.unescape(text)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1, \2", text)
    text = re.sub(r"(?m)^\s*(?:#{1,6}\s+|[-*•]\s+|\d+[.)]\s+)", "", text)
    text = re.sub(r"[*_`]+", "", text)
    text = re.sub(r"https?://", "", text)
    text = re.sub(r"\bwww\.sundirect\.in/?(?=\s|[.,!?]|$)", "Sun Direct dot in", text, flags=re.I)
    text = text.replace("/month", " per month")

    def currency(match):
        value = Decimal(match.group(1).replace(",", ""))
        rupees = int(value)
        paise = int((value - rupees) * 100)
        result = f"{rupees:,} " + ("rupee" if rupees == 1 else "rupees")
        if not rupees and paise:
            return f"{paise} " + ("paisa" if paise == 1 else "paise")
        return result + (f" and {paise} " + ("paisa" if paise == 1 else "paise") if paise else "")
    text = re.sub(r"(?:\bINR\b|\bRs\.?|₹)\s*(\d+(?:,\d{3})*(?:\.\d{1,2})?)", currency, text, flags=re.I)

    def spoken_date(match):
        try:
            value = date.fromisoformat(match.group())
            return f"{value.day} {value.strftime('%B')} {value.year}"
        except ValueError:
            return match.group()
    text = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", spoken_date, text)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return " ".join(line if line.endswith((".", "?", "!", ":", ";")) else line + "." for line in lines)


def text_chunks(text, limit=1500):
    while len(text) > limit:
        boundaries = [m.end() for m in re.finditer(r"[.!?;]\s+", text[:limit])]
        split = boundaries[-1] if boundaries else text.rfind(" ", 0, limit)
        if split < 1:
            split = limit
        yield text[:split].strip()
        text = text[split:].strip()
    if text:
        yield text


class SpeakerDevice:
    def __init__(self, *, device=None):
        self.device, self.audio, self.stream = device, None, None

    async def __aenter__(self):
        try:
            import pyaudio
            self.audio = pyaudio.PyAudio()
            self.stream = self.audio.open(format=pyaudio.paInt16, channels=1, rate=24000,
                output=True, output_device_index=self.device, frames_per_buffer=1024)
        except Exception:
            await self.close()
            raise SpeechOutputError("Could not open the speaker at 24 kHz mono. Check the output device.") from None
        return self

    async def _blocking(self, function, *args):
        operation = asyncio.create_task(asyncio.to_thread(function, *args))
        try:
            return await asyncio.shield(operation)
        except asyncio.CancelledError:
            with suppress(Exception):
                await operation
            raise

    async def write(self, data):
        await self._blocking(self.stream.write, data)

    async def drain(self):
        def finish():
            self.stream.stop_stream()
            self.stream.start_stream()
        await self._blocking(finish)

    async def close(self):
        def release():
            try:
                if self.stream is not None:
                    with suppress(Exception):
                        self.stream.stop_stream()
                    with suppress(Exception):
                        self.stream.close()
                    self.stream = None
            finally:
                if self.audio is not None:
                    self.audio.terminate()
                    self.audio = None
        await asyncio.to_thread(release)

    async def __aexit__(self, *args):
        await self.close()


class DeepgramSpeechOutput:
    def __init__(self, settings, *, device_factory=SpeakerDevice, client=None, clock=time.monotonic):
        self.settings, self.device_factory, self.clock = settings, device_factory, clock
        self.client, self.device = client, None
        self.speaking, self.suppress_until, self.epoch = False, 0, 0
        from .barge_in import BargeInDetector
        self.detector = BargeInDetector()
        self._playback_task = None
        self.interrupted = False
        self.current_text = ""
        self.audit = None
        self.first_audio_at = None
        self.synthesis_started_at = None
        self._cached_text, self._cached_audio = None, b""
        self._collect_audio = None
        self.socket_transport = None

    def observe_microphone(self, data):
        if not self.speaking:
            return None
        preroll = self.detector.feed(data)
        # VAD only opens a transcription window. Breathing can pass VAD; it
        # must never cancel playback without recognized words.
        return preroll

    def confirms_interruption(self, event):
        if event.get("type") != "Results":
            return False
        alternatives = event.get("channel", {}).get("alternatives", [])
        if not alternatives:
            return False
        alternative = alternatives[0]
        words = re.findall(r"[a-z]+", alternative.get("transcript", "").lower())
        if not words or alternative.get("confidence", 0) < .75:
            return False
        if len(words) == 1 and words[0] not in {"stop", "wait", "hello", "hey", "no", "yes", "quit", "exit"}:
            return False
        # Do not accept the assistant's own words leaking into the microphone.
        spoken = " " + " ".join(re.findall(r"[a-z]+", self.current_text.lower())) + " "
        return " " + " ".join(words) + " " not in spoken

    def interrupt(self):
        if self._playback_task is not None and not self._playback_task.done():
            self.interrupted = True
            self.speaking = False
            self.suppress_until = 0
            self._playback_task.cancel()
            if self.audit: self.audit.event("Playback interrupted; the assistant response may not have been fully heard.")

    @property
    def mute_microphone(self):
        return self.speaking or self.clock() < self.suppress_until

    async def wait_ready(self):
        delay = self.suppress_until - self.clock()
        if delay > 0:
            await asyncio.sleep(delay)

    async def __aenter__(self):
        self.device = self.device_factory(device=self.settings.output_device)
        try:
            await self.device.__aenter__()
            if self.client is None:
                self.client = httpx.AsyncClient(headers={"Authorization": "Token " + self.settings.api_key},
                    timeout=httpx.Timeout(30, connect=10),
                    limits=httpx.Limits(max_connections=4, max_keepalive_connections=4, keepalive_expiry=120))
            if getattr(self.settings, "tts_transport", "http") == "websocket":
                from .tts_transport import AuraSocket
                self.socket_transport = AuraSocket(self.settings)
                try:
                    await self.socket_transport.open()
                except Exception:
                    await self.socket_transport.close()
                    self.socket_transport = None
        except Exception:
            try:
                await self.device.__aexit__(None, None, None)
            finally:
                if self.client is not None:
                    await self.client.aclose()
            raise
        return self

    async def speak(self, text):
        from .latency import mark
        mark("tts_start")
        self.first_audio_at = None
        self.synthesis_started_at = self.clock()
        self.interrupted = False
        self.current_text = text
        self.detector.reset()
        task = self._playback_task = asyncio.create_task(self._play(text))
        try:
            # Child cancellation is an expected barge-in result; cancellation of
            # this caller must still propagate. gather distinguishes the two on
            # Python 3.10 too, where Task.cancelling() is unavailable.
            result, = await asyncio.gather(task, return_exceptions=True)
            if isinstance(result, BaseException):
                if not (isinstance(result, asyncio.CancelledError) and self.interrupted):
                    raise result
        finally:
            self._playback_task = None

    async def _write(self, data):
        from .latency import mark, active_turn
        mark("first_pcm")
        if self.first_audio_at is None:
            self.first_audio_at = self.clock()
            if self.audit:
                self.audit.event(f"TTS first PCM ready: {1000 * (self.first_audio_at - self.synthesis_started_at):.0f} ms after speech request began.")
        if self._collect_audio is not None:
            if len(self._collect_audio) + len(data) <= 2 * 1024 * 1024:
                self._collect_audio.extend(data)
            else:
                self._collect_audio = None
        self.detector.playback(data)
        await self.device.write(data)
        timer = active_turn.get()
        if timer is not None: timer.first_audio()

    async def _play(self, text):
        text = speech_text(text)
        if not text:
            return
        self.speaking = True
        self.epoch += 1
        replay = text == self._cached_text and bool(self._cached_audio)
        self._collect_audio = None if replay else bytearray()
        try:
            if replay:
                if self.audit: self.audit.event("Replayed the previous completed reply from session RAM; no new TTS request.")
                for offset in range(0, len(self._cached_audio), 960):
                    await self._write(self._cached_audio[offset:offset + 960])
                await self.device.drain()
                return
            for piece in text_chunks(text):
                pending = b""
                audio = self._audio(piece)
                try:
                    async for chunk in audio:
                        pending += chunk
                        while len(pending) >= 960:
                            await self._write(pending[:960])
                            pending = pending[960:]
                finally:
                    await audio.aclose()
                if len(pending) % 2:
                    raise SpeechOutputError("Speech synthesis returned an incomplete audio sample.")
                if pending:
                    await self._write(pending)
            await self.device.drain()
            if self._collect_audio is not None:
                self._cached_text, self._cached_audio = text, bytes(self._collect_audio)
        except asyncio.CancelledError:
            raise
        except Exception:
            raise SpeechOutputError("Speech playback failed. The complete answer is still available on screen.") from None
        finally:
            self._collect_audio = None
            self.speaking = False
            self.suppress_until = 0 if self.interrupted else self.clock() + .2

    async def _audio(self, piece):
        if self.socket_transport is not None:
            received = False
            stream = self.socket_transport.audio(piece, self.audit.synthesized if self.audit else None)
            try:
                async for chunk in stream:
                    received = True
                    yield chunk
                return
            except Exception:
                await self.socket_transport.close()
                self.socket_transport = None
                if received:
                    raise  # Never replay words already delivered to the caller.
                if self.audit: self.audit.event("TTS socket unavailable; using HTTP for this session.")
            finally:
                await stream.aclose()
        params = {"model": self.settings.tts_model, "encoding": "linear16", "sample_rate": 24000, "container": "none"}
        async with self.client.stream("POST", "https://api.deepgram.com/v1/speak", params=params, json={"text": piece}) as response:
            response.raise_for_status()
            if self.audit: self.audit.synthesized(len(piece))
            async for chunk in response.aiter_bytes():
                yield chunk

    async def __aexit__(self, *args):
        self._cached_text, self._cached_audio = None, b""
        try:
            if self.socket_transport is not None:
                await self.socket_transport.close()
            if self.client is not None:
                await self.client.aclose()
        finally:
            if self.device is not None:
                await self.device.__aexit__(*args)
