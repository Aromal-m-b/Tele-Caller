# Malayalam voice demo with Gemini Live

This optional backend replaces Deepgram Voice Agent for a call with Google's
native audio model. It uses the existing LiveKit room, dialer, access code and
Vercel/ngrok routes. No frontend changes or Vercel secrets are needed.

This is a conversation demo, not parity with the Deepgram service backend:
it does not load MongoDB, customer identities, complaint/order tools or FAISS.
It cannot quote verified current prices or submit requests. Keep using
`start-webrtc.bat` for the existing Deepgram customer-service implementation.

## Start

1. Run `setup-webrtc.bat` if the LiveKit environment is not installed.
2. Install the additional SDK in PowerShell:
   `.\.venv-livekit\Scripts\python.exe -m pip install -r requirements-gemini-live.txt`
3. Keep the existing LiveKit and dialer settings in `.env`, and add:

   ```dotenv
   GEMINI_API_KEY=your_private_google_api_key
   GEMINI_LIVE_MODEL=gemini-3.8-live
   GEMINI_LIVE_VOICE=Kore
   ```

4. Stop the existing Uvicorn server with Ctrl+C, then run `start-gemini-live.bat`.
   Alternatively:

   ```powershell
   .\.venv-livekit\Scripts\python.exe -m uvicorn telecom_support.gemini_live_server:app --host 127.0.0.1 --port 8000 --workers 1 --no-proxy-headers
   ```

5. Leave ngrok forwarding to port 8000. Use the same webpage and dial code.
   `/health` now reports `backend: gemini-live`. The introduction and replies are
   generated in Malayalam; ask for English to switch languages.

Microphone PCM streams to Google at 16 kHz throughout the connected call;
Google performs turn detection. Returned 24 kHz audio plays incrementally.
Provider interruption events clear queued playback. The semantic `end_call`
tool disconnects after the generated goodbye finishes. Browser hangup also works.
There is a 15 minute server call limit; provider session-limit notices end the
demo rather than silently dropping conversation state. No automatic reconnection
or idle prompt scheduler is implemented in this demo.

Private local files in `output/calls` contain transcript fragments and raw usage
snapshots. Do not sum cumulative snapshots. Native audio billing differs from text
generation; costs are explicitly unavailable rather than estimated with text rates.
Audio and conversational content are sent to Google, so use demo conversations.
The API key stays on the Python server. Model access, billing and Malayalam voice
quality must be checked with your own API account and a real listening test.

Protocol/configuration follow the [Google Live API guide](https://ai.google.dev/gemini-api/docs/live-api/capabilities).
