# Simulated callers tickets and private call logs

## Incoming caller identity

Set `CALLER_PHONE` in private `.env` to simulate an incoming Indian number. Each
session resolves its national/+91 variants against the customer collection. A
unique active `SYNTHETIC` match binds the account; the LLM generates a personalized
greeting using the approved display name. Guests get ordinary LLM greetings.
Override the number with `python main.py --caller-number NUMBER`.

`python scripts/seed_demo_caller.py` creates the configured synthetic caller only
if its number and demo customer ID are unused. It never overwrites a real or
ambiguous record. Defaults: username `demo_caller`, display name `Demo Caller`,
and a fresh random smartcard ID shown only by the local seed command. No subscriptions
or payments are fabricated. The number
belongs in `.env`, not source control.

A CLI number is not trusted telephone-provider caller ID. This route intentionally
authenticates only synthetic accounts. Real customer access requires trusted
carrier/session identity and appropriate authentication. The existing operator
`--demo-customer` override remains a separate trusted synthetic fixture selection.

## Unknown calling number

General enquiries, new-customer questions and dealer/distributor discussions do not
require registration. Maya asks relevant questions and uses approved knowledge;
she must not invent investment terms, packages or promised returns.

For private account access or tickets, `begin_customer_verification` starts local
intake of a registered username/full name and stored smartcard ID. The card is
matched locally and never sent to Gemini. Spacing, hyphens and spoken digit words
are normalized. Three failed attempts exhaust the per-call check. Legacy customers
need a valid stored `smartcard_id`; missing identifiers are not guessed or assigned.

## Issue tickets using your DOCX

Set `SUPPORT_TICKET_TEMPLATE` to the local DOCX path. The supplied template stays
external to the public repository. Its fields contain customer name/email/phone,
issue type, priority, description, attachments, related tickets, history and
preferred contact method. Customer values are filled locally from the verified
account. The voice flow has no attachment-upload UI, so the form records none.
Related tickets are scoped to that same customer.

`prepare_support_ticket` drafts the issue from caller-provided details. No write
occurs until the caller reviews the summary and explicitly confirms. The local
handler stores an Open record in `support_tickets` and writes
`output/tickets/TKT-....docx`. No model tool can directly invoke confirmation. A no
cancels the draft. These writes never alter payments, subscriptions or profiles.

The DOCX renderer expands fields and conditional/repeating blocks while preserving
other OOXML package parts. A missing template prevents submission; failures are
reported rather than claiming success.

## One TXT file per call

Every session writes `output/calls/<UTC timestamp>_<call ID>.txt`: caller identity,
timestamped final user/assistant text, interruptions and a final usage/cost section.
Ctrl+C/error paths also finalize it. These files are local and ignored by Git.
Verification responses are redacted before writes. Later model prompts mask the
display name; name-transfer permission is limited to greeting generation/speech.

Local transcripts can contain issues and private on-screen results. Secure them
and set a retention policy appropriate to your deployment; never publish them in
repository issues. Raw database account/payment results remain excluded from Gemini.

Gemini input/output tokens come from provider usage metadata for opening,
conversation, tool-selection and lifecycle requests. Reasoning is included in
output rather than counted twice. STT tracks PCM bytes and connected time. TTS
counts text accepted for synthesis, including requests interrupted later.

The footer is an **estimated USD cost, not a provider invoice**. Defaults use the
Gemini 3 Flash $0.50/$3 per million input/output tokens, Nova-3 $0.0077/min and
Aura-2 $0.030/1,000 characters planning snapshot. Override `COST_*` values for your
account/model. Rounding, cache discounts, retries, missing metadata and taxes may
differ. Embedding/OCR usage is unavailable through the callback and explicitly
excluded. Typed calls have no STT charge. Sources are linked in the
[business guide](business-case.md).
