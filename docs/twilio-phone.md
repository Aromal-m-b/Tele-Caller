# Inbound phone calls with Twilio, ngrok and Uvicorn

The phone bridge replaces the local microphone and speaker with a bidirectional
Twilio Media Stream. It reuses the managed Deepgram agent, FAISS tools, synthetic
customer-service tools, confirmation flow and private call logs. The standard
desktop launchers remain available.

```text
Caller → Twilio number → POST /voice → ngrok → Uvicorn/FastAPI
                           ↓
                 WSS /media-stream ↔ Deepgram Voice Agent
                           ↕
              FAISS / synthetic MongoDB / private call logs
```

## 1. Install in the environment you will run

Use Python 3.10–3.12. The telephone resampler uses `audioop`, which is not included
in Python 3.13+. From the repository directory in your activated environment:

```powershell
python -m pip install -r requirements-phone.txt
```

Keep the existing Gemini and Deepgram keys and trusted FAISS index. Build the
fictional demo index first if this is a fresh checkout, as described in the
[quickstart](quickstart.md). MongoDB remains optional for knowledge-only calls.

## 2. Configure your local .env

```dotenv
PUBLIC_BASE_URL=https://your-current-ngrok-host.ngrok-free.dev
TWILIO_ACCOUNT_SID=your_account_sid
TWILIO_AUTH_TOKEN=your_auth_token
TWILIO_ALLOWED_CALLERS=your_pilot_caller_in_+91_format
TWILIO_MAX_CALLS=1
```

Use your actual ngrok HTTPS origin without `/voice`. Never commit credentials.
The allowed callers are comma-separated numbers in the existing Indian +91
format. If that setting is empty, the existing `CALLER_PHONE` is used. A signed
Twilio request alone does not authorize arbitrary numbers for this pilot.

Caller identity now comes from the signed webhook's `From` field rather than a
hardcoded simulated number. Customer database access remains SYNTHETIC-only;
caller ID does not authenticate real customer accounts. Add pilot numbers
deliberately. Continue to use the [service setup](customer-services.md) for saved
addresses and approved synthetic service-detail sharing.

## 3. Start the server and tunnel

```powershell
python -m uvicorn telecom_support.phone_server:app --host 127.0.0.1 --port 8000 --workers 1 --ws-max-size 32768 --no-proxy-headers
```

Or run `start-phone.bat` in a configured environment. It prefers the repository's
`.venv` when present. In another terminal, keep the tunnel running:

```powershell
ngrok http 8000
```

`GET /` identifies the service. `GET /health` checks telephone configuration and
reports whether the knowledge base has loaded; it does not verify provider keys
or MongoDB connectivity. The first authorized stream loads the knowledge base,
then later calls reuse its retriever. Each call has separate identity, drafts and
audit state. Configuration changes require restarting the server.

## 4. Point your Twilio number at the webhook

In your Twilio number's Voice configuration, set **A call comes in** to:

- Handler: Webhook.
- URL: `https://your-current-ngrok-host.ngrok-free.dev/voice`.
- Method: **HTTP POST**.

Save the configuration. Call that Twilio number from an allowed pilot number.
Twilio account/trial restrictions and charges still apply. This bridge does not
create outbound calls or buy/configure a phone number on your behalf.

Opening `/voice` in a browser sends GET and is not a call test. A manual unsigned
POST is rejected. The returned TwiML connects `/media-stream`, then hangs up when
that stream finishes. The browser root is a status endpoint, not a browser dialer.

## Audio and lifecycle

Twilio supplies base64 G.711 mu-law audio at 8 kHz. The bridge resamples it to the
existing 16 kHz PCM agent input. Agent 24 kHz PCM output becomes 8 kHz mu-law and
is paced back to Twilio; no audio file or full-response buffer is required.
Telephone bandwidth can sound different from desktop playback.

On speech interruption, the bridge clears both the local playback queue and
Twilio's audio buffer. Twilio mark acknowledgements establish when playback
actually finishes, so cleared review audio does not arm form submission. The
goodbye is acknowledged before normal stream closure; TwiML then hangs up the
call. A stalled acknowledgement has a bounded timeout rather than an indefinite
open call. Provider idle and call-control behaviour remain those of the managed
backend.

Webhook and WebSocket signatures are validated against the configured public URL,
not an untrusted forwarded host. A short-lived, single-use token binds the stream
to the signed call SID, account and caller without placing the phone number in
the stream URL. Calls above the configured capacity receive a non-success response.

## Current deployment scope

Run **one Uvicorn worker**. Pending call tokens and capacity are in process memory;
multiple workers or machines need shared coordination. The default limit is one
call; increasing it requires load and provider-quota testing. The server provides
inbound voice only: human transfer, a staff dashboard, real-account authentication
and distributed operation are not implemented.

Call TXT logs remain in private `output/calls/`. API estimates exclude Twilio and
ngrok charges and are not invoices. No recordings are written by this bridge.
Keep the machine, Uvicorn and ngrok running for the call; ngrok inspection may
retain webhook/media details according to your local/account settings.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Connection refused | Uvicorn must listen on port 8000 while ngrok forwards there |
| HTTP 503 configuration | Set the public URL, account SID, auth token and allowed callers |
| HTTP 503 capacity | Another call or an unexpired 90-second stream reservation occupies the slot |
| HTTP 403 | Check the exact ngrok origin, Twilio account token and allowed calling number |
| Stream opens then closes | Check private server diagnostics, Gemini/Deepgram access and trusted index availability |
| No or distorted audio | Confirm bidirectional Connect/Stream and mu-law/8 kHz mono start metadata |

Tests in `tests/test_phone.py` use synthetic signed webhooks and audio without
placing a telephone call. Install the phone requirements to include them in the
full test run.

References: [Twilio Media Streams](https://www.twilio.com/docs/voice/media-streams),
[stream messages and marks](https://www.twilio.com/docs/voice/media-streams/websocket-messages),
[Twilio signature validation](https://www.twilio.com/docs/usage/security),
[FastAPI/Uvicorn deployment](https://fastapi.tiangolo.com/deployment/manually/).
