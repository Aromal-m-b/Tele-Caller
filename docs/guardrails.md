# Support scope and backend guardrails

`telecom_support/guardrails.py` enforces the support boundary locally, before FAISS
retrieval, embedding, answer generation or MongoDB tool selection. The same scope
guard is used by the separate voice-agent prototype and by direct tool-agent calls.

## Enforcement layers

1. Normalize Unicode width, case and zero-width characters for screening, without
   rewriting accepted customer questions. Cap raw and normalized requests at 4,000
   characters.
2. Reject programming, code generation, unrelated task categories, instruction
   overrides, backend-secret requests, raw database operations and bulk customer
   extraction. Explicit disallowed tasks take precedence over support keywords,
   so adding "Tech Five" to a coding request does not authorize it.
3. Admit supported TV/account/recharge topics and contextual service follow-ups.
   Conversational asides, identity, greetings and ambiguous fragments use a constrained
   Gemini conversational path, explicitly authorized for text dialogue. That path
   has no retrieval or account tools and must decline unrelated tasks.
4. Limit backend-bound requests to 30 per 60 seconds per application session.
   Hard local refusals make no model calls; conversational generation uses the cap.
5. Reinforce the same service-only boundary in the model prompt. Screen generated
   replies for executable code, false programming-capability offers and claims
   that the assistant charged or renewed something itself.
6. Retain the existing fixed tool registry, strict argument schemas, server-bound
   identity, read-only operations and confirmation state machine. Unknown or malformed
   model tool calls now produce a support clarification rather than exposing an
   internal "invalid tool arguments" message.

Hard-rejected requests are not added to recent conversation history or the compressed
archive. They also do not consume retrieval or answer-generation API calls. The
current scope decision and reason are available locally at
`application.last_scope_decision` for debugging without storing rejected text.

TV error codes, recharge QR-code problems, receiver resets, password-reset guidance,
package preferences and contextual confirmations remain permitted. A scoped request
can still receive a knowledge-gap response if the company sources lack evidence.
Account access remains subject to authentication independently of scope admission.

## Validation

```powershell
.\.venv-voice\Scripts\python.exe -m pytest
```

The replay includes the reported Python/hello-world sequence, incomplete spoken
questions, prompt overrides, domain-keyword smuggling, Unicode/spacing obfuscation,
customer enumeration and credential requests. Tests assert rejected inputs never
reach retrieval, models or account tools, and never enter conversation memory.
Conversational tests separately verify generated text context and no account-tool access.
Negative controls cover valid customer service and follow-up requests. The live
check then verifies a legitimate recharge answer. Existing account confirmation
and multi-turn package replays also pass.

## Limits

This is a layered rule-based scope boundary, not a guarantee against every adversarial
paraphrase or language. Its vocabulary is tuned for the English support flow currently
used by the application; ambiguous or unsupported phrasing may be refused. The
generated-output screen is also heuristic. The request cap is per running session,
not a distributed, account-wide quota. A public production deployment still needs
authenticated ingress, shared rate limits and ongoing misuse/false-positive tests.
No arbitrary code execution, raw-query tool or write-capable account action is exposed.
