# Deploy the browser dialer on Vercel

For the current application, deploy the static browser page on Vercel and keep
the persistent Python/RTC worker on your computer through ngrok, or on an always-on
backend host. The agent starts background tasks and retains live room/session
state in memory; this implementation is not a stateless Vercel Function.

```text
Vercel HTTPS site → /api/* rewrite → ngrok HTTPS → local FastAPI on port 8000
Browser microphone/speaker ↔ LiveKit ↔ Python Deepgram-agent worker
```

Vercel serves HTML, JavaScript, CSS and the bundled LiveKit browser SDK. Its
external rewrites forward the short session start/status/end API requests. Audio
does not pass through a Vercel Function or the ngrok HTTP proxy; it uses LiveKit.

## 1. Keep the backend running

Stop the old phone server, if it is still using port 8000, then run:

```powershell
.\start-webrtc.bat
```

In another terminal, start ngrok with your account-assigned domain:

```powershell
ngrok http --url=https://supper-rinsing-mop.ngrok-free.dev 8000
```

If ngrok is already serving this domain, leave that process running. Do not create
a second tunnel unnecessarily. Verify the Forwarding line points to port 8000.
The computer must stay awake and online, with both the backend and tunnel running.

## 2. Import the repository into Vercel

1. In Vercel, choose **Add New → Project**, then import the Tele-Caller GitHub repo.
2. Set **Root Directory** to `telecom_support/web`.
3. Set **Framework Preset** to **Other**.
4. The included `vercel.json` sets empty build/install commands and output directory
   `.`. If the dashboard asks for overrides, leave Build and Install empty and set
   Output Directory to `.`. Do not configure a Python/Uvicorn start command there.
5. Add no provider environment variables for this static deployment.
6. Deploy and open the production `https://your-project.vercel.app/` URL.
7. Enter your private numeric dial code and tap Call. Grant microphone permission.

The page calls relative `/api/sessions` routes. The included routing file forwards
them to `https://supper-rinsing-mop.ngrok-free.dev/api/...` while retaining the
Vercel URL in the browser. No frontend API-origin variable or cross-origin CORS
change is needed. The browser client includes the ngrok warning-skip header for
API traffic so it receives JSON rather than a browser interstitial.

If the wrong root directory is selected, Vercel may try to build the whole Python
project. Recheck the root: it must contain `index.html`, `static/` and `vercel.json`.

## 3. Where each credential belongs

| Setting | Vercel static frontend | Python backend `.env` |
| --- | --- | --- |
| `LIVEKIT_URL` | Delivered by the authenticated session API; no manual variable needed | Required |
| `LIVEKIT_API_KEY` and `LIVEKIT_API_SECRET` | Never add to browser configuration | Required |
| `DEEPGRAM` or `DEEPGRAM_API_KEY` | Not needed | Required |
| `GEMINI_API_KEY` | Not needed | Required |
| `WEBRTC_ACCESS_CODE` | Do not embed; the owner enters it at runtime | Required |
| `WEBRTC_DIAL_CODE` | Do not embed; the owner dials it at runtime | Optional generated numeric credential |
| `CALLER_PHONE` | Do not expose or allow client account selection | Configured synthetic demo caller |
| `MONGODB_URI`, `MONGODB_DATABASE` | Not needed | Needed for database-backed service features |
| `AGENT_SHARE_SYNTHETIC_SERVICE_DETAILS` | Not needed | Existing approved synthetic-data setting |

Keep the rest of the existing backend model/voice/cost settings on the backend.
The Vercel static site needs **no API keys** with the supplied routing file. Do not
upload `.env`, set public-prefixed secrets, or write keys into `index.html` or JS.
The browser receives a short-lived, room-scoped token from the backend, not the
LiveKit API secret. Twilio credentials are not needed for browser calls.

Only share the access code with authorized demo testers: it selects the same
configured synthetic account, not independent real customer logins. Default
capacity is one call. Provider API and platform usage charges still apply.

## 4. Does the ngrok URL change on restart?

Current free ngrok accounts get one assigned dev domain, which remains assigned
across agent restarts. With the same account and domain, restarting the tunnel
should reuse `supper-rinsing-mop.ngrok-free.dev`. Always check the actual Forwarding
line after reconnecting. The hostname can differ if you change accounts, domains
or tunnel configuration.

If it really changes, replace the old origin in **both** `destination` values in
`telecom_support/web/vercel.json`, preserving `/api/:path*` and `/health`. Commit,
push and redeploy the Vercel site. There is no backend-origin environment variable
in this configuration. Editing a file only on your computer does not update an
already deployed Vercel routing table.

Do not replace `LIVEKIT_URL` with ngrok or Vercel: it remains your LiveKit project's
`wss://...` URL. `PUBLIC_BASE_URL` belongs to the separate Twilio bridge and is not
used by this LiveKit browser route. Update it only if you continue using that bridge.

Later, when the Python worker moves to a permanent HTTPS backend, replace the two
ngrok origins with that backend origin. The frontend and its Vercel URL can stay
the same, and your laptop no longer needs to remain online.

## 5. Verify the deployed result

- The Vercel root should show the dialer, not a JSON response.
- `https://your-project.vercel.app/health` should report `livekit-webrtc` and
  `deepgram-agent`. This checks proxy routing/configuration, not provider quotas.
- A call should progress through microphone permission, ringing and connected.
- If the UI loads but calls fail, first check local Uvicorn, ngrok Forwarding and
  the rewrite destinations. Use the same private access code configured locally.

The routing file was validated against Vercel's configuration schema, and browser
controller checks passed locally. The external proxy still needs verification
after you deploy in Vercel; no remote deployment has been performed. No Vercel
account or paid hosting plan has been created or changed.

References: [Vercel external rewrites](https://vercel.com/docs/routing/rewrites),
[build/root directory settings](https://vercel.com/docs/builds/configure-a-build),
[Python lifecycle considerations](https://vercel.com/i/serverless-python),
[ngrok assigned dev domains](https://ngrok.com/docs/pricing-limits/free-plan-limits).
