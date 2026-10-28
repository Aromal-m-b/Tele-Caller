# Streaming voice replies

Microphone audio streams continuously to Deepgram Nova-3. The turn detector still
waits for an end-of-turn pause before accepting a request. Interim words can change:
the assistant does not execute tools or answer an unfinished request.

For public procedural questions such as “How can I recharge online?”, the regular
Gemini text API now streams tokens through a sentence boundary buffer. Each complete
sentence passes the existing output screen before entering a bounded speech queue.
Deepgram's existing HTTP audio stream plays that sentence while the next sentences
are being generated. This is not Gemini Live and does not change the configured model.

Account/identity requests, package recommendations, pending confirmations and ticket
operations retain the existing buffered tool path. Conversational intent JSON also
stays buffered so repeat and hang-up intentions are resolved before speech. Private
account results retain their safe spoken substitutes. This is an initial public
answer streaming path, not streaming coverage of every kind of response.

Confirmed barge-in stops the current audio and discards queued sentences for that
reply. The worker finishes before session state closes; it does not launch tools
speculatively. Each sentence offered for playback is logged even if the caller
interrupts before the complete answer can be printed. Logs do not prove that the
caller heard every word. Completed generation remains in conversational history.

Sentence screening is incremental: later text cannot retract words already spoken.
The input scope guard and grounded prompt remain active. Network/provider timing
still affects first audio. HTTP synthesis per sentence can introduce gaps; this
implementation does not claim persistent WebSocket TTS or measured live latency gains.

Run `python -m pytest tests/test_streaming.py -q` to verify early delivery, decimal
boundaries, scope rejection, barge-in and playback-failure cleanup without API calls.
SIP/PSTN transport, Redis session storage and human transfers from the proposed
architecture are not implemented by this change.
