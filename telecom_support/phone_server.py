"""Signed Twilio inbound-call webhooks and bidirectional audio via Uvicorn."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import logging
import os
import re
import secrets
import time
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlsplit

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from starlette.datastructures import FormData
from starlette.websockets import WebSocketState
from twilio.request_validator import RequestValidator
from twilio.twiml.voice_response import VoiceResponse, Connect, Stream
import httpx

from .caller import phone_number
from .settings import ROOT

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PhoneConfig:
    public_url: str
    account_sid: str
    auth_token: str = field(repr=False)
    allowed_callers: frozenset = field(default_factory=frozenset, repr=False)
    max_calls: int = 1
    verify_unsigned_calls: bool = False

    def __post_init__(self):
        url = urlsplit(self.public_url)
        if (url.scheme != "https" or not url.netloc or url.username or url.password
                or url.path not in {"", "/"} or url.query or url.fragment):
            raise ValueError("PUBLIC_BASE_URL must be an HTTPS origin without a path.")
        if not re.fullmatch(r"AC[0-9a-fA-F]{32}", self.account_sid) or not self.auth_token:
            raise ValueError("Set TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN.")
        if not self.allowed_callers or not 1 <= self.max_calls <= 10:
            raise ValueError("Set TWILIO_ALLOWED_CALLERS (or CALLER_PHONE) and a call limit from 1 to 10.")

    @classmethod
    def load(cls):
        load_dotenv(ROOT / ".env")
        callers = os.getenv("TWILIO_ALLOWED_CALLERS") or os.getenv("CALLER_PHONE", "")
        return cls(os.getenv("PUBLIC_BASE_URL", "").rstrip("/"),
            os.getenv("TWILIO_ACCOUNT_SID", ""), os.getenv("TWILIO_AUTH_TOKEN", ""),
            frozenset(phone_number(value.strip()) for value in callers.split(",") if value.strip()),
            int(os.getenv("TWILIO_MAX_CALLS", "1")),
            os.getenv("TWILIO_VERIFY_UNSIGNED_CALLS", "false").casefold() == "true")

    @property
    def media_url(self): return "wss://" + urlsplit(self.public_url).netloc + "/media-stream"


class CallTickets:
    """Short-lived, single-use binding from a signed HTTP call to its stream."""
    def __init__(self, limit, clock=time.monotonic):
        self.limit, self.clock = limit, clock
        self.pending, self.active = {}, set()

    def issue(self, call_sid, caller):
        self.pending = {k:v for k,v in self.pending.items() if v["expires"] > self.clock()}
        for token, row in self.pending.items():
            if row["call_sid"] == call_sid: return token
        if call_sid in self.active or len(self.pending) + len(self.active) >= self.limit:
            raise HTTPException(503, "Call capacity reached.")
        token = secrets.token_urlsafe(32)
        self.pending[token] = dict(call_sid=call_sid, caller=caller, expires=self.clock()+90)
        return token

    def consume(self, token, call_sid):
        if not isinstance(token, str): return None
        row = self.pending.get(token)
        if not row or row["expires"] <= self.clock() or row["call_sid"] != call_sid: return None
        self.pending.pop(token)
        self.active.add(call_sid)
        return row

    def available(self, token):
        return isinstance(token,str) and token in self.pending and self.pending[token]["expires"] > self.clock()


async def verify_live_call(cfg, params):
    """Alternate proof for an unsigned callback: verify a current call at Twilio.

    The submitted phone and SID are not proof themselves. Only Twilio's
    authenticated, recent, active inbound record can authorize this path.
    """
    try:
        url = f"https://api.twilio.com/2010-04-01/Accounts/{cfg.account_sid}/Calls/{params['CallSid']}.json"
        async with httpx.AsyncClient(timeout=5, follow_redirects=False) as client:
            response = await client.get(url, auth=(cfg.account_sid,cfg.auth_token))
        if response.status_code != 200: return False
        row = response.json()
        age = (datetime.now(timezone.utc)-parsedate_to_datetime(row["date_created"])).total_seconds()
        return (row.get("account_sid") == cfg.account_sid and row.get("sid") == params["CallSid"]
            and row.get("direction") == "inbound" and row.get("status") in {"ringing","in-progress"}
            and row.get("from") == params.get("From") and row.get("to") == params.get("To")
            and -10 <= age <= 120)
    except Exception as error:
        logger.warning("Live call verification unavailable (%s).",type(error).__name__)
        return False


class PhoneRuntime:
    """Share a loaded retriever; allocate identity, forms and audit per call."""
    def __init__(self):
        self.lock = asyncio.Lock()
        self.base = self.client = self.database = None

    def load(self):
        from .cli import database_service
        from .rag import build_application
        from .settings import Settings
        from .service_schema import ensure_service_schema
        service, client = database_service()
        try:
            if service: ensure_service_schema(service.db)
            self.base = build_application(Settings.load(), source="index")
            self.database, self.client = service.db if service else None, client
        except BaseException:
            if client is not None: client.close()
            raise

    async def prepare(self, caller_phone):
        async with self.lock:
            if self.base is None:
                task = asyncio.create_task(asyncio.to_thread(self.load))
                try: await asyncio.shield(task)
                except asyncio.CancelledError:
                    await asyncio.gather(task, return_exceptions=True)
                    raise
        task = asyncio.create_task(asyncio.to_thread(self.new_call, caller_phone))
        try: return await asyncio.shield(task)
        except asyncio.CancelledError:
            result = await asyncio.gather(task, return_exceptions=True)
            if not isinstance(result[0], BaseException): result[0][0].audit.close()
            raise

    def new_call(self, caller_phone):
        from .account_tools import AccountTools
        from .call_audit import CallAudit
        from .caller import CallerSession
        from .conversation import ConversationResponder
        from .customer_services import CustomerServices
        from .deepgram_agent import AgentConfig
        from .guardrails import SupportScopeGuard
        from .speech_input import SpeechSettings
        config, speech = AgentConfig.load(), SpeechSettings.load(audio_output=True)
        audit = CallAudit(ROOT / "output" / "calls", caller=caller_phone,
            model="deepgram-agent:" + config.provider + "/" + config.model)
        try:
            audit.mode = "phone"
            audit.event("Transport: verified inbound Twilio Media Stream; synthetic account tools only.")
            service = AccountTools(self.database) if self.database is not None else None
            caller = CallerSession(service, caller_phone, call_id=audit.call_id, audit=audit, allow_suspended=True)
            services = CustomerServices(service, caller, call_id=audit.call_id,
                share_details=os.getenv("AGENT_SHARE_SYNTHETIC_SERVICE_DETAILS", "false").casefold() in {"1","true","yes"}) if service else None
            conversation = ConversationResponder(self.base.model.with_config(callbacks=[audit]), SupportScopeGuard())
            app = SimpleNamespace(retriever=self.base.retriever, customer_services=services,
                caller_session=caller, conversation=conversation, audit=audit)
            return app, speech, config
        except BaseException:
            audit.close()
            raise

    def close(self):
        if self.client is not None: self.client.close()


def create_app(*, config=None, runtime=None, phone_runner=None, call_verifier=None):
    runtime = runtime or PhoneRuntime()

    @asynccontextmanager
    async def lifespan(app):
        try: yield
        finally: runtime.close()

    app = FastAPI(title="Tele-Caller phone bridge", lifespan=lifespan)
    app.state.phone_config = config
    app.state.tickets = CallTickets(config.max_calls if config else 1)

    def settings():
        if app.state.phone_config is None:
            try: app.state.phone_config = PhoneConfig.load()
            except ValueError:
                raise HTTPException(503, "Configure PUBLIC_BASE_URL, Twilio credentials and allowed pilot callers in .env.") from None
            app.state.tickets.limit = app.state.phone_config.max_calls
        return app.state.phone_config

    @app.get("/")
    async def index():
        return {"service": "Tele-Caller", "voice_webhook": "/voice", "transport": "Twilio Media Streams"}

    @app.get("/health")
    async def health():
        settings()
        return {"status": "online", "knowledge_loaded": runtime.base is not None}

    @app.post("/voice")
    async def voice(request: Request):
        cfg = settings()
        if request.headers.get("content-type", "").split(";")[0] != "application/x-www-form-urlencoded":
            raise HTTPException(415, "Expected Twilio form data.")
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > 16384: raise HTTPException(413)
            body.extend(chunk)
        try: params = FormData(parse_qsl(body.decode("utf-8"), keep_blank_values=True, max_num_fields=100))
        except (ValueError, UnicodeError): raise HTTPException(400) from None
        signed_url = cfg.public_url.rstrip("/") + "/voice"
        signature = request.headers.get("X-Twilio-Signature", "")
        if request.url.query or (signature and not RequestValidator(cfg.auth_token).validate(signed_url, params, signature)):
            logger.warning("Phone webhook rejected: invalid_signature.")
            raise HTTPException(403, "Invalid Twilio signature.")
        if params.get("AccountSid") != cfg.account_sid or not re.fullmatch(r"CA[0-9a-fA-F]{32}", params.get("CallSid", "")):
            raise HTTPException(403)
        try: caller = phone_number(params.get("From", ""))
        except ValueError: raise HTTPException(403) from None
        if caller not in cfg.allowed_callers: raise HTTPException(403, "Caller is not enabled for this pilot.")
        unsigned = not signature
        if unsigned:
            if not cfg.verify_unsigned_calls:
                logger.warning("Phone webhook rejected: missing_signature.")
                raise HTTPException(403, "Twilio signature is missing.")
            verifier = call_verifier or verify_live_call
            if not await verifier(cfg,params):
                logger.warning("Phone webhook rejected: live_call_not_verified.")
                raise HTTPException(403, "Could not verify an active inbound call with Twilio.")
            logger.info("Unsigned callback verified against Twilio's live call record.")
        token = app.state.tickets.issue(params["CallSid"], caller)
        response, connect = VoiceResponse(), Connect()
        stream = Stream(url=cfg.media_url+"/"+token if cfg.verify_unsigned_calls else cfg.media_url)
        stream.parameter(name="session_token", value=token)
        connect.append(stream)
        response.append(connect)
        response.hangup()
        return Response(str(response), media_type="application/xml")

    @app.websocket("/media-stream")
    @app.websocket("/media-stream/{ticket}")
    async def media(socket: WebSocket, ticket: str = ""):
        cfg, call_sid = None, None
        try:
            cfg = settings()
            validator = RequestValidator(cfg.auth_token)
            signature = socket.headers.get("X-Twilio-Signature", "")
            media_url = cfg.media_url + ("/"+ticket if ticket else "")
            signed = bool(signature) and any(validator.validate(url, {}, signature) for url in (media_url,media_url+"/"))
            verified_ticket = (not signature and cfg.verify_unsigned_calls and app.state.tickets.available(ticket))
            if socket.url.query or not (signed or verified_ticket):
                await socket.close(code=1008)
                return
            await socket.accept()
            connected = await asyncio.wait_for(socket.receive_json(), 10)
            if connected.get("event") != "connected" or connected.get("protocol") != "Call": raise ValueError()
            packet = await asyncio.wait_for(socket.receive_json(), 10)
            start = packet.get("start", {})
            if (packet.get("event") != "start" or start.get("accountSid") != cfg.account_sid
                    or start.get("mediaFormat") != {"encoding":"audio/x-mulaw","sampleRate":8000,"channels":1}
                    or not re.fullmatch(r"MZ[0-9a-fA-F]{32}", start.get("streamSid", ""))
                    or packet.get("streamSid") != start.get("streamSid")):
                raise ValueError()
            candidate = start.get("callSid")
            parameter_token = start.get("customParameters", {}).get("session_token")
            if ticket and ticket != parameter_token: raise ValueError()
            row = app.state.tickets.consume(parameter_token, candidate)
            if row is None: raise ValueError()
            call_sid = candidate
            from .phone_audio import run_phone_call
            runner = phone_runner or run_phone_call
            await runner(socket, start, lambda:runtime.prepare(row["caller"]))
        except (WebSocketDisconnect, asyncio.CancelledError):
            pass
        except Exception as error:
            logger.warning("Phone session stopped (%s); private payload omitted.", type(error).__name__)
        finally:
            if call_sid is not None: app.state.tickets.active.discard(call_sid)
            if socket.application_state != WebSocketState.DISCONNECTED:
                try: await socket.close(code=1000)
                except (RuntimeError, WebSocketDisconnect): pass

    return app


app = create_app()
