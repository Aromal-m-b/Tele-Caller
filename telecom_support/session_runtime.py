"""Shared knowledge runtime with isolated identity, form and audit state per call."""
import asyncio
import os
from types import SimpleNamespace
from .settings import ROOT


class SessionRuntime:
    """Share a loaded retriever; allocate identity, forms and audit per call."""
    def __init__(self, transport="verified inbound Twilio Media Stream", mode="phone"):
        self.transport = transport
        self.mode = mode
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
            audit.mode = self.mode
            audit.event("Transport: " + self.transport + "; synthetic account tools only.")
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
