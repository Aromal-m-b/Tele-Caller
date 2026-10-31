# Tele-Caller

**Give your customers someone to talk to. Give your team time to get things done.**

Tele-Caller is a working AI voice-support application for founders and small
support teams. It listens, answers from approved business knowledge, follows the
conversation, and turns customer issues into confirmed, trackable requests.

The reference product is **Maya, Tech Five's telecom support assistant**.
Run a voice demo on your computer today; use it to evaluate the workflows your
business needs before connecting a real phone line.

[Start the demo](docs/quickstart.md) · [Read the product pitch](docs/product-pitch.md)
· [Explore service workflows](docs/customer-services.md) · [Evaluate costs](docs/business-case.md)

## From a customer question to a useful outcome

Your customer explains the problem in their own words. The agent looks up what it
needs, keeps the conversation moving, and asks for confirmation before submitting
a request. Your team receives a structured record instead of having to reconstruct
the issue from memory.

| What customers need | What Tele-Caller provides | Value for your team |
| --- | --- | --- |
| An answer about services or packages | Retrieval from an approved FAISS knowledge base | Less repeated FAQ explanation |
| Help reporting a problem | Complaint drafts and confirmed MongoDB submission | A recorded issue with a request reference |
| A visit or connection change | Service booking, new-connection, relocation and cancellation requests | Clear intake for staff to review |
| Replacement equipment | Equipment order requests linked to delivery records | Consistent item, quantity and destination capture |
| An order update | Recorded location, holder/courier, ETA and delivery charges when available | Less manual status lookup |
| A conversation that flows | Streaming speech, spoken replies, interruption handling and text context | Fewer restarts and repeated explanations |
| A clear end to the call | Requested disconnection and generated inactivity check-ins | Controlled session closure |
| A record of what happened | Private transcripts, timing and usage/cost estimates | Evidence for review and improvement |

These workflows use synthetic customer data in the current demo. Filing a request
does not take a payment, dispatch an item, guarantee a visit or change a subscription.

## See the service workflow in a short demo

With the managed backend and synthetic MongoDB setup enabled:

1. Explain a TV signal or recharge-credit problem naturally.
2. Let the agent prepare a complaint using the issue you described and the saved address.
3. Hear the summary and approve submission in your own words.
4. Receive acknowledgement based on the committed database result and request reference.
5. Ask about an equipment order or delivery, then end the session.

Caller consent is interpreted by a language model, rather than a fixed list of
accepted confirmation phrases. Local checks control the actual database write.
The agent speaks naturally; this walkthrough is a demo outline, not a reply script.

[Full demo walkthrough](docs/product-pitch.md#show-it-in-five-minutes)

## Try it on Windows

1. Download this repository using **Code → Download ZIP**, then extract it.
2. Install Python 3.10–3.12 and connect a microphone and headphones.
3. Double-click **setup.bat**, enter `GEMINI_API_KEY` and `DEEPGRAM` in `.env`, and save.
4. Double-click **start.bat** for the standard voice assistant, or **start-agent.bat**
   for the managed Deepgram Voice Agent.

The first launch prepares a small fictional knowledge index if none exists.
API usage is billable. A knowledge-only demo needs no customer database.

For browser voice through **LiveKit WebRTC**, run `setup-webrtc.bat` once and then
`start-webrtc.bat`. Open the local or ngrok page and enter the private demo access
code. This uses the same Deepgram agent over browser audio, with no Twilio number.
The phone-style page includes a keypad, microphone permission/retry screen,
ringing, connected call timer and hang-up controls.
[Vercel deployment guide](docs/vercel-deployment.md): host the static dialer on
Vercel and forward its session API to the persistent backend; no provider keys
belong in the browser deployment.
See the [WebRTC guide](docs/livekit-webrtc.md). Use this route to test voice while
Twilio's trial account blocks Media Streams.

For inbound phone calls through **Twilio + ngrok + Uvicorn**, install
`requirements-phone.txt` and follow the [phone bridge setup](docs/twilio-phone.md).
It connects the managed agent to a signed bidirectional telephone audio stream.
Twilio's current Voice trial strips the required Stream instruction; use an
account with Media Streams enabled. A successful webhook response alone does
not mean the call's audio reached the agent.

For complaints, service requests, equipment orders and tracking, configure
MongoDB, a synthetic caller and the approved synthetic-data sharing option as
explained in the [service setup](docs/customer-services.md). Keep `.env`, customer
records and transcripts private.

## Choose the workflow you want to demonstrate

| Capability | Standard pipeline: `start.bat` | Managed agent: `start-agent.bat` |
| --- | --- | --- |
| Streaming voice and knowledge retrieval | Yes | Yes |
| Main conversation engine | Gemini text model | Deepgram-managed OpenAI model |
| Text context and conversational follow-ups | Application-managed history and archive | Managed call conversation; local caller context |
| Registered caller greeting | Authorized display-name personalization | Generic generated greeting; identity stays local |
| Private account/subscription tools | Verified local account access | General account/payment tools not exposed |
| Issue records | Existing confirmed ticket/DOCX workflow | Confirmed MongoDB complaints and service requests |
| Equipment orders and delivery tracking | Not the managed service workflow | Caller-scoped tools with linked records |
| Consent interpretation for service submissions | Separate ticket workflow | Gemini semantic interpretation; local commit |
| Private call logs | Transcript, reported Gemini tokens, estimated usage cost | Transcript, provider timing and connection-based estimate; hosted tokens unavailable |

No Gemini Live API is used. The managed service combines speech recognition,
reasoning and speech synthesis over one persistent connection; it also uses Gemini
for retrieval embeddings and pending-form intent interpretation.
Both the LiveKit browser route and Twilio route reuse this managed backend;
they change the audio transport, not the reasoning/service tool implementation.

## Why this matters to a small business

Founders should be able to spend less time repeating answers and collecting the
same issue details. Tele-Caller provides a concrete way to evaluate that:

- Answer documented questions through a voice conversation.
- Capture customer needs in structured requests for follow-up.
- Use saved addresses and recorded tracking facts to reduce repetitive questions.
- Review transcripts and response timing to find friction.
- Compare operating cost with the staff time actually released.

Savings and customer satisfaction must be measured in a pilot. There is no promised
profit, call-resolution rate or response-time guarantee. The [business guide](docs/business-case.md)
provides an adjustable cost model; the [product pitch](docs/product-pitch.md)
explains the outcomes to measure without assuming vendor prices or a guaranteed ROI.

## Product scope today

This repository delivers a runnable local application, Windows launchers, knowledge
retrieval, two voice backends, synthetic service workflows and developer documentation.
It currently handles one local conversation per process through a microphone and
speaker. An optional inbound Twilio bridge now supports telephone audio and the
signed caller number through ngrok/Uvicorn; the desktop mode still simulates calls.

A production phone service still requires trusted customer authentication, human
handoff, operational monitoring and data-handling controls. The Twilio bridge is
an allowlisted inbound pilot, not a deployed multi-business answering service.
Multi-business hosting also requires tenant isolation, concurrency and budget controls.
Changing industry requires adapting the knowledge, persona, scope and data model.
The Tech Five demo uses synthetic accounts and illustrative prices; verified
company policies must be supplied before production use.

## Build and extend

```text
Customer speech → streaming recognition → conversation + approved knowledge/tools
                                              ↓
                           spoken response / confirmed request / private call log
```

[Architecture](docs/architecture.md) · [Managed backend](docs/deepgram-agent.md)
· [Database model](docs/customer-services.md) · [Developer guide](docs/developer-guide.md)
· [Caller, ticket and logging setup](docs/callers-tickets-logs.md)

## Test the model safely with pytest

The project’s pytest suite is the repeatable quality gate for the model
integration. It verifies the surrounding contracts that make a voice assistant
safe and useful: RAG retrieval and context continuity, tool schemas and
confirmation gates, phone/account scoping, channel entitlement reasoning,
MongoDB writes, streaming audio, barge-in cancellation and call cleanup. It
also tests the HTML channel import and synthetic package rebuild, including
deterministic IDs and link integrity.

Run the full regression suite with the voice environment:

```powershell
.\.venv-livekit\Scripts\python.exe -m pytest -q
```

The tests use fake model/provider streams and in-memory database fixtures for
fast, deterministic, non-billable checks. They do not train the model; they
catch malformed model tool calls, stale subscription data, privacy leaks and
audio lifecycle regressions before a live call. Provider qualification checks
are kept separate because real Gemini/Deepgram calls are slower, variable and
billable. See [Testing with pytest](docs/testing.md) for the complete test map,
focused commands, current result and live-check procedure.

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
python main.py --text
python main.py --backend deepgram-agent
python scripts/estimate_costs.py --calls 1000 --containment 0.6
```

Tests use synthetic fixtures and mocks; they do not require API keys. Optional
advanced PDF extraction dependencies are in `requirements-pdf.txt`. Credentials,
private corpora, database exports, generated indexes and call logs are excluded
from the repository. Included demo policies and prices are fictional.

Released under the [MIT license](LICENSE).
# Optional Malayalam voice demo

Use [Gemini Live](docs/gemini-live.md) with the existing browser dialer by running
`start-gemini-live.bat`. This separate native audio demo leaves the Deepgram
customer-service backend available through `start-webrtc.bat`.

Both voice backends support [missing-channel diagnosis](docs/channel-support.md):
check active packages and add-ons, explain eligibility, and compare illustrative
individual-channel and package options without making a payment or changing plans.

[Import channel tables from HTML](docs/channel-import.md) while preserving original
TD values, source metadata and existing package/subscription channel links.

[Subscription summaries](docs/subscription-summary.md) give callers their actual
plan names and channel counts, with language, genre and HD/SD drill-down on request.
