# Developer guide

## Supported paths

`main.py` starts the microphone assistant. `--text` enables keyboard interaction;
`--question TEXT` runs one silent typed request. `pdf_ingest.py` extracts approved
PDFs and builds an embedding-model-matched FAISS index.

Python 3.10 and 3.12 are tested. Install core runtime requirements with
`python -m pip install -r requirements.txt`; install `requirements-dev.txt` to run
`python -m pytest -q`. The tests use synthetic fixtures and mocked APIs and need no
credentials. `requirements-pdf.txt` adds optional Docling and PaddleOCR for complex
or scanned documents. Native PDF extraction is part of the core install.

## Configuration

Copy `.env.example` to `.env` and enter your own `GEMINI_API_KEY` and `DEEPGRAM`.
The text model defaults to `gemini-3-flash-preview`. Gemini embeddings retain
`models/gemini-embedding-001`. `MONGODB_URI` is optional; leave it blank for the demo.
Do not commit keys or credentials. The example database schema is in
[metadata_csv.md](../metadata_csv.md); no database contents or connection credentials
are distributed in this repository.

The supplied Windows `setup.bat` and `start.bat` create/use `.venv`. An existing
Anaconda environment can instead run `python main.py` directly.

## Knowledge setup

`python scripts/prepare_demo.py` builds a fictional demonstration index from
`examples/knowledge/`. It calls the embedding API once and preserves any existing
index. The demo is not official Sun Direct policy, packages or pricing.

For approved PDFs, create a local directory containing only the PDFs to ingest:

```powershell
python pdf_ingest.py --assets PATH_TO_APPROVED_PDFS --output data/business_index
python main.py --index-dir data/business_index
```

Complex PDF ingestion can load additional models and/or send pages to Gemini
vision. Verify the document-processing route and approvals for the intended data.
An extraction failure stops index publication. Replacing an existing index requires
an explicit `--overwrite`, which retains a sibling backup. Generated FAISS metadata
uses pickle: load only indexes generated or otherwise trusted by your application.

## Layout

| Path | Responsibility |
| --- | --- |
| telecom_support/ | RAG, text conversation, tools, audio and call lifecycle |
| loaders/adaptive_pdf_loader.py | PDF routing and extraction |
| voice_support/transcripts.py | Stable STT turn assembly |
| voice_main.py, remaining voice_support/ | Older optional voice prototype |
| examples/knowledge/ | Fictional public demo corpus |
| scripts/prepare_demo.py | Non-overwriting demo index builder |
| scripts/estimate_costs.py | Offline operating-cost/capacity model |
| tests/ | Synthetic regression tests |
| docs/ | Owner, architecture, account, speech and policy guides |

Original private/third-party assets, legacy experiments, local environments,
recordings, runtime reports and prebuilt indexes are intentionally excluded. Optional
historical integration runners from the development workspace are not part of this
release. Never execute an old database seed script against production.

## Call and speech settings

Use `--speech-pause 2500` to allow longer pauses while the caller speaks. The
default is 1500 ms. Idle warning/grace defaults are 45/20 seconds, with an early audibility check at 15 seconds, separately
configurable with `--idle-timeout` and `--idle-grace`. Zero idle timeout disables
automatic idle disconnect. See [speech input](speech-input.md) and
[speech output](speech-output.md) for interruption and data-flow behavior.

Account operations remain read-only and require trusted identity; confirmed issue tickets are a separate write action. See [caller and ticket setup](callers-tickets-logs.md). The synthetic
`--demo-customer` flag is not production authentication. There are no payment,
renewal, carrier-call or human-transfer actions. See [architecture](architecture.md)
for the additional components needed to host multiple businesses safely.
