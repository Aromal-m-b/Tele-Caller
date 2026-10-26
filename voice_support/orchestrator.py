import asyncio
import json
import logging
import time
from .deepgram import TranscriptBuffer

log = logging.getLogger(__name__)


class VoiceOrchestrator:
    def __init__(self, audio, agent, voice):
        self.audio, self.agent, self.voice = audio, agent, voice
        self.response_task = None

    async def interrupt(self):
        if self.response_task:
            self.response_task.cancel()
            await asyncio.gather(self.response_task, return_exceptions=True)
            self.response_task = None
        self.audio.clear_playback_buffer()

    async def respond(self, text):
        started = time.monotonic()
        try:
            print("Customer:", text)
            answer = await self.agent.reply(text)
            print("Maya:", answer)
            await self.voice.speak(answer, self.audio)
            log.info("Response audio queued in %.3fs", time.monotonic() - started)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Response failed; caller may retry")

    async def run(self):
        stop = asyncio.Event()
        tasks = []
        try:
            async with self.voice.listen() as socket:
                async def microphone():
                    while True:
                        await socket.send(await self.audio.read_input_chunk())

                async def receive():
                    buffer = TranscriptBuffer()
                    async for raw in socket:
                        event = json.loads(raw)
                        if event.get("type") == "Error":
                            raise RuntimeError("Deepgram transcription failed: " + str(event.get("description", "unknown error")))
                        if event.get("type") == "SpeechStarted":
                            await self.interrupt()
                        text = buffer.consume(event)
                        if text:
                            await self.interrupt()
                            self.response_task = asyncio.create_task(self.respond(text))

                tasks = [asyncio.create_task(microphone()), asyncio.create_task(receive()),
                         asyncio.create_task(self.audio.playback_loop(stop))]
                print("Connected. Speak in English; use headphones to prevent echo. Ctrl+C to stop.")
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
        finally:
            stop.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.interrupt()
