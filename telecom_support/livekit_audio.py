"""LiveKit WebRTC audio adapters for the existing Deepgram Voice Agent."""
import asyncio
from livekit import rtc

from .deepgram_agent import run_voice_agent
from .speech_input import SpeechInputError


class LiveKitAudio:
    def __init__(self, source):
        self.source = source
        self.queue = asyncio.Queue(maxsize=250)
        self.listening = False

    async def __aenter__(self): return self
    async def __aexit__(self, *_): return False
    async def read(self):
        self.listening = True
        return await self.queue.get()

    async def write(self, pcm):
        if len(pcm)%2: raise SpeechInputError("Incomplete WebRTC PCM frame.")
        await self.source.capture_frame(rtc.AudioFrame(pcm,24000,1,len(pcm)//2))

    async def drain(self): await asyncio.wait_for(self.source.wait_for_playout(),10)
    def interrupt(self): self.source.clear_queue()

    async def ingest(self, track):
        stream = rtc.AudioStream(track,sample_rate=16000,num_channels=1,frame_size_ms=20,capacity=100)
        try:
            async for event in stream:
                # The browser can publish while knowledge/provider startup is
                # still running. Never accumulate that pre-listening audio.
                if not self.listening: continue
                try: self.queue.put_nowait(bytes(event.frame.data))
                except asyncio.QueueFull:
                    raise SpeechInputError("WebRTC input queue overflowed.") from None
        finally: await stream.aclose()


async def run_livekit_call(url, token, caller_identity, prepare, stop, update,
                           *, runner=run_voice_agent):
    room, source = rtc.Room(), rtc.AudioSource(24000,1,queue_size_ms=100)
    audio = LiveKitAudio(source)
    microphone_ready = asyncio.Event()
    tasks, readers, app = [], [], None

    @room.on("track_subscribed")
    def subscribed(track, publication, participant):
        if participant.identity != caller_identity or track.kind != rtc.TrackKind.KIND_AUDIO: return
        if readers: return
        readers.append(asyncio.create_task(audio.ingest(track)))
        microphone_ready.set()

    @room.on("participant_disconnected")
    def left(participant):
        if participant.identity == caller_identity: stop.set()

    @room.on("disconnected")
    def disconnected(*_): stop.set()

    async def agent():
        nonlocal app
        update("waiting_for_microphone")
        await asyncio.wait_for(microphone_ready.wait(),60)
        update("starting_agent")
        app, speech, config = await prepare()
        await runner(app,speech,config=config,write=app.audit.write,
            microphone_factory=lambda **_:audio,device_factory=lambda **_:audio,
            on_ready=lambda:update("connected"))

    async def input_watch():
        await microphone_ready.wait()
        await readers[0]

    try:
        await asyncio.wait_for(room.connect(url,token),20)
        track = rtc.LocalAudioTrack.create_audio_track("maya-voice",source)
        await room.local_participant.publish_track(track,rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
        tasks = [asyncio.create_task(agent()),asyncio.create_task(stop.wait()),asyncio.create_task(input_watch())]
        done,_ = await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
        for task in done: task.result()
    finally:
        for task in tasks+readers: task.cancel()
        await asyncio.gather(*(tasks+readers),return_exceptions=True)
        await room.disconnect()
        await source.aclose()
        if app is not None: app.audit.close()
