"""Deepgram microphone input for the existing text/account support application.

Adapted from caller_stream.py: 16 kHz mono PCM over listen/v1. Backend work is
serialized in a worker thread so it never blocks microphone or WebSocket traffic.
"""
import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
import json
import os
import re
import time
from urllib.parse import urlencode

from dotenv import load_dotenv

from .settings import ROOT
from voice_support.transcripts import TranscriptBuffer

STOP_COMMANDS = {"stop listening", "end call", "quit", "exit"}


class SpeechInputError(RuntimeError):
    pass


@dataclass(frozen=True)
class SpeechSettings:
    api_key: str = field(repr=False)
    model: str = "nova-3"
    language: str = "en-US"
    device: int | None = None
    endpointing_ms: int = 1100
    tts_model: str = "aura-2-andromeda-en"
    output_device: int | None = None
    audio_output: bool = False
    idle_timeout_seconds: float = 45
    idle_grace_seconds: float = 20
    idle_checkin_seconds: float = 15
    tts_transport: str = "http"

    @classmethod
    def load(cls, *, language=None, device=None, voice=None, output_device=None, audio_output=True,
             idle_timeout=None, idle_grace=None, endpointing_ms=None, idle_checkin=None):
        load_dotenv(ROOT / ".env")
        key = os.getenv("DEEPGRAM") or os.getenv("DEEPGRAM_API_KEY")
        if not key:
            raise SpeechInputError("Set DEEPGRAM (or DEEPGRAM_API_KEY) in the project .env for microphone input.")
        import math
        try:
            timeout = float(idle_timeout if idle_timeout is not None else os.getenv("CALL_IDLE_TIMEOUT_SECONDS", "45"))
            grace = float(idle_grace if idle_grace is not None else os.getenv("CALL_IDLE_GRACE_SECONDS", "20"))
            checkin = float(idle_checkin if idle_checkin is not None else os.getenv("CALL_IDLE_CHECKIN_SECONDS", "15"))
            endpoint = int(endpointing_ms if endpointing_ms is not None else os.getenv("DEEPGRAM_ENDPOINTING_MS", "1100"))
            if not 500 <= endpoint <= 10000:
                raise ValueError()
            if not all(math.isfinite(v) for v in (timeout, grace, checkin)) or timeout < 0 or grace <= 0 or checkin < 0:
                raise ValueError()
        except (ValueError, TypeError):
            raise SpeechInputError("Idle timeout must be zero or positive, grace positive, and speech pause between 500 and 10000 ms.") from None
        transport = os.getenv("DEEPGRAM_TTS_TRANSPORT", "websocket")
        if transport not in {"http", "websocket"}:
            raise SpeechInputError("DEEPGRAM_TTS_TRANSPORT must be http or websocket.")
        return cls(api_key=key, model=os.getenv("DEEPGRAM_STT_MODEL", "nova-3"),
                   language=language or os.getenv("DEEPGRAM_STT_LANGUAGE", "en-US"), device=device,
                   tts_model=voice or os.getenv("DEEPGRAM_TTS_MODEL", "aura-2-andromeda-en"),
                   output_device=output_device, audio_output=audio_output,
                   idle_timeout_seconds=timeout, idle_grace_seconds=grace, endpointing_ms=endpoint,
                   idle_checkin_seconds=checkin, tts_transport=transport)

    @property
    def url(self):
        params = dict(model=self.model, language=self.language, smart_format="true",
                      encoding="linear16", sample_rate=16000, channels=1,
                      endpointing=self.endpointing_ms, interim_results="true",
                      utterance_end_ms=max(1500, self.endpointing_ms + 500), vad_events="true", punctuate="true")
        return "wss://api.deepgram.com/v1/listen?" + urlencode(params)


class MicrophoneSource:
    """Callback-based capture: device reads cannot strand a cancelled executor thread."""
    def __init__(self, *, device=None):
        self.device = device
        self.audio = self.stream = None
        self.queue = asyncio.Queue(maxsize=64)
        self.closed = False
        self.error = None

    def _feed(self, data, status):
        if self.closed:
            return
        if status or self.queue.full():
            self.error = SpeechInputError("Microphone audio overflowed. Check the input device and retry.")
            if not self.queue.full():
                self.queue.put_nowait(b"")
            return
        self.queue.put_nowait(data)

    async def __aenter__(self):
        try:
            import pyaudio
            self.audio = pyaudio.PyAudio()
            loop = asyncio.get_running_loop()
            def callback(data, frames, timing, status):
                if self.closed:
                    return None, pyaudio.paAbort
                try:
                    loop.call_soon_threadsafe(self._feed, data, status)
                except RuntimeError:
                    return None, pyaudio.paAbort
                return None, pyaudio.paContinue
            self.stream = self.audio.open(format=pyaudio.paInt16, channels=1, rate=16000,
                input=True, input_device_index=self.device, frames_per_buffer=1024,
                stream_callback=callback, start=False)
            self.stream.start_stream()
        except Exception:
            await self.close()
            raise SpeechInputError("Could not open the microphone at 16 kHz mono. Check microphone permission, the selected device, and the PyAudio installation.") from None
        return self

    async def read(self):
        if self.error:
            raise self.error
        data = await self.queue.get()
        if self.error:
            raise self.error
        return data

    async def close(self):
        self.closed = True
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


@dataclass(frozen=True)
class SpokenTurn:
    text: str
    started_at: float
    final_at: float | None = None
    number: int = 0
    last_speech_at: float | None = None


def spoken_command(text):
    return re.sub(r"[^\w\s]", "", text.lower()).strip()


async def generated_call_message(application, event, fallback):
    generate = getattr(application, "generate_call_message", None)
    if not callable(generate):
        return fallback
    task = asyncio.create_task(asyncio.to_thread(generate, event))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        with suppress(Exception):
            await task
        raise
    except Exception:
        # Emergency recovery only. Normal call-event wording comes from Gemini.
        return fallback


async def process_turns(application, turns, *, write=print, clock=time.monotonic, speaker=None,
                        control=None, idle_timeout=45, idle_grace=20, idle_checkin=0):
    from .call_control import CallControl
    control = control or CallControl(clock=clock)
    warned_at = None
    hinted_for = None
    presence_prompt = False
    last_answer_at = float("-inf")
    turn_number = 0
    async def emit(answer, spoken=None, *, ending=False):
        write("A: " + answer)
        if speaker is not None:
            from .speech_output import SpeechOutputError
            try:
                await speaker.speak(spoken if spoken is not None else answer)
                if callable(getattr(speaker, "wait_ready", None)):
                    await speaker.wait_ready()
                if not ending:
                    write("Listening...")
            except SpeechOutputError:
                write("Speech playback is unavailable; please read the answer above.")
    async def call_event(event, fallback, *, ending=False, idle=False):
        before = control.last_activity
        text = await generated_call_message(application, event, fallback)
        if idle and (control.last_activity != before or not turns.empty()):
            return False
        await emit(text, ending=ending)
        remember = getattr(application, "remember_call_message", None)
        if callable(remember):
            remember(text)
        return True
    opening = getattr(application, "start_conversation", None)
    if callable(opening):
        generation = asyncio.create_task(asyncio.to_thread(opening))
        try:
            answer = await asyncio.shield(generation)
        except asyncio.CancelledError:
            with suppress(Exception):
                await generation
            raise
        await emit(answer)
    control.touch()
    while True:
        if hinted_for is not None and control.last_activity > hinted_for:
            hinted_for = None
        try:
            if idle_timeout:
                deadline = (warned_at + idle_grace) if warned_at is not None else (control.last_activity + idle_timeout)
                if warned_at is None and hinted_for is None and 0 < idle_checkin < idle_timeout:
                    deadline = min(deadline, control.last_activity + idle_checkin)
                turn = await asyncio.wait_for(turns.get(), max(.001, deadline - clock()))
            else:
                turn = await turns.get()
        except asyncio.TimeoutError:
            if warned_at is not None and control.last_activity > warned_at:
                warned_at = None
                continue
            if warned_at is None:
                if hinted_for is None and 0 < idle_checkin < idle_timeout and clock() >= control.last_activity + idle_checkin:
                    before, began = control.last_activity, clock()
                    control.pending = False
                    presence_prompt = True
                    if not await call_event("idle_hint", "Hello again. Can you hear me?", idle=True):
                        continue
                    # Check-in playback does not count against the caller's wait.
                    if control.last_activity == before:
                        control.last_activity += clock() - began
                        hinted_for = control.last_activity
                    continue
                if clock() < control.last_activity + idle_timeout:
                    continue
                control.pending = False
                presence_prompt = True
                if not await call_event("idle_warning", "Are you still there? Please say something if you'd like to keep the call open.", idle=True):
                    continue
                control.touch()
                warned_at = clock()
            else:
                if await call_event("idle_goodbye", "I haven't heard from you, so I'll end the call now. You're welcome to call again. Goodbye!", ending=True, idle=True):
                    return
                warned_at = None
            continue
        timing_token = None
        timing = None
        try:
            if turn is None:
                return
            from .latency import TurnTiming, active_turn
            turn_number += 1
            timing = TurnTiming(turn.number or turn_number, turn.started_at,
                turn.final_at if turn.final_at is not None else clock(),
                last_speech_at=turn.last_speech_at,
                write=write, audit=getattr(application, "audit", None), clock=clock)
            timing_token = active_turn.set(timing)
            write(f"Timing turn {timing.number}: final transcript received; reply timer running.")
            control.touch()
            warned_at = None
            hinted_for = None
            write("You: " + turn.text)
            action = control.decide(turn.text, started_at=turn.started_at)
            if action == "end":
                await call_event("goodbye", "Thanks for calling Tech Five. Goodbye!", ending=True)
                return
            if presence_prompt:
                presence_prompt = False
                from .account_tools import explicit_confirmation
                if explicit_confirmation(spoken_command(turn.text)) or spoken_command(turn.text) in {"im here", "i am here", "i can hear you", "hello"}:
                    await call_event("presence_ack", "Thanks, I'm here. Go ahead.")
                    control.touch()
                    continue
                if spoken_command(turn.text) in {"no", "not really", "i cant hear you"}:
                    spoken = (getattr(application, "last_answer_speech", None)
                              or getattr(application, "last_answer", None)
                              or "My replies also appear on screen. Please check your speaker volume.")
                    await emit(spoken)
                    control.touch()
                    continue
            if action == "ask":
                await call_event("confirm_end", "Would you like me to end the call?")
                control.asked()
                control.touch()
                continue
            if action == "stay":
                await call_event("stay", "Of course, we'll keep the call open. What else would you like help with?")
                control.touch()
                continue
            agent = getattr(application, "account_agent", None)
            if agent and (agent.service.pending_recharge or getattr(getattr(agent.service, "ticket_service", None), "pending", None)):
                from .account_tools import explicit_confirmation
                if explicit_confirmation(spoken_command(turn.text)) and turn.started_at < last_answer_at:
                    await emit("Please confirm again now that the package details have been presented.")
                    last_answer_at = clock()
                    continue
            # Deepgram punctuates utterances; the existing confirmation check accepts
            # punctuation. Other customer text is passed through without rewriting.
            generation_started = clock()
            streaming = speaker is not None and callable(getattr(application, "generate_stream", None))
            if streaming:
                from .streaming import generate_and_speak
                generation = asyncio.create_task(generate_and_speak(application, turn.text, speaker))
            else:
                generation = asyncio.create_task(asyncio.to_thread(application.generate, turn.text))
            try:
                answer = await asyncio.shield(generation)
                streamed = False
                if streaming:
                    answer, streamed = answer
            except asyncio.CancelledError:
                # A synchronous request cannot be cancelled safely halfway through.
                # Let it finish before the CLI closes MongoDB or discards session state.
                with suppress(Exception):
                    await generation
                raise
            except Exception:
                await emit("I couldn't complete that request. Please try again.")
                continue
            if getattr(application, "audit", None):
                stage = "Generation and streamed playback finished" if streamed else "Backend reply ready"
                application.audit.event(f"{stage}: {1000 * (clock() - generation_started):.0f} ms after final transcript processing began.")
            if streamed:
                write("A: " + answer)
                write("Listening...")
            else:
                await emit(answer, getattr(application, "last_spoken_response", None))
            from .call_control import end_confirmation_question
            if getattr(application, "call_action", None) == "confirm_end" or end_confirmation_question(answer):
                if not end_confirmation_question(answer):
                    await call_event("confirm_end", "Would you like me to end the call?")
                control.asked()
            control.touch()
            last_answer_at = float("inf") if speaker is not None and getattr(speaker, "interrupted", False) else clock()
        finally:
            if timing is not None:
                timing.finish()
                active_turn.reset(timing_token)
            turns.task_done()


async def run_microphone(application, settings, *, microphone_factory=MicrophoneSource,
                         connect_factory=None, write=print, clock=time.monotonic, speaker_factory=None):
    if settings.audio_output:
        from .speech_output import DeepgramSpeechOutput
        factory = speaker_factory or DeepgramSpeechOutput
        async with factory(settings) as speaker:
            speaker.audit = getattr(application, "audit", None)
            return await _microphone_session(application, settings, microphone_factory=microphone_factory,
                connect_factory=connect_factory, write=write, clock=clock, speaker=speaker)
    return await _microphone_session(application, settings, microphone_factory=microphone_factory,
        connect_factory=connect_factory, write=write, clock=clock)


async def _microphone_session(application, settings, *, microphone_factory=MicrophoneSource,
                              connect_factory=None, write=print, clock=time.monotonic, speaker=None):
    if connect_factory is None:
        try:
            from websockets.asyncio.client import connect
        except ImportError:
            raise SpeechInputError("Install microphone dependencies with pip install -r requirements.txt.") from None
        connect_factory = connect
    tasks = []
    try:
        async with connect_factory(settings.url, additional_headers={"Authorization": "Token " + settings.api_key},
                                   max_size=2**20, open_timeout=15, close_timeout=5) as socket:
            if getattr(application, "audit", None): application.audit.stt_connected()
            async with microphone_factory(device=settings.device) as microphone:
                turns = asyncio.Queue(maxsize=32)
                stop_requested = asyncio.Event()
                stop_turn = None
                from .latency import AudioTimeline
                audio_timeline = AudioTimeline()
                candidate_until = 0
                from .call_control import CallControl
                control = CallControl(clock=clock)

                async def send_audio():
                    nonlocal candidate_until
                    last_keepalive = float("-inf")
                    async def send_pcm(data):
                        await socket.send(data)
                        audio_timeline.sent(len(data), clock())
                        if getattr(application, "audit", None): application.audit.audio_sent(len(data))
                    while True:
                        data = await microphone.read()
                        if not data:
                            await socket.send(json.dumps({"type": "CloseStream"}))
                            return
                        if speaker is not None and speaker.mute_microphone:
                            observe = getattr(speaker, "observe_microphone", None)
                            preroll = observe(data) if callable(observe) else None
                            if preroll is not None:
                                # Upload the start of the interruption too, not just
                                # the audio captured after speech detection fired.
                                candidate_until = clock() + 1.5
                                await send_pcm(preroll)
                                continue
                            if clock() < candidate_until:
                                await send_pcm(data)
                                continue
                            if clock() - last_keepalive >= 3:
                                await socket.send(json.dumps({"type": "KeepAlive"}))
                                last_keepalive = clock()
                            continue
                        await send_pcm(data)

                async def receive_transcripts():
                    nonlocal stop_turn
                    buffer = TranscriptBuffer()
                    started_at = None
                    turn_number = 0
                    last_speech_at = None
                    playback_epoch = speaker.epoch if speaker else 0
                    awaiting_fresh_speech = False
                    async for raw in socket:
                        event = json.loads(raw)
                        kind = event.get("type")
                        if kind == "Error":
                            raise SpeechInputError("Deepgram reported a transcription error. Check its model/language configuration and retry.")
                        if speaker is not None:
                            if speaker.epoch != playback_epoch:
                                playback_epoch = speaker.epoch
                                buffer, started_at = TranscriptBuffer(), None
                                last_speech_at = None
                                awaiting_fresh_speech = True
                            if speaker.mute_microphone:
                                confirms = getattr(speaker, "confirms_interruption", None)
                                if (getattr(speaker, "speaking", False) and clock() < candidate_until
                                        and callable(confirms) and confirms(event)):
                                    speaker.interrupt()
                                    awaiting_fresh_speech = False
                                    started_at = clock()
                                else:
                                    buffer, started_at = TranscriptBuffer(), None
                                    last_speech_at = None
                                    continue
                            if awaiting_fresh_speech:
                                if kind == "SpeechStarted":
                                    awaiting_fresh_speech = False
                                elif kind in {"Results", "UtteranceEnd"}:
                                    continue
                        if kind == "SpeechStarted" and started_at is None:
                            started_at = clock()
                            write("Timing: speech detected; timer started.")
                        if kind == "Results" and started_at is None:
                            alternatives = event.get("channel", {}).get("alternatives", [])
                            if alternatives and alternatives[0].get("transcript", "").strip():
                                started_at = clock()
                                write("Timing: speech transcript detected; timer started.")
                        if kind == "Results":
                            alternatives = event.get("channel", {}).get("alternatives", [])
                            word_items = alternatives[0].get("words", []) if alternatives else []
                            if word_items:
                                last_speech_at = audio_timeline.wall_time(word_items[-1].get("end"))
                            if (alternatives and alternatives[0].get("transcript", "").strip()
                                    and alternatives[0].get("confidence", 1) >= .5):
                                control.touch()
                        text = buffer.consume(event)
                        if text:
                            turn_number += 1
                            turn = SpokenTurn(text, started_at if started_at is not None else clock(), clock(), turn_number, last_speech_at)
                            from .call_control import direct_end_request
                            if direct_end_request(text):
                                write("You: " + text)
                                stop_turn = turn
                                stop_requested.set()
                                return
                            if turns.full():
                                raise SpeechInputError("Too many spoken turns are waiting. Please pause for the assistant's reply and restart.")
                            turns.put_nowait(turn)
                            started_at = None
                            last_speech_at = None
                    await turns.put(None)

                sender = asyncio.create_task(send_audio())
                receiver = asyncio.create_task(receive_transcripts())
                processor = asyncio.create_task(process_turns(application, turns, write=write, clock=clock, speaker=speaker,
                    control=control, idle_timeout=settings.idle_timeout_seconds, idle_grace=settings.idle_grace_seconds,
                    idle_checkin=settings.idle_checkin_seconds))
                tasks = [sender, receiver, processor]
                write("Listening. " + ("You can interrupt a spoken reply. Use headphones to reduce speaker echo. " if speaker else "Speak your question; answers appear here. ")
                      + "Say 'stop listening' or press Ctrl+C to exit.")
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                if processor in done:
                    await processor
                elif receiver in done:
                    await receiver
                    sender.cancel()
                    if stop_requested.is_set():
                        processor.cancel()
                        await asyncio.gather(processor, return_exceptions=True)
                        from .latency import TurnTiming, active_turn
                        timing = TurnTiming(stop_turn.number, stop_turn.started_at, stop_turn.final_at,
                            last_speech_at=stop_turn.last_speech_at,
                            write=write, audit=getattr(application, "audit", None), clock=clock)
                        token = active_turn.set(timing)
                        try:
                            farewell = await generated_call_message(application, "goodbye", "Thanks for calling Tech Five. Goodbye!")
                            write("A: " + farewell)
                            if speaker is not None:
                                from .speech_output import SpeechOutputError
                                with suppress(SpeechOutputError, asyncio.TimeoutError):
                                    await asyncio.wait_for(speaker.speak(farewell), 5)
                        finally:
                            timing.finish()
                            active_turn.reset(token)
                    else:
                        await processor
                else:
                    await sender
                    await asyncio.wait_for(receiver, timeout=15)
                    await processor
    except (SpeechInputError, asyncio.CancelledError):
        raise
    except Exception as exc:
        raise SpeechInputError(f"Speech input stopped ({type(exc).__name__}). Check the Deepgram key, connection and audio device.") from None
    finally:
        if getattr(application, "audit", None): application.audit.stt_disconnected()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
