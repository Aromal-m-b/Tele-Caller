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

The output safety screen, personal-data restrictions, local confirmation checks
and read-only account behavior remain enforced. Speech is still played after the
answer has been checked; unvalidated LLM token streaming is not enabled.
