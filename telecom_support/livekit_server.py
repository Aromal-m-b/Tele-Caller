"""Authenticated browser sessions: LiveKit WebRTC to Deepgram Voice Agent."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
import hmac
import logging
import os
from pathlib import Path
import secrets
import time
from urllib.parse import urlsplit

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from livekit import api

from .caller import phone_number
from .session_runtime import SessionRuntime
from .settings import ROOT

logger = logging.getLogger(__name__)
WEB = Path(__file__).parent / "web"


@dataclass(frozen=True)
class WebRTCConfig:
    url: str
    api_key: str = field(repr=False)
    api_secret: str = field(repr=False)
    access_code: str = field(repr=False)
    caller_phone: str = field(repr=False)
    max_calls: int = 1

    def __post_init__(self):
        parsed = urlsplit(self.url)
        if (parsed.scheme != "wss" or not parsed.netloc or parsed.username or parsed.password
                or parsed.query or parsed.fragment): raise ValueError("Set a valid LIVEKIT_URL using wss://.")
        if not self.api_key or not self.api_secret or len(self.access_code)<16:
            raise ValueError("Set LiveKit credentials and WEBRTC_ACCESS_CODE (at least 16 characters).")
        phone_number(self.caller_phone)
        if not 1<=self.max_calls<=10: raise ValueError("WEBRTC_MAX_CALLS must be 1 through 10.")

    @classmethod
    def load(cls):
        load_dotenv(ROOT / ".env")
        return cls(os.getenv("LIVEKIT_URL",""),os.getenv("LIVEKIT_API_KEY",""),
            os.getenv("LIVEKIT_API_SECRET",""),os.getenv("WEBRTC_ACCESS_CODE",""),
            os.getenv("CALLER_PHONE",""),int(os.getenv("WEBRTC_MAX_CALLS","1")))

    def token(self, room, identity):
        return (api.AccessToken(self.api_key,self.api_secret).with_identity(identity)
            .with_ttl(timedelta(minutes=2)).with_grants(api.VideoGrants(
                room_join=True,room=room,can_publish=True,can_subscribe=True,
                can_publish_data=False,can_publish_sources=["microphone"],
                can_update_own_metadata=False)).to_jwt())


class BrowserSessions:
    def __init__(self, config, runtime, runner=None, delete_room=None):
        self.config,self.runtime,self.runner = config,runtime,runner
        self.delete_room = delete_room
        self.sessions = {}

    def start(self):
        # Keep recent status briefly, without retaining transcripts in HTTP state.
        self.sessions = {k:v for k,v in self.sessions.items()
            if v["status"] not in {"ended","error"} or time.monotonic()-v["created"]<600}
        active = [v for v in self.sessions.values() if v["status"] not in {"ended","error"}]
        if len(active)>=self.config.max_calls: raise HTTPException(409,"A call is already active. End it before starting another.")
        if len(self.sessions)>=30:
            old=next((k for k,v in self.sessions.items() if v["status"] in {"ended","error"}),None)
            if old: self.sessions.pop(old)
        sid=secrets.token_urlsafe(24)
        room="telecaller-"+secrets.token_hex(12)
        caller="caller-"+secrets.token_hex(8)
        agent="maya-"+secrets.token_hex(8)
        browser_token=self.config.token(room,caller)
        agent_token=self.config.token(room,agent)
        row=dict(room=room,caller=caller,status="connecting",stop=asyncio.Event(),created=time.monotonic())
        self.sessions[sid]=row
        row["task"]=asyncio.create_task(self.run(row,agent_token))
        return dict(session_id=sid,url=self.config.url,token=browser_token)

    async def run(self,row,token):
        def update(status): row["status"]=status
        try:
            from .livekit_audio import run_livekit_call
            runner=self.runner or run_livekit_call
            await asyncio.wait_for(runner(self.config.url,token,row["caller"],
                lambda:self.runtime.prepare(self.config.caller_phone),row["stop"],update),900)
            row["status"]="ended"
        except asyncio.CancelledError:
            row["status"]="ended"
            raise
        except Exception as error:
            row["status"]="error"
            logger.warning("WebRTC session ended (%s); private provider payload omitted.",type(error).__name__)
            from .speech_input import SpeechInputError
            if isinstance(error,SpeechInputError):
                logger.warning("Voice transport diagnostic: %s",str(error))
        finally:
            try:
                if self.delete_room: await self.delete_room(row["room"])
                else:
                    async with api.LiveKitAPI(self.config.url,self.config.api_key,self.config.api_secret) as client:
                        await client.room.delete_room(api.DeleteRoomRequest(room=row["room"]))
            except Exception as error:
                logger.warning("WebRTC room cleanup unavailable (%s).",type(error).__name__)

    async def end(self,sid):
        row=self.sessions.get(sid)
        if row is None: raise HTTPException(404,"Session not found.")
        row["stop"].set()
        await asyncio.gather(row["task"],return_exceptions=True)

    async def close(self):
        for row in self.sessions.values():
            row["stop"].set()
            row["task"].cancel()
        await asyncio.gather(*(row["task"] for row in self.sessions.values()),return_exceptions=True)


def create_app(*,config=None,runtime=None,runner=None,delete_room=None):
    runtime=runtime or SessionRuntime("LiveKit WebRTC browser audio",mode="webrtc")
    @asynccontextmanager
    async def lifespan(app):
        try: yield
        finally:
            if app.state.sessions: await app.state.sessions.close()
            runtime.close()
    app=FastAPI(title="Tele-Caller WebRTC",lifespan=lifespan)
    app.state.sessions=None
    app.state.config=config
    app.mount("/static",StaticFiles(directory=WEB / "static"),name="static")

    def settings():
        if app.state.config is None:
            try: app.state.config=WebRTCConfig.load()
            except ValueError: raise HTTPException(503,"Configure LiveKit, CALLER_PHONE and WEBRTC_ACCESS_CODE in .env.") from None
        return app.state.config

    def authorize(request):
        cfg=settings()
        code=request.headers.get("X-Call-Access","")
        if not code or not hmac.compare_digest(code.encode(),cfg.access_code.encode()):
            raise HTTPException(401,"Enter the demo access code from your local .env file.")
        return cfg

    @app.get("/")
    async def index(): return FileResponse(WEB / "index.html",headers={"Cache-Control":"no-store"})

    @app.get("/health")
    async def health():
        settings()
        return {"status":"online","transport":"livekit-webrtc","backend":"deepgram-agent"}

    @app.post("/api/sessions")
    async def start(request:Request):
        cfg=authorize(request)
        if app.state.sessions is None:
            app.state.sessions=BrowserSessions(cfg,runtime,runner,delete_room)
        return JSONResponse(app.state.sessions.start(),headers={"Cache-Control":"no-store"})

    @app.get("/api/sessions/{sid}")
    async def status(sid:str,request:Request):
        authorize(request)
        row=app.state.sessions.sessions.get(sid) if app.state.sessions else None
        if row is None: raise HTTPException(404)
        return JSONResponse({"status":row["status"]},headers={"Cache-Control":"no-store"})

    @app.delete("/api/sessions/{sid}")
    async def end(sid:str,request:Request):
        authorize(request)
        if app.state.sessions is None: raise HTTPException(404)
        await app.state.sessions.end(sid)
        return {"status":"ended"}

    return app


app=create_app()
