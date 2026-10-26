# Technical architecture and operating limits

The supported entry points are `main.py` (microphone or typed support) and
`pdf_ingest.py` (adaptive PDF extraction and indexing). The optional older voice
prototype is retained as a developer reference, not the supported owner launcher.

## Per-session flow

1. PyAudio captures 16 kHz mono PCM and streams it to Deepgram Nova-3.
2. Final utterances enter a bounded queue. Async network/audio tasks remain active
   while a serialized worker performs model, retrieval and database work.
3. A local guard blocks explicit programming misuse, prompt override and bulk data
   extraction. Gemini interprets ordinary conversation using text history. Public
   support questions use FAISS reference context; account requests use named tools.
4. Account identity is supplied by a trusted application, not by a spoken ID.
   Read-only MongoDB results render locally; raw private values are excluded from
   Gemini history and replaced with safe prompts for Deepgram speech.
5. Deepgram Aura-2 synthesizes public text; small PCM writes support interruption.
   Local VAD opens a transcription window. Confident recognized customer words,
   rather than mic activity alone, can interrupt playback. Direct echo is filtered;
   headphones remain recommended because this is not full acoustic cancellation.
6. Repeat requests replay the actual previous answer. Direct disconnect requests,
   confirmed wrap-up and idle expiry close audio/socket resources. Idle time is
   measured while waiting for the caller, not during backend work or playback.

Recent dialogue contains six exchanges. Older exchanges are losslessly compressed
in RAM and relevant excerpts are reintroduced within a 5,000-character memory
budget. This avoids a separate summarization request but does not eliminate prompt
token costs. Memory disappears on process exit; the application is not a persistent CRM.

## Knowledge ingestion

The adaptive loader uses native PDF text for straightforward pages, optional
Docling for complex layouts/tables, PaddleOCR for scans, and standard Gemini vision
where configured. Build an approved corpus explicitly. A manifest ties the index
to its embedding model. Only load indexes you generated or otherwise trust because
FAISS metadata uses pickle. The included demo builder never overwrites an index.

## Runtime configuration

| Setting | Default / use |
| --- | --- |
| GEMINI_MODEL | gemini-3-flash-preview, standard text API |
| GEMINI_EMBEDDING_MODEL | models/gemini-embedding-001; must match the index |
| DEEPGRAM_STT_MODEL | nova-3 |
| DEEPGRAM_TTS_MODEL | aura-2-andromeda-en |
| DEEPGRAM_ENDPOINTING_MS | 1500; raise for longer breathing/thinking pauses |
| CALL_IDLE_CHECKIN_SECONDS | 15; zero disables the early audibility check |
| CALL_IDLE_TIMEOUT_SECONDS | 45; zero disables idle disconnect |
| CALL_IDLE_GRACE_SECONDS | 20 |
| MONGODB_URI | optional; keep in local .env/secret storage |

Configuration secrets are not committed. Account tools have no write, payment or
renewal capability. The synthetic demo identity option is not production login.

## Moving from pilot to service

This release runs one local audio session. Horizontal scale requires a separate
session worker and isolated identity/memory per call, a telephony/WebRTC ingress,
tenant-scoped retrieval and database authorization, secret management, durable
operational metrics, budget enforcement and human escalation. Do not share the
current `.env`, global FAISS index or a trusted identity across unrelated tenants.

Measure end-to-end response delay, premature turn endings, false interruptions,
grounded-answer accuracy, account authorization, containment and billed cost per
resolved contact. Unit/regression tests validate logic, not room acoustics,
multi-tenant isolation, production throughput or actual financial return.
