import asyncio
import threading
import pyaudio

FORMAT = pyaudio.paInt16
CHANNELS = 1
DEFAULT_RATE_IN = 16000
DEFAULT_RATE_OUT = 24000
DEFAULT_CHUNK_SIZE = 512


class HardwareAudioIO:
    def __init__(
        self,
        rate_in: int = DEFAULT_RATE_IN,
        rate_out: int = DEFAULT_RATE_OUT,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ):
        self.rate_in = rate_in
        self.rate_out = rate_out
        self.chunk_size = chunk_size

        self.p = pyaudio.PyAudio()
        self.mic_stream = None
        self.speaker_stream = None

        self.speaker_lock = threading.Lock()
        self.audio_queue = asyncio.Queue(maxsize=40)
        self.generation = [0]

    def start_streams(self):
        """Initializes and keeps device streams open across session reconnections."""
        if self.mic_stream is None:
            self.mic_stream = self.p.open(
                format=FORMAT,
                channels=CHANNELS,
                rate=self.rate_in,
                input=True,
                frames_per_buffer=self.chunk_size,
            )

        if self.speaker_stream is None:
            self.speaker_stream = self.p.open(
                format=FORMAT,
                channels=CHANNELS,
                rate=self.rate_out,
                output=True,
                frames_per_buffer=256,
            )

    async def read_input_chunk(self) -> bytes:
        """Non-blocking read from mic stream."""
        return await asyncio.to_thread(
            self.mic_stream.read,
            self.chunk_size,
            exception_on_overflow=False,
        )

    def enqueue_playback(self, audio_data: bytes):
        """Pushes incoming model audio chunks tagged with current generation."""
        if self.audio_queue.full():
            try:
                self.audio_queue.get_nowait()
                self.audio_queue.task_done()
            except asyncio.QueueEmpty:
                pass
        self.audio_queue.put_nowait((self.generation[0], audio_data))

    def clear_playback_buffer(self) -> int:
        """Flushes unplayed audio and resets the hardware speaker on barge-in."""
        self.generation[0] += 1
        while True:
            try:
                self.audio_queue.get_nowait()
                self.audio_queue.task_done()
            except asyncio.QueueEmpty:
                break

        def _reset_hw():
            with self.speaker_lock:
                if self.speaker_stream is not None:
                    if self.speaker_stream.is_stopped():
                        self.speaker_stream.start_stream()
                    else:
                        self.speaker_stream.stop_stream()
                        self.speaker_stream.start_stream()

        threading.Thread(target=_reset_hw, daemon=True).start()
        return self.generation[0]

    async def playback_loop(self, stop_event: asyncio.Event):
        """Dedicated consumer worker that writes audio chunks to speaker."""
        while not stop_event.is_set():
            try:
                chunk_gen, audio_data = await asyncio.wait_for(
                    self.audio_queue.get(), timeout=0.1
                )
            except asyncio.TimeoutError:
                continue

            try:
                if chunk_gen != self.generation[0]:
                    continue

                def _write():
                    with self.speaker_lock:
                        if chunk_gen == self.generation[0] and self.speaker_stream:
                            self.speaker_stream.write(audio_data)

                await asyncio.to_thread(_write)
            finally:
                self.audio_queue.task_done()

    def close(self):
        """Terminates hardware handles."""
        for stream in (self.mic_stream, self.speaker_stream):
            if stream is not None:
                try:
                    if stream.is_active():
                        stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
        self.p.terminate()