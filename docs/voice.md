For the current assistant with account tools and temporary memory, use `main.py`:
it now accepts microphone input by default and prints its replies. This document
describes the separate `voice_main.py` duplex prototype, which has its own agent.

The voice prototype uses Deepgram streaming speech recognition, standard Gemini
text generation with explicit FAISS tool calls, and Deepgram HTTP speech synthesis
with incremental PCM playback. Gemini receives text only. Previous Gemini audio
entry points have been consolidated into `voice_main.py`.

Setup (PowerShell, from this folder):

```powershell
python -m venv .venv-voice
.\.venv-voice\Scripts\python.exe -m pip install -r requirements-voice.txt
.\.venv-voice\Scripts\python.exe voice_main.py
```

Use `voice_main.py --text` to exercise the agent and retrieval without audio devices.
Keep `GEMINI_API_KEY` and `DEEPGRAM` in the root `.env`. Optional model overrides
are documented in `.env.example`. Use headphones to prevent speaker feedback.
Speech recognition is English, 16 kHz mono PCM; synthesis is English, 24 kHz.
An utterance endpoint triggers reasoning. Speech start cancels the response and
flushes playback. Final transcript segments are accumulated until an endpoint.
Conversation context is bounded to six completed exchanges, tool rounds to four,
and generated responses to 512 tokens. No automatic waiting phrases are added.

The existing `faiss_index` must be trusted and its embedding model must match
`GEMINI_EMBEDDING_MODEL`; rebuild the index if switching embedding models.
The repository's index builders use different embedding models, so confirm which
builder produced the index before changing this setting. The old mock hardware
retriever is not used by the new voice flow.

This change replaces the voice transport. It does not implement the full V2
MongoDB customer tools, authentication, LangGraph migration, asset synchronization
or latency percentile reporting. Gemini currently completes a text response before
TTS starts; HTTP TTS audio is played incrementally. The logged duration measures
audio queueing, not first audible response, and no 800 ms guarantee is made.
