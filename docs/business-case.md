# Business case: support capacity with transparent unit economics

Tele-Caller targets repetitive, documented support questions that consume founders'
and operators' time. The current deliverable is a local telecom-support pilot. It
can demonstrate self-service answers, conversational follow-ups and read-only
account lookup; it does not demonstrate revenue growth or measured labor savings.

## Where operational value can come from

| Capability | Operational benefit to test | Measure in a pilot |
| --- | --- | --- |
| Grounded FAQ answers | Fewer repeated explanations by staff | Resolved contacts without a repeat call |
| Read-only account tools | Less manual navigation for verified customers | Handling time and lookup accuracy |
| Text session memory | Fewer repeated questions and lost preferences | Repeated-question and correction rate |
| Local compressed history | Avoids a separate paid summarization call | Total input/output tokens per contact |
| Idle disconnect | Limits abandoned-session usage | Idle minutes and abandonment rate |
| Longer breathing pauses | Less premature turn-taking | Cut-off rate versus response latency |

These are intended benefits, not benchmark claims. One local CLI handles one
conversation. Vendor API concurrency limits do not make this application a
multi-tenant service. No phone carrier or automatic human transfer is integrated.

## Cost model, USD; pricing checked October 26, 2026

| Component | Planning rate | Billing driver |
| --- | --- | --- |
| Deepgram Nova-3 monolingual streaming | $0.0077/minute regular rate | Budget the full connected audio duration conservatively |
| Deepgram Aura-2 | $0.030 / 1,000 characters | Text synthesized, including cancelled and repeated speech |
| Gemini 3 Flash Preview | $0.50 / million input tokens; $3.00 / million output tokens | All calls and repeated text context; output includes billable reasoning |

Deepgram currently advertises a lower promotional Nova-3 rate ($0.0048/minute);
the example deliberately uses the regular rate. Validate account-specific rates,
quotas and billing before a rollout. Sources: [Deepgram pricing](https://deepgram.com/pricing)
and [Google's Gemini 3 pricing table](https://ai.google.dev/gemini-api/docs/gemini-3).

For each call, sum:

```text
STT cost = connected minutes × STT price/minute
TTS cost = characters synthesized ÷ 1,000 × TTS price/1,000
LLM cost = total input tokens ÷ 1,000,000 × input price
         + total output tokens ÷ 1,000,000 × output price
Monthly operating cost = calls × variable cost/call + fixed operating costs
```

Input/output tokens are totals over every model request in a contact, not just the
last turn. Longer dialogue resends context. Startup greetings, clarification and
conversation replies and LLM-generated idle/closing lines also incur usage. Embeddings/OCR, retry traffic, cloud hosting,
monitoring, maintenance, taxes, payment fees and any future telephony charges must
be included in the real budget. The example below assigns a hypothetical $180/month
fixed budget for hosting/admin/maintenance, not a vendor quote. Initial implementation
cost and telephony are excluded and should be added for an actual deployment.

## An illustrative founder planning scenario

Assume 1,000 contacts/month, four minutes/contact, 1,800 synthesized characters,
6,000 aggregate input tokens and 600 output tokens/contact. This gives approximately
**$0.0896 variable cost/contact**, $89.60 monthly API usage and **$269.60 total**
including the assumed $180 fixed budget. These usage levels are inputs to a model,
not measurements of this application's live calls.

Assume fully loaded staff time is $12/hour and AI-resolved contacts would otherwise
take four minutes each. Define containment as resolution without staff work or a
repeat contact during the chosen follow-up window.

| Assumed containment | Staff hours potentially released/month | Value at $12/hour | Capacity value less $269.60 operating cost |
| --- | ---: | ---: | ---: |
| 40% | 26.67 | $320 | $50.40 |
| 60% | 40.00 | $480 | $210.40 |
| 80% | 53.33 | $640 | $370.40 |

Released capacity is not automatically cash profit. It becomes a cash saving only
when paid hours, outsourcing or hiring actually fall. If staff time is redeployed,
measure the contribution margin from that work instead. Do not count both reduced
labor cost and the same hours' redeployment value. Bad answers, callbacks and human
review can erase the modeled benefit.

At the 60% containment assumption, the model's capacity-value break-even is about
**462 contacts/month**: fixed cost divided by (value released/contact minus variable
cost/contact), rounded up. It is not a sales target or a guaranteed ROI.

## Recalculate with your own numbers

```powershell
python scripts/estimate_costs.py --calls 1000 --minutes 4 --containment 0.6 --hourly-cost 12 --fixed-cost 180
```

The calculator is offline, editable and requires only Python's standard library.
Override vendor rates and measured token/character totals as billing changes.
Before scaling, compare a supervised pilot against your existing support process:
track resolution quality, customer satisfaction, cost per resolved contact and
actual paid staff time. Expand only when those measured results justify it.
