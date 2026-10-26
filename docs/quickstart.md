# Start here: business owner pilot

Tele-Caller is a working local voice-support reference application. The current
assistant and account schema use a telecom/DTH example. It is not yet a hosted
multi-business SaaS, public phone line, or generic agent for every industry.

## Windows: download and launch

1. Download this repository with **Code → Download ZIP**, then extract the folder.
2. Install Python 3.10, 3.11 or 3.12 with Python available on PATH. Python 3.10 and
   3.12 are the tested runtimes. Connect a microphone and preferably headphones.
3. Double-click `setup.bat`. It creates an isolated `.venv` and installs the core
   dependencies. In the opened `.env` file, enter `GEMINI_API_KEY` and `DEEPGRAM`.
   Save the file. Keep your keys private; each business should use its own accounts.
4. Double-click `start.bat`. If no index exists, it embeds the included fictional
   demo knowledge once, then starts a microphone conversation. API usage is billable.

Maya speaks first. Try asking what the demo can help with, asking her name, or
asking her to repeat a response. Say "please end the call" to disconnect. The demo
contains no official prices, real customer records or payment instructions.

Existing Anaconda users can keep running `python main.py` in their configured
environment. The launchers use their own `.venv`; they do not modify Anaconda.

## Set a comfortable pace

The default pause allowance is 1.5 seconds. For a caller who pauses longer to
breathe or think, open a terminal in the folder and run:

```powershell
.\.venv\Scripts\python.exe main.py --speech-pause 2500
```

Or set `DEEPGRAM_ENDPOINTING_MS=2500` in `.env`. A longer pause reduces premature
answers but makes replies start later. Caller inactivity is separate: three minutes
to a check-in, then 45 seconds before disconnect. Change `CALL_IDLE_TIMEOUT_SECONDS`
and `CALL_IDLE_GRACE_SECONDS`; zero timeout disables idle disconnect.

## Add approved business knowledge

The demo is a safe starting point, not sellable product information. Ask your
technical implementer to supply approved PDFs, build an index with `pdf_ingest.py`,
and start the app with `--index-dir PATH`. Complex tables/scans may require the
optional `requirements-pdf.txt` dependencies. See [developer guide](developer-guide.md).
Adapting to another industry also requires changing the persona, scope guard and
account-tool schema; replacing PDFs alone does not remove telecom-specific behavior.

MongoDB is optional. Leave its URI empty for a knowledge-only pilot. Private account
lookups require an authenticated identity supplied by a trusted application; the
synthetic demo-customer flag is not customer authentication. No recharge or payment
write operation exists. Private account results remain on screen rather than being
sent to Gemini or Deepgram speech output.

## Before serving real customers

Run a supervised pilot using approved knowledge and consented data. Measure useful
answers, successful resolution, repeat contacts, response delay and total cost. A
production rollout still needs real customer authentication, human handoff,
telephony integration if required, monitoring, and tenant isolation for multiple
businesses. Budget these explicitly using the [business case](business-case.md).

If startup fails, check the terminal message, API keys/quotas and selected audio
device. Try `main.py --text` to isolate audio problems. Don't upload your `.env`,
database exports or customer transcripts to an issue.
