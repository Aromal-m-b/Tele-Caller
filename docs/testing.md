# Testing with pytest

Tele-Caller uses `pytest` as its repeatable quality gate. The suite tests the
conversation application around the language model: retrieval, prompt/context
assembly, tool arguments, privacy boundaries, database writes, audio turn
taking and call control. It does not train the model, and it does not pretend
that a mocked response is the same as a production provider response.

## Running the tests

From the repository root on Windows, the project environment used for the full
voice suite is:

```powershell
.\.venv-livekit\Scripts\python.exe -m pytest -q
```

The development environment also works for the text/RAG portion:

```powershell
python -m pytest -q
```

`pytest.ini` sets `tests` as the test root and explicitly lists the supported
test modules. `-q` keeps the output readable; `-ra` (configured in the file)
still reports skipped, expected-failure and other non-passing outcomes. A
single area can be run while developing:

```powershell
.\.venv-livekit\Scripts\python.exe -m pytest tests/test_channel_support.py -q
.\.venv-livekit\Scripts\python.exe -m pytest tests/test_gemini_live.py -q
.\.venv-livekit\Scripts\python.exe -m pytest tests/test_livekit.py tests/test_web_dialer.py -q
```

The browser smoke harness can also be run directly when Node.js is installed:

```powershell
node tests/web_dialer.cjs
```

The exact passing count changes as coverage grows. The current regression run
is 426 passed with one expected LangChain-community deprecation warning.

## What is covered

### Deterministic application and RAG behavior

The main, PDF-loader, memory/context, guardrail, speech, barge-in, call-control,
streaming and latency tests check that user turns are normalized, recent text
context is retained, retrieval is used consistently, and unrelated requests or
unsafe tool arguments are refused. They also check that interruption cancels
spoken output instead of allowing stale audio to continue.

### Customer and service workflows

Account, caller, ticket-audit, customer-service and release-tool tests verify
phone-number scoping, verified-session rules, complaint/order/service drafts,
explicit confirmation before a write, idempotency, audit records and safe call
termination. Tests use synthetic customers and never charge, dispatch or alter
a real subscription.

### Channel catalog and subscription reasoning

Channel-import tests parse the HTML `<td>` source, preserve metadata and logos,
generate deterministic IDs, retain existing IDs and fail closed for malformed
input. Membership-rebuild tests verify the documented language/HD/SD section
rules, alias remapping, link integrity and preservation of subscription terms.
Channel-support tests cover active package plus add-on unions, expired/no-plan
cases, language/genre/quality/type filters, compact summaries and alternatives.
The family-assignment tests verify that the synthetic Malayalam/English/kids/
Discovery plan is created only for the demo caller, is repeatable, has no
payment side effect and refuses unsafe duplicate or real-customer assignments.

### Provider and streaming integrations

Deepgram and Gemini Live tests use fake WebSocket/audio/model objects to verify
the protocol schema, persistent receive loops, streamed audio chunks,
interruption, cleanup, function-call dispatch and caller isolation. LiveKit,
phone and web-dialer tests exercise the transport adapters with mocked browser,
microphone, WebRTC and Twilio boundaries. This catches integration regressions
without requiring a microphone, a public tunnel or a billable API call.

## Why most tests are mocked

External voice and model calls are slow, costly and non-deterministic. A test
that depends on a live provider can fail because of network, quota, model
variation or a temporary service outage rather than because our code changed.
Mocks let the suite assert exact contracts: the tool schema, the arguments sent
to MongoDB, the data returned to the model, the order of streamed events and
the cleanup performed after a hang-up. Mongo-style integration tests use an
in-memory fixture, so query scoping and indexes are still exercised without
writing to a cloud database.

This separation also protects privacy. Test results contain synthetic records;
API keys, customer exports, transcripts and generated indexes remain local and
are ignored by Git.

## Live qualification checks

Provider smoke checks are separate from the normal gate. They are run only when
credentials and network access are intentionally available, and are read-only:

* Gemini Live returned streamed Malayalam audio from a real prompt.
* The Gemini Live channel-tool schema was accepted and audio was returned after
  a synthetic tool result.
* The live synthetic MongoDB summary for `9645405518` reported the active
  43-channel plan and Discovery variants without exposing account identifiers.
* Channel import and membership rebuild were read back after writing, with no
  broken links or duplicate package memberships.

These checks complement, rather than replace, pytest. The deterministic suite
is the fast pre-commit signal; live checks validate provider compatibility when
it is worth spending the time and API budget.

## Interpreting a failure

Start with the smallest failing module, then run the complete suite before
committing. A failure in a tool test usually indicates a contract or privacy
regression; a failure in a streaming test usually indicates ordering,
cancellation or resource-cleanup drift. Do not “fix” a failing test by weakening
the assertion unless the product contract has deliberately changed—update the
implementation and the test together so the behavior stays documented.
