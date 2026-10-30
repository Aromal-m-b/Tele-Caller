"""Twilio Media Streams transport for the existing PCM voice-agent session.

Python 3.10--3.12: audioop provides stateful telephone-rate conversion. No
microphone, sound card or audio file is used on this transport.
"""
import asyncio
import audioop
import base64
import binascii
import secrets

from .deepgram_agent import run_voice_agent
from .speech_input import SpeechInputError


class TwilioAudio:
    def __init__(self, socket, stream_sid):
        self.socket, self.stream_sid = socket, stream_sid
        self.queue = asyncio.Queue(maxsize=250)
        self.send_lock = asyncio.Lock()
        self.input_state = self.output_state = None
        self.marks, self.clear_tasks = {}, []
        self.clear_pending = None

    async def __aenter__(self): return self

    async def __aexit__(self, *_): return False

    async def send(self, payload):
        async with self.send_lock:
            await self.socket.send_json(dict(payload, streamSid=self.stream_sid))

    def accept_media(self, payload):
        if not isinstance(payload, str) or not 0 < len(payload) <= 16000:
            raise SpeechInputError("Invalid telephone audio frame.")
        try:
            raw = base64.b64decode(payload, validate=True)
        except (ValueError, binascii.Error):
            raise SpeechInputError("Invalid telephone audio encoding.") from None
        pcm = audioop.ulaw2lin(raw, 2)
        pcm, self.input_state = audioop.ratecv(pcm, 2, 1, 8000, 16000, self.input_state)
        try:
            self.queue.put_nowait(pcm)
        except asyncio.QueueFull:
            raise SpeechInputError("Telephone input queue overflowed.") from None

    async def read(self): return await self.queue.get()

    async def write(self, pcm):
        if self.clear_pending is not None:
            await self.clear_pending
        audio, self.output_state = audioop.ratecv(pcm, 2, 1, 24000, 8000, self.output_state)
        audio = audioop.lin2ulaw(audio, 2)
        if audio:
            await self.send({"event": "media", "media": {"payload": base64.b64encode(audio).decode("ascii")}})
        # Bound Twilio's queued playback rather than filling it at network speed.
        await asyncio.sleep(len(pcm) / 48000)

    async def drain(self):
        if self.clear_pending is not None: await self.clear_pending
        name = secrets.token_hex(12)
        future = asyncio.get_running_loop().create_future()
        self.marks[name] = future
        try:
            await self.send({"event": "mark", "mark": {"name": name}})
            await asyncio.wait_for(future, 10)
        except asyncio.TimeoutError:
            raise SpeechInputError("Telephone playback acknowledgement timed out.") from None
        finally:
            self.marks.pop(name, None)

    def acknowledge(self, name):
        future = self.marks.get(name)
        if future is not None and not future.done(): future.set_result(None)

    def interrupt(self):
        # Called synchronously by AgentPlayback. Writes wait until clear is sent.
        for future in self.marks.values():
            if not future.done(): future.set_result(None)
        self.output_state = None
        self.clear_pending = asyncio.create_task(self.send({"event": "clear"}))
        self.clear_tasks.append(self.clear_pending)

    async def receive(self):
        while True:
            payload = await self.socket.receive_json()
            if payload.get("streamSid") != self.stream_sid:
                raise SpeechInputError("Telephone stream identity mismatch.")
            kind = payload.get("event")
            if kind == "media":
                media = payload.get("media", {})
                if media.get("track", "inbound") == "inbound":
                    self.accept_media(media.get("payload"))
            elif kind == "mark": self.acknowledge(payload.get("mark", {}).get("name"))
            elif kind == "stop": return
            elif kind != "dtmf": raise SpeechInputError("Unexpected telephone stream event.")

    async def close(self):
        for future in self.marks.values():
            if not future.done(): future.cancel()
        await asyncio.gather(*self.clear_tasks, return_exceptions=True)


async def run_phone_call(socket, start, prepare, *, runner=run_voice_agent):
    """Keep receiving audio/mark events during model startup and playback."""
    audio = TwilioAudio(socket, start["streamSid"])
    app, tasks = None, []

    async def agent():
        nonlocal app
        app, speech, config = await prepare()
        await runner(app, speech, config=config, write=app.audit.write,
            microphone_factory=lambda **_: audio, device_factory=lambda **_: audio)

    try:
        tasks = [asyncio.create_task(audio.receive()), asyncio.create_task(agent())]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done: task.result()
    finally:
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await audio.close()
        if app is not None: app.audit.close()
