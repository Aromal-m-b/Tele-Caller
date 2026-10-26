# Tele-Caller

**A conversational voice-support pilot for founders and small support teams.**

Tele-Caller listens through Deepgram, reasons with Gemini 3 Flash, retrieves approved
knowledge with FAISS, and answers in a natural female voice. It keeps text context,
handles interruptions, repeats previous answers, and closes a call on request,
after confirmation, or after an inactivity warning.

The current reference application uses a **Sun Direct / telecom support example**.
It is a local microphone application, not a hosted multi-tenant SaaS or a public
telephone line. Adapting to another industry requires approved knowledge, a new
persona/scope policy and appropriate account tools.

## Try it on Windows

1. Download and extract this repository. Install Python 3.10–3.12.
2. Double-click **setup.bat** and enter your Gemini and Deepgram API keys in `.env`.
3. Double-click **start.bat**. A small fictional demo index is prepared on first use.

API usage is billable. Headphones reduce speaker echo. No customer database is
needed for the demo. Say “please end the call” to finish.

[Owner quickstart](docs/quickstart.md) · [Business case and cost model](docs/business-case.md)
· [Technical architecture](docs/architecture.md) · [Developer guide](docs/developer-guide.md)

## What a team can evaluate

- Reduce repeated FAQ explanations with answers grounded in approved documents.
- Keep conversational text context instead of restarting every exchange.
- Retrieve account and subscription facts through authenticated, read-only tools.
- Leave callers time to breathe: **1.5-second turn pause**, configurable per business.
- End idle calls after a **15-second audibility check, 45-second warning and 20-second grace period**.
- Model operating costs before expanding a supervised pilot.

These are capabilities and testable operational hypotheses, not measured profit
claims. At illustrative usage levels, the supplied model estimates $0.0896 API
cost/contact; actual spend depends on audio duration, synthesized text, tokens,
provider rates and retries. The business guide includes fixed costs, human fallback,
capacity-value scenarios and break-even math.

## Technical path

```text
Microphone → Deepgram Nova-3 → text dialogue + Gemini 3 Flash
                              ├─ conversational intent and reply
                              ├─ FAISS retrieval over approved knowledge
                              └─ authenticated read-only MongoDB tools
Assistant text → Deepgram Aura-2 Andromeda → speaker
```

No Gemini Live API is used. Private database result values remain local and use
safe spoken prompts. Each session keeps six recent text exchanges and a compressed,
searchable older archive in RAM. Explicit abuse checks precede model/tool access.

## Developer commands

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
python scripts/estimate_costs.py --calls 1000 --containment 0.6
python main.py --text
python main.py --speech-pause 2500 --idle-timeout 180 --idle-grace 45
```

The tests use synthetic data/mocks and do not need API keys. Optional advanced PDF
extraction dependencies are in `requirements-pdf.txt`. The repository intentionally
excludes credentials, database exports, original private/third-party corpora,
generated FAISS indexes, recordings and old experimental code. The included demo
knowledge is fictional; it is not official Sun Direct pricing or policy.

## Deployment boundary

For real customers, add trusted customer authentication, supervised escalation,
monitoring, consent/data handling and telephony if needed. Multi-business hosting
also requires tenant isolation and usage/budget controls. The CLI does not charge
accounts, renew subscriptions, make outbound phone calls or transfer to a human.

See [LICENSE](LICENSE) for the repository's MIT license.
