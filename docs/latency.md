# Reply latency

The response path includes caller pause detection, retrieval where needed, Gemini
generation and Deepgram synthesis. Increasing speaking rate alone would not reduce
the silence before an answer.

The default end-of-turn pause is now 1100 ms, balanced between the earlier 500 ms
cut-offs and the slower 1500 ms setting. Use `--speech-pause 1500` for longer
breathing pauses, or `--speech-pause 800` for faster turn-taking when appropriate.
Existing `.env` overrides are respected.

Known account-only lookups, ticket drafts and pending confirmations bypass FAISS
query embeddings. They still use the existing scoped tool/identity checks. Public
procedures and troubleshooting continue to retrieve their reference documents.
Replies normally focus on the next useful step instead of every option at once.
An LLM-generated hang-up confirmation question no longer needs a second LLM call
just to ask the same question again.

The TTS HTTP client keeps idle pooled connections for up to 120 seconds instead of
the default five, avoiding reconnection where the provider/network permits reuse.
The last completed spoken reply is cached in session RAM, up to 2 MiB. An exact
repeat replays that audio without a new synthesis request or charge. Interrupted
or failed synthesis is not cached as a complete answer. Changed replies still use
Deepgram; this is audio reuse, not a canned response selector. The cache is cleared
on session close and no audio file is saved.

Call logs now include backend reply time and time until the first PCM audio is
handed to the output device. That is not a measurement of physical speaker latency.
Network/model/provider delays still vary. Live baseline samples measured about
1.6–2.9 seconds to text plus about 1.6 seconds to the first audio on fresh HTTP
connections, excluding caller endpointing. Connection probes also encountered
timeouts, so no fixed speedup or sub-second fresh-response guarantee is claimed.
Compare timings from actual calls after restarting the application.

The newer implementation opens a reusable Aura WebSocket before the greeting,
with HTTP fallback, and defaults to Gemini 3.8 Flash with low thinking. Its public
support prompt is shorter. A warm TTS-only probe reached first PCM in 0.31 seconds,
but full public RAG/model/TTS probes still took about 4.1-4.6 seconds before first
PCM, excluding STT endpointing. The overall waiting-time issue remains measurable;
the transport improvement alone does not solve model/retrieval startup time.

The output safety screen, personal-data restrictions, local confirmation checks
and read-only account behavior remain enforced. Public procedural questions now
support screened sentence streaming; account/tool replies and conversational control
intentions remain buffered. Ordinary conversational reply text also streams. See
[streaming behavior and limits](streaming.md). For streamed
answers, audio cache reuse applies to the last completed sentence, not the whole reply.

## Per-turn timer

Each detected speech turn starts a timer. On final transcript receipt, the console
announces that the reply timer is running. A timing summary appears as soon as the
first 20 ms PCM frame has been written to the speaker device, and is saved under
`output/calls` alongside that call's transcript. This runs for every response,
including safe spoken account results and goodbye responses, not only the opening.
Idle notices have no caller speech turn and are not assigned a caller response time.

The summary distinguishes:

- Speech detection to first audio: includes the caller's speaking time.
- Final transcript receipt to first audio: includes queued work, RAG/model/TTS.
- Estimated last spoken word to first audio, plus endpoint/STT wait: available when
  STT word timestamps can be mapped to the local PCM send timeline. This is an
  estimate; microphone capture/OS buffering is not independently calibrated.
- RAG total, remote query embedding time, and local FAISS vector search time.
- LLM request to first generated token, or buffered-response time on protected paths.
- First generated token to the first screened sentence queued for speech.
- TTS request to first PCM, followed by the first device write. This is not a
  measurement of the sound physically leaving the speaker.

Missing stages are omitted; missing audio is reported without inventing a latency.
Timing output contains no transcripts/account data. Reporting failures cannot stop
otherwise successful playback. A fresh context per turn prevents previous timings
from leaking into subsequent responses, including worker threads.

A live in-memory two-turn probe measured 3.38 and 3.66 seconds from request to first
PCM. The public RAG turn spent about 594 ms creating its query embedding; local vector
search was below 0.1 ms, Gemini took about 2.34 seconds to its first token, and TTS
took about 547 ms to first PCM. The conversational turn took about 3.03 seconds to
Gemini's first token and 297 ms to TTS PCM. These are individual network samples,
exclude real STT endpointing, and do not establish a general speedup. Loading FAISS
in memory does not eliminate the remote embedding or generation requests.
