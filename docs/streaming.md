# Streaming voice replies

Microphone audio streams continuously to Deepgram Nova-3. The turn detector still
waits for an end-of-turn pause before accepting a request. Interim words can change:
the assistant does not execute tools or answer an unfinished request.

For public procedural questions such as “How can I recharge online?”, the regular
Gemini text API now streams tokens through a sentence boundary buffer. Each complete
sentence passes the existing output screen before entering a bounded speech queue.
Deepgram's reusable Aura-2 WebSocket plays that sentence while the next sentences
are being generated. Normal startup attempts to open the socket once, before the
greeting. `DEEPGRAM_TTS_TRANSPORT=http` selects the older HTTP transport. If socket
setup fails, HTTP is used; a synthesis failure falls back only before any audio
arrives, so already delivered words are never replayed. Accepted failed attempts
may still be billable. This is not Gemini Live.

The default text model is now `gemini-3.8-flash` with `low` thinking. Existing
explicit model overrides are supported. Gemini 3.8 does not accept `minimal`, and
custom sampling parameters are omitted for newer models. The public grounding
prompt is shorter; evidence, history, privacy and tool checks remain active.

Account/identity requests, package recommendations, pending confirmations and ticket
operations retain the existing buffered tool path. Ordinary conversational reply,
decline and clarification text now streams from an intent-first JSON header, with
escape-safe decoding and sentence screening. Repeat and hang-up intentions remain
buffered so their control action is validated before speech. Reordered JSON fields
fall back to buffered playback. Private
account results retain their safe spoken substitutes. This is an initial public
answer streaming path, not streaming coverage of every kind of response.

Confirmed barge-in stops the current audio and discards queued sentences for that
reply. The public model stream closes at its next token/callback checkpoint;
an in-flight synchronous network read cannot be preempted immediately. Buffered
tool requests still finish before session state closes; no speculative tools run.
Cancelling socket synthesis clears/closes its connection so stale audio cannot
enter the next reply. Each sentence offered for playback is logged even if the caller
interrupts before the complete answer can be printed. Logs do not prove that the
caller heard every word. Partial public generation is marked interrupted in history.

Sentence screening is incremental: later text cannot retract words already spoken.
The input scope guard and grounded prompt remain active. Network/provider timing
still affects first audio. In an in-memory PCM probe, the socket took 1.49 seconds
to open; first audio took 1.16 seconds for its first phrase and 0.31 seconds for the
next phrase. Full Gemini/RAG/Aura probes still took 4.14-4.56 seconds to first PCM,
excluding STT endpointing and real speaker playback. These few samples are not an
end-to-end speedup guarantee. Provider timing and sentence boundaries can add gaps.

The local call controller accepts an explicit end-call clause inside a longer
utterance and natural confirmation such as “Yes, of course” after a hang-up question.
Negated, quoted and hypothetical commands do not authorize ending. A generated
permission question arms the controller regardless of which model path produced
it. Ending returns from the processor and closes microphone, TTS and STT contexts;
it does not restart idle warnings. This remains a simulated local call.

Run `python -m pytest tests/test_streaming.py -q` to verify early delivery, decimal
boundaries, scope rejection, barge-in and playback-failure cleanup without API calls.
SIP/PSTN transport, Redis session storage and human transfers from the proposed
architecture are not implemented by this change.

Every microphone turn now prints a timing line when its first PCM frame has been
written to the speaker device. The same line is saved in the local call log. See
[timing definitions](latency.md#per-turn-timer) for which delays are measured.
