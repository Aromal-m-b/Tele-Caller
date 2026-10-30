# Browser calls with LiveKit and the existing Deepgram agent

This route uses WebRTC in a browser rather than a telephone number. It does not
depend on Twilio's trial capabilities or a SIP connection. LiveKit carries the
audio; the same `run_voice_agent()` used by `main.py --backend deepgram-agent`
provides streaming recognition, managed reasoning, speech, tools and call control.

```text
Browser microphone → LiveKit room → Python 16 kHz PCM → Deepgram Voice Agent
Browser speaker    ← LiveKit room ← Python 24 kHz PCM ← streamed agent audio
                                          ↕
                          FAISS / synthetic service tools / call logs
```

## Launch

Use Python 3.10–3.12. Run `setup-webrtc.bat` once, or create a dedicated environment:

```powershell
python -m venv .venv-livekit
.\.venv-livekit\Scripts\python.exe -m pip install -r requirements-livekit.txt
.\.venv-livekit\Scripts\python.exe scripts/configure_webrtc.py
```

Keep LiveKit separate from an OCR environment that pins an older protobuf version.
The WebRTC requirements do not install optional Paddle OCR. An existing trusted
FAISS index and the normal Gemini/Deepgram configuration are reused. On a fresh
checkout, build a demo index first using the [quickstart](quickstart.md).

Set these entries in the private `.env`:

```dotenv
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=your_livekit_key
LIVEKIT_API_SECRET=your_livekit_secret
CALLER_PHONE=your_synthetic_demo_customer_number
WEBRTC_ACCESS_CODE=your_generated_private_browser_code
WEBRTC_MAX_CALLS=1
WEBRTC_DIAL_CODE=your_generated_eight_digit_code
```

The setup helper generates the long browser code and an eight-digit keypad code
without printing either or changing
provider credentials. The configured caller selects a SYNTHETIC account locally.
The browser cannot choose another account by submitting a phone number. This is
an owner-operated demo, not authentication for real customers.

Stop another Uvicorn process using port 8000 with Ctrl+C. Run `start-webrtc.bat` or:

```powershell
.\.venv-livekit\Scripts\python.exe -m uvicorn telecom_support.livekit_server:app --host 127.0.0.1 --port 8000 --workers 1 --no-proxy-headers
```

Open `http://127.0.0.1:8000/`, dial `WEBRTC_DIAL_CODE` on the keypad, and tap the
green **Call** button. You can also paste the existing `WEBRTC_ACCESS_CODE` in
the code field. Allow microphone access. If autoplay is blocked, use **Enable
speaker audio**. Headphones reduce echo. The page provides mute and end controls;
the assistant can also end the session through its existing call-control flow.

## Dialer and calling screens

The responsive page behaves like a phone dialer on a mobile or tablet:

1. Enter the private code with the keypad, paste, or keyboard; use backspace to edit.
2. Call opens **Connecting** and requests microphone permission. A persistent retry
   card remains when permission is denied or the device is unavailable. No cloud
   room is created until capture succeeds. The browser may require the user to
   change the site's microphone setting before a retry can work; the page cannot
   override a denied permission or force a blocked permission dialog to reappear.
3. Once capture succeeds, the screen shows **Ringing — Connecting to agent** while
   LiveKit, knowledge and Deepgram initialize.
4. **Connected** and the call timer begin after Deepgram accepts its settings and
   the audio adapters open, not merely when the browser joins a room.
5. Mute, speaker mute, cancel/hang-up, redial and return-to-keypad controls manage
   the session. Cancelling an outstanding permission request prevents a late grant
   from accidentally starting a new call.

The numeric dial code is an optional second demo credential, protected by a
five-failed-attempt/30-second cooldown. The long code remains valid. Neither code
is included in the client room token or page source. This is an owner demo, not
a replacement for production user authentication.

## Use the ngrok page on another device

Keep `ngrok http 8000` running and open its HTTPS URL in your browser. The page,
token requests and status requests pass through FastAPI/ngrok; microphone and
speaker audio travel through LiveKit WebRTC. No Twilio webhook needs changing
for this browser test, and the browser page does not use `/voice`.

Microphone access needs localhost or HTTPS. Keep the access code private. Do not
publish `.env`, browser tokens or private call logs. SDK JavaScript is pinned and
served locally; its upstream license is included under `telecom_support/web/static`.

## Session lifecycle and limits

The server issues room-scoped, two-minute join tokens. Browser grants allow only
microphone publishing and audio subscription, without room administration or
data publishing. LiveKit identities and room names are random and contain no
customer phone, profile or address. Provider keys never go to the browser.

Each session gets isolated customer/form state and a private call log; the loaded
knowledge retriever is shared. The Python agent publishes audio and subscribes
only to that session's caller identity. An idle startup without a microphone
expires after 60 seconds. Sessions have a 15-minute upper bound and default to
one concurrent call. Closing/leaving ends the agent and deletes the room; provider
errors are logged by type without raw credentials or payloads.
Microphone startup frames are discarded until the Deepgram session begins
reading, preventing cold knowledge/provider startup from filling the input queue.
Wait for Maya's introduction before explaining the issue.

Interruption clears the LiveKit source queue and local reply queue. The source
drain waits for local RTC playout before the normal call-control completion;
this is not proof of physical sound playback on every browser/device. Network
and browser jitter buffers can retain a short tail of audio. Client muting stops
microphone publication, while End call disconnects the room.

API costs and LiveKit usage still apply according to the respective accounts.
Current TXT estimates do not include LiveKit charges or a complete provider
invoice. A successful `/health` response validates local settings, not provider
quotas; the actual RTC/audio test is the connectivity check.

## Verification

`tests/test_livekit.py` covers private access, scoped tokens, isolated sessions,
audio formats, interruption and cleanup. It uses mocks and no microphone.
A separate live smoke check connected two RTC participants through the configured
LiveKit project, sent generated silence to the Deepgram agent, and received its
real synthesized audio. This verifies network audio, not human listening quality.
The smoke check requires speech-level output and a matching assistant transcript,
rather than counting RTC startup/comfort-noise frames as a successful agent reply.
With Node.js installed, `tests/test_web_dialer.py` also runs six controller tests
covering keypad editing, denied permission and retry, ringing/readiness, repeated
clicks, and cancellation during the permission dialog. No real microphone is used.

References: [LiveKit browser SDK](https://docs.livekit.io/reference/client-sdk-js/),
[Python RTC SDK](https://docs.livekit.io/reference/python/livekit/rtc/),
[access tokens](https://docs.livekit.io/reference/python/livekit/api/access_token.html).
