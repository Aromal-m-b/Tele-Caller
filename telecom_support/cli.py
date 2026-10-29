import argparse
import asyncio
import os

from .settings import DEFAULT_URL, Settings


def database_service(demo_customer=None, disabled=False):
    from .database import MongoSettings, connect
    from .account_tools import AccountTools, CustomerIdentity
    settings = MongoSettings.load()
    if disabled:
        if demo_customer:
            raise ValueError("--demo-customer requires database tools.")
        return None, None
    if not settings.uri:
        if demo_customer:
            raise ValueError("--demo-customer requires MONGODB_URI.")
        return None, None
    client, database = connect(settings)
    identity = CustomerIdentity(demo_customer, verified=True, synthetic_demo=True) if demo_customer else None
    service = AccountTools(database, identity)
    if demo_customer:
        try:
            service._customer()  # Reject non-synthetic records before any account access.
        except Exception:
            client.close()
            raise ValueError("Demo customer is missing, duplicated, not SYNTHETIC, or the database is unavailable.") from None
    return service, client


def run_menu(application, *, read=input, write=print):
    """Iterative menu: repeated back/invalid choices cannot grow the call stack."""
    while True:
        write("\n[1] Ask a question\n[2] Exit")
        try:
            choice = read("Enter your choice: ").strip()
            if choice == "2":
                write("Goodbye!")
                return
            if choice != "1":
                write("Invalid choice")
                continue
            write("Type 'x' to return to the menu.")
            while True:
                question = read("Q: ").strip()
                if question.lower() == "x":
                    break
                if question:
                    write("A: " + application.generate(question))
        except (EOFError, KeyboardInterrupt):
            write("\nGoodbye!")
            return


def main(argv=None):
    parser = argparse.ArgumentParser(description="Customer support knowledge base")
    parser.add_argument("--backend", choices=["pipeline", "deepgram-agent"], default="pipeline",
        help="Voice backend: existing Gemini pipeline or managed Deepgram Voice Agent (public RAG)")
    parser.add_argument("--source", choices=["web", "index"], default="index")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--index-dir", help="Trusted local FAISS index directory")
    input_mode = parser.add_mutually_exclusive_group()
    input_mode.add_argument("--question", help="Answer one typed question and exit")
    input_mode.add_argument("--text", action="store_true", help="Use the typed menu instead of microphone input")
    parser.add_argument("--input-device", type=int, help="PyAudio microphone device index (default: system input)")
    parser.add_argument("--stt-language", help="Deepgram transcription language (default: en-US)")
    parser.add_argument("--silent", action="store_true", help="Microphone input with text-only replies")
    parser.add_argument("--tts-voice", help="Deepgram voice (default: aura-2-andromeda-en, conversational female English)")
    parser.add_argument("--output-device", type=int, help="PyAudio speaker device index (default: system output)")
    parser.add_argument("--idle-timeout", type=float, help="Seconds of caller inactivity before final warning (default: 45; 0 disables)")
    parser.add_argument("--idle-grace", type=float, help="Seconds to wait after the final idle warning (default: 20)")
    parser.add_argument("--idle-checkin", type=float, help="Seconds before a friendly audibility check (default: 15; 0 disables)")
    parser.add_argument("--speech-pause", type=int, help="Silence in milliseconds before a spoken turn ends (default: 1100)")
    parser.add_argument("--demo-customer", help="Trusted operator selection of a SYNTHETIC customer fixture; not production authentication")
    parser.add_argument("--caller-number", help="Simulated caller; defaults to CALLER_PHONE in .env")
    parser.add_argument("--no-database", action="store_true", help="Disable MongoDB tools")
    args = parser.parse_args(argv)
    if args.backend == "deepgram-agent" and (args.text or args.question is not None or args.silent or args.demo_customer):
        parser.error("deepgram-agent is a microphone/public-RAG backend; omit --text, --question, --silent and --demo-customer.")
    if args.backend == "deepgram-agent" and args.speech_pause is not None:
        parser.error("--speech-pause applies to the pipeline backend; Deepgram Voice Agent manages its own turn detection.")
    from .rag import build_application
    application, client, audit = None, None, None
    try:
        speech_settings = None
        if args.question is None and not args.text:
            from .speech_input import SpeechSettings
            speech_settings = SpeechSettings.load(language=args.stt_language, device=args.input_device,
                voice=args.tts_voice, output_device=args.output_device, audio_output=not args.silent,
                idle_timeout=args.idle_timeout, idle_grace=args.idle_grace, endpointing_ms=args.speech_pause,
                idle_checkin=args.idle_checkin)
        service, client = database_service(args.demo_customer, args.no_database or args.backend == "deepgram-agent")
        settings = Settings.load()
        from .call_audit import CallAudit
        from .caller import CallerSession
        from .tickets import TicketService
        from .settings import ROOT
        caller_phone = args.caller_number or os.getenv("CALLER_PHONE") or "0000000000"
        agent_config = None
        model_label = settings.model
        if args.backend == "deepgram-agent":
            from .deepgram_agent import AgentConfig
            agent_config = AgentConfig.load()
            model_label = "deepgram-agent:" + agent_config.provider + "/" + agent_config.model
        audit = CallAudit(ROOT / "output" / "calls", caller=caller_phone, model=model_label)
        audit.mode = "text" if args.text or args.question is not None else "voice"
        caller = CallerSession(service, caller_phone, call_id=audit.call_id, audit=audit)
        if service and args.demo_customer:
            from .account_tools import CustomerIdentity
            service.identity = CustomerIdentity(args.demo_customer, verified=True, synthetic_demo=True)
            caller.bind(service._customer())
        if service:
            service.caller_session = caller
            service.ticket_service = TicketService(service, os.getenv("SUPPORT_TICKET_TEMPLATE", ""), ROOT / "output" / "tickets", call_id=audit.call_id)
        application = build_application(settings, source=args.source,
                                        url=args.url, index_dir=args.index_dir,
                                        account_service=service, database_client=client, audit=audit)
        application.caller_session, application.audit = caller, audit
        if args.question is None:
            print("Incoming simulated call: " + caller.phone + (" (registered synthetic customer)" if caller.customer else " (guest)"))
            print("Transcript and usage log: " + str(audit.path))
        if args.question is not None:
            print(application.generate(args.question))
        else:
            print("Voice backend: " + args.backend)
            print("Text model: " + model_label)
            print("Knowledge base: " + (str(args.index_dir or Settings.index_dir)
                                          if args.source == "index" else args.url))
            if service:
                print("Database tools: synthetic dataset, illustrative prices. "
                      + ("Demo customer session selected." if args.demo_customer else "Account details require a verified session."))
            if args.text:
                run_menu(application)
            else:
                from .speech_input import run_microphone
                try:
                    def write(line):
                        if line.startswith("You: ") and caller.stage:
                            audit.redact(line[5:])
                        audit.write(line)
                    if args.backend == "deepgram-agent":
                        from .deepgram_agent import run_voice_agent
                        print("Managed agent: public knowledge only; private account/ticket tools are available in --backend pipeline.")
                        asyncio.run(run_voice_agent(application, speech_settings, config=agent_config, write=write))
                    else:
                        asyncio.run(run_microphone(application, speech_settings, write=write))
                except KeyboardInterrupt:
                    print("\nStopped listening.")
    except RuntimeError as exc:
        # Speech errors intentionally contain no credentials or raw provider payload.
        from .speech_input import SpeechInputError
        from .speech_output import SpeechOutputError
        if not isinstance(exc, (SpeechInputError, SpeechOutputError)):
            raise
        parser.exit(1, f"Audio setup failed: {exc}\n")
    except (ValueError, FileNotFoundError) as exc:
        parser.exit(1, f"Startup failed: {exc}\n")
    finally:
        try:
            if audit is not None: audit.close()
        finally:
            if application is not None and callable(getattr(application, "close", None)):
                application.close()
            elif client is not None:
                client.close()
