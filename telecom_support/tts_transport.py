"""Reusable Deepgram Aura socket; close on cancellation to discard stale audio."""
import asyncio
import json
from contextlib import suppress
from urllib.parse import urlencode


class AuraSocket:
    def __init__(self, settings, connect=None):
        self.settings, self.connect, self.socket = settings, connect, None

    async def open(self):
        if self.socket is None:
            if self.connect is None:
                from websockets.asyncio.client import connect
                self.connect = connect
            params = urlencode(dict(model=self.settings.tts_model, encoding="linear16", sample_rate=24000))
            self.socket = await self.connect("wss://api.deepgram.com/v1/speak?" + params,
                additional_headers={"Authorization": "Token " + self.settings.api_key},
                open_timeout=6, close_timeout=1, max_size=2**20, ping_interval=20)

    async def audio(self, text, accepted=None):
        try:
            await self.open()
            await self.socket.send(json.dumps({"type": "Speak", "text": text}))
            if accepted:
                accepted(len(text))
            await self.socket.send(json.dumps({"type": "Flush"}))
            while True:
                message = await asyncio.wait_for(self.socket.recv(), 15)
                if isinstance(message, bytes):
                    yield message
                else:
                    event = json.loads(message)
                    if event.get("type") == "Flushed":
                        return
                    if event.get("type") == "Error":
                        raise RuntimeError("Speech provider rejected synthesis")
        except BaseException:
            # A new socket for the next turn prevents late audio from the old turn.
            await self.close(clear=True)
            raise

    async def close(self, clear=False):
        socket, self.socket = self.socket, None
        if socket is not None:
            if clear:
                with suppress(Exception):
                    await asyncio.wait_for(socket.send(json.dumps({"type": "Clear"})), 1)
            with suppress(Exception):
                await asyncio.wait_for(socket.close(), 2)
