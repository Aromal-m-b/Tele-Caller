"""Per-turn monotonic timings; no transcript or account values in telemetry."""
from contextlib import contextmanager
from contextvars import ContextVar
import threading
import time
from collections import deque

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.embeddings import Embeddings

active_turn = ContextVar("voice_turn_timing", default=None)


class TurnTiming:
    def __init__(self, number, speech_at, final_at, *, last_speech_at=None, write=print, audit=None, clock=time.monotonic):
        self.number, self.clock, self.write, self.audit = number, clock, write, audit
        self.points = {"speech_detected": speech_at, "transcript_final": final_at}
        if last_speech_at is not None: self.points["speech_end_estimate"] = last_speech_at
        self.durations, self.lock, self.reported = {}, threading.RLock(), False

    def mark(self, name):
        with self.lock:
            self.points.setdefault(name, self.clock())

    @contextmanager
    def measure(self, name):
        start = self.clock()
        try:
            yield
        finally:
            with self.lock:
                self.durations[name] = self.durations.get(name, 0) + self.clock() - start

    def first_audio(self):
        with self.lock:
            if self.reported:
                return
            self.reported = True
            self.mark("first_audio_write")
            now = self.points["first_audio_write"]
            fields = [f"speech detected->audio {now-self.points['speech_detected']:.3f}s (includes caller speaking)",
                      f"final transcript->audio {now-self.points['transcript_final']:.3f}s"]
            if "speech_end_estimate" in self.points:
                end = self.points["speech_end_estimate"]
                fields += [f"estimated speech end->audio {now-end:.3f}s",
                           f"endpoint/STT wait {(self.points['transcript_final']-end)*1000:.1f}ms"]
            for name, label in [("retrieval", "RAG total"), ("query_embedding", "query embedding API"), ("faiss", "local vector search")]:
                if name in self.durations:
                    precision = 3 if name == "faiss" else 1
                    fields.append(f"{label} {self.durations[name]*1000:.{precision}f}ms")
            for start, end, label in [("llm_start", "llm_first_token", "LLM first token"),
                                       ("llm_first_token", "first_sentence", "token->sentence buffer"),
                                       ("tts_start", "first_pcm", "TTS first PCM")]:
                if start in self.points and end in self.points:
                    fields.append(f"{label} {(self.points[end]-self.points[start])*1000:.1f}ms")
            if "llm_start" in self.points and "llm_first_token" not in self.points and "llm_complete" in self.points:
                fields.append(f"LLM buffered response {(self.points['llm_complete']-self.points['llm_start'])*1000:.1f}ms")
            line = f"Timing turn {self.number}: " + " | ".join(fields)
            self._emit(line)

    def _emit(self, line):
        # Telemetry must not turn otherwise successful playback into an audio error.
        try:
            self.write(line)
        except (OSError, UnicodeError):
            pass
        if self.audit:
            try:
                self.audit.event(line)
            except (OSError, UnicodeError):
                pass

    def finish(self):
        if not self.reported:
            line = f"Timing turn {self.number}: no reply audio was written (text-only, interrupted or failed)."
            self._emit(line)


@contextmanager
def measure(name):
    timer = active_turn.get()
    if timer is None:
        yield
    else:
        with timer.measure(name):
            yield


def mark(name):
    timer = active_turn.get()
    if timer is not None: timer.mark(name)


class ModelTiming(BaseCallbackHandler):
    def on_chat_model_start(self, *args, **kwargs): mark("llm_start")
    def on_llm_start(self, *args, **kwargs): mark("llm_start")
    def on_llm_new_token(self, token, **kwargs):
        if token: mark("llm_first_token")
    def on_llm_end(self, *args, **kwargs): mark("llm_complete")


class MeasuredEmbeddings(Embeddings):
    def __init__(self, provider): self.provider = provider
    def embed_documents(self, texts): return self.provider.embed_documents(texts)
    def embed_query(self, text):
        with measure("query_embedding"):
            return self.provider.embed_query(text)


class AudioTimeline:
    """Map provider audio offsets to local send times, including muted-audio gaps."""
    def __init__(self):
        self.seconds, self.frames = 0.0, deque(maxlen=2048)

    def sent(self, size, at):
        duration = size / 32000  # 16 kHz mono int16
        self.frames.append((self.seconds, self.seconds + duration, at))
        self.seconds += duration

    def wall_time(self, offset):
        try:
            offset = float(offset)
        except (ValueError, TypeError):
            return None
        for start, end, at in reversed(self.frames):
            if start <= offset <= end:
                return at - (end - offset)
        return None
