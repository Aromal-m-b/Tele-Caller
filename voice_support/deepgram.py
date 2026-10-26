"""Streaming recognition and incrementally delivered raw PCM synthesis."""
from urllib.parse import urlencode
import httpx
from websockets.asyncio.client import connect
from .transcripts import TranscriptBuffer


class DeepgramVoice:
    def __init__(self, settings):
        self.settings = settings
        self.headers = {"Authorization": "Token " + settings.deepgram_key}
        self.http = httpx.AsyncClient(headers=self.headers, timeout=30)

    def listen(self):
        params = dict(model=self.settings.stt_model, language="en-US",
                      encoding="linear16", sample_rate=16000, channels=1,
                      interim_results="true", vad_events="true",
                      endpointing=300, utterance_end_ms=1000, punctuate="true")
        return connect("wss://api.deepgram.com/v1/listen?" + urlencode(params),
                       additional_headers=self.headers, max_size=2**20)

    async def speak(self, text, audio):
        params = dict(model=self.settings.tts_model, encoding="linear16",
                      sample_rate=24000, container="none")
        async with self.http.stream("POST", "https://api.deepgram.com/v1/speak",
                                    params=params, json={"text": text}) as response:
            response.raise_for_status()
            pending = b""
            async for chunk in response.aiter_bytes():
                pending += chunk
                while len(pending) >= 960:
                    await audio.audio_queue.put((audio.generation[0], pending[:960]))
                    pending = pending[960:]
            if pending:
                if len(pending) % 2:
                    raise RuntimeError("Deepgram returned an incomplete PCM sample.")
                await audio.audio_queue.put((audio.generation[0], pending))

    async def close(self):
        await self.http.aclose()
