# Managed Deepgram Voice Agent backend

Run with your existing environment:

```powershell
python main.py --backend deepgram-agent
```

Or double-click `start-agent.bat` after the normal `setup.bat`. The regular
`start.bat` and `python main.py` retain the existing Gemini pipeline.

For an inbound Twilio call instead of desktop audio, use the separate
[Uvicorn/ngrok phone bridge](twilio-phone.md). It reuses this backend through
network audio adapters without opening a local microphone or speaker.

This adapts the supplied pseudocode to the current Voice Agent protocol. One
connection to `wss://agent.deepgram.com/v1/agent/converse` carries microphone PCM,
control messages, function calls, transcript events and streamed reply PCM. The
client waits for `Welcome`, sends `Settings`, and waits for `SettingsApplied`
before opening audio. Function results use `content`, not the older `output` field.
Turn detection is managed by the Agent API; the pipeline's `--speech-pause` flag is
rejected rather than silently ignored in this mode.

The default uses Nova-3, Deepgram-managed OpenAI `gpt-4o-mini`, and Aura-2 Andromeda.
This is a managed STT/LLM/TTS service, not a standalone Deepgram reasoning model.
Only the existing `DEEPGRAM` (or `DEEPGRAM_API_KEY`) is used to authenticate it;
no OpenAI key is sent. Gemini embeddings still use `GEMINI_API_KEY` for local FAISS
queries. Settings contain no API keys or database customer values.

`DEEPGRAM_AGENT_PROVIDER` and `DEEPGRAM_AGENT_MODEL` select another supported
managed provider/model. Deepgram's own thinking models require Early Access;
choosing an unavailable model fails explicitly. No provider silently falls back.

The backend generates its first introduction from a synthetic call-connected
message. Greetings are generic: registered names are not exported to the new
hosted thinker. Synthetic control notices are distinguished from caller speech
in local logs. The socket stays open across turns; silence sends `KeepAlive` when
microphone frames are absent.

## Retrieval and scope

`query_rag` searches the configured trusted FAISS index, returning at most three
public document excerpts and 8,000 characters to the hosted agent. It validates a
fixed argument schema and scope before retrieval. Its task runs separately from
socket receiving and audio playback. Cancelled calls never send late results;
uninterruptible embedding reads finish during cleanup. At most two lookups run
concurrently, with bounded outstanding calls and a per-session rate limit.

The prompt retains the Tech Five support scope. Local guards restrict retrieval
and screen returned assistant text before allowing its audio. Unlike the custom
pipeline, cloud input reaches the managed agent before the local client receives
its transcript, so the client cannot enforce a pre-LLM input guard. Incremental
output screening also cannot retract speech already played. This mode is an
opt-in support pilot, not equivalent to a guaranteed policy enforcement
boundary around a hosted model.

With `MONGODB_URI` configured, the backend also enables caller-scoped complaint,
service-request, equipment-order and delivery tools. It creates three missing
collections and indexes and resolves the simulated caller locally. Submission
requires a completed draft question and later real caller confirmation; there is
no model-exposed commit function. `--no-database` disables these tools.
See the [service data model and workflow](customer-services.md).

General private account/payment lookup, username/smartcard verification and the
older DOCX ticket flow remain in `--backend pipeline`. The managed agent uses only
its declared FAISS, call-control and service tools; retrieved documents are data,
not instructions. The example shop return/shipping/pricing strings from the
pseudocode are not installed as Tech Five knowledge.

## Playback, interruption and hang-up

Microphone capture is asynchronous. Received 24 kHz mono PCM enters a bounded
playback queue and is written in 20 ms frames; receiving does not wait for an
entire answer to play. `UserStartedSpeaking` clears queued audio and abandons
previous function tasks. Headphones are recommended; this mode relies on the
managed agent's speech detection and does not add full local echo cancellation.

Local call control authorizes disconnection from an explicit caller request or a
later confirmation to a completed hang-up question. The model cannot authorize
`end_call` by calling it. The question may span multiple transcript events. A
goodbye is drained to the speaker before the local session closes, with a bounded
fallback if the provider never finishes it. Idle checks, a warning and a final
goodbye use generated notices and the existing configurable idle timings.

## Logs, timing and cost

Each call uses the existing private `output/calls/*.txt` log. Local first-PCM timing
and the provider's optional `LatencyReport` fields are printed and logged. Provider
reports may arrive in several partial messages; values are seconds and are not a
measurement of physical sound leaving the speaker.

Managed LLM token counts are recorded as **unavailable (`null`)**, never as zero.
The cost estimate uses actual socket connection time and
`COST_DEEPGRAM_AGENT_PER_MINUTE`. The default model's Standard pay-as-you-go planning
rate is USD 0.075/minute; review the rate for a different model/tier. Separate
STT/TTS/model charges are not added again. Query embedding costs remain excluded
and clearly identified as unavailable. This is not a provider invoice.

A live public greeting probe reached first PCM 0.98 seconds after text injection,
excluding microphone/STT and physical playback. This single sample is not an
end-to-end latency guarantee.
A separate live public recharge question completed a `query_rag` round trip against
the existing FAISS index and returned streamed PCM; first audio reached the sink
in 3.31 seconds. The hosted provider reported about 3.09 seconds to first audio.
These clocks have different reference points and neither includes a microphone
utterance. Headless tests need KeepAlive frames while no audio is being sent.

References: [Voice Agent API](https://developers.deepgram.com/reference/voice-agent/voice-agent),
[function responses](https://developers.deepgram.com/docs/voice-agent-function-call-response),
[managed models](https://developers.deepgram.com/docs/voice-agent-llm-models),
[pricing](https://deepgram.com/pricing).
