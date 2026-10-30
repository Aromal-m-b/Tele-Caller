# Tele-Caller: turn conversations into customer support outcomes

**A voice assistant that answers questions, understands an issue, and captures the
next step—so your team can focus on resolving it.**

## The pitch

Small teams often do support while running the rest of the business. Repeating
service explanations, collecting issue details and checking order updates takes
time away from customers who need a person's judgment.

Tele-Caller offers a working voice assistant for those repeatable interactions.
Customers speak naturally. The assistant uses approved knowledge for answers,
keeps conversational context, and can prepare complaints, service requests and
equipment orders. It asks before submitting and acknowledges the result returned
by the database. Staff can review the structured record and private call transcript.

The current product demonstrates this using a telecom/DTH business and synthetic
customers. It is a local application you can run and evaluate, with source code
you can adapt. An optional [Twilio/ngrok bridge](twilio-phone.md) connects inbound
pilot calls to the managed agent. It is not a hosted telephone answering service.

## What an owner gets today

| Product capability | What it does in the current application |
| --- | --- |
| Voice conversation | Streaming recognition and spoken responses; generated opening and idle notices |
| Interruption handling | Clears pending reply audio when caller speech is detected |
| Knowledge-grounded answers | Retrieves relevant approved documents from a local FAISS index |
| Conversation context | Supports follow-ups; the standard pipeline keeps recent text and a compressed older archive |
| Caller-aware support | Uses a simulated phone number as a local lookup key for synthetic customers |
| Complaint intake | Captures signal issues, missing payment credit, billing/pricing concerns, technician behavior and other support issues |
| Service intake | Records visit, new-connection, relocation and cancellation requests |
| Equipment requests | Captures supported equipment, quantities and cable length when relevant |
| Delivery enquiries | Reads linked delivery records, including location, holder, ETA, charges and destination when recorded |
| Saved addresses | Reuses the customer record for relevant requests; relocation needs a new destination |
| Confirmed submission | Model-based interpretation of caller consent, with local identity, review and transaction checks |
| Traceability | Private text transcripts, timing diagnostics and usage/cost estimates with unavailable fields identified |

Complaints, equipment and delivery tools run in the managed Deepgram backend.
Verified account/subscription lookup and the earlier ticket/DOCX workflow run in
the standard backend. The [README comparison](../README.md#choose-the-workflow-you-want-to-demonstrate)
helps you choose; these are not all one combined workflow.

## Show it in five minutes

Prepare the managed backend with approved demo knowledge, MongoDB service tools,
a synthetic caller and a saved synthetic address. Enable synthetic service-detail
sharing only for data you are authorized to send to the hosted agent.
Follow [service configuration](customer-services.md) before demonstrating writes.

| Demo step | Show the audience | What it proves |
| --- | --- | --- |
| Open a session | The assistant introduces itself without waiting for the first question | Voice-led interaction |
| Ask a documented service question | The agent retrieves information and answers aloud | Knowledge access |
| Explain a problem in ordinary speech | The issue becomes a complaint draft using the saved address | Structured intake |
| Approve the reviewed draft | A committed service request and reference appear in the local result | A completed intake action |
| Ask for equipment | Items and destination become an order draft, submitted only with approval | Linked order/delivery workflow |
| Ask for a delivery update | The agent uses recorded facts and identifies unknown ETA or charges | Evidence-based status support |
| End the call | The local session closes and its transcript/usage log is available | Reviewable call outcome |

Use your own wording. This is a demonstration sequence, not a canned conversation.
For an order just created by the demo, expect an awaiting-quote/not-dispatched
state. No payment, stock reservation or courier dispatch happens automatically.

## Where business value can come from

The opportunity is to move routine explanations and intake away from staff while
preserving useful records for the work that still needs a person.

| Outcome to evaluate | How to measure it |
| --- | --- |
| Less repetitive work | Staff minutes spent per comparable contact, including review and callbacks |
| Better intake | Requests accepted by staff without missing details or caller recontact |
| Useful self-service | Questions resolved accurately without repeat contact in a defined follow-up window |
| A smoother conversation | Caller corrections, interruptions, repeated questions and response delay |
| Sustainable operating cost | Total cost per handled contact and per successfully resolved contact |

Use actual pilot results in a business decision:

```text
Staff hours released = comparable baseline staff hours − pilot staff hours
Capacity value = staff hours released × fully loaded hourly staff cost
Net capacity value = capacity value − added operating and implementation costs
```

Pilot staff hours must include supervision, correction and repeat contacts.
Released time becomes cash savings only when paid work, outsourcing or hiring
actually decreases. If the team uses that time elsewhere, measure the benefit
of that work instead; do not count the same hours twice.

Operating costs include provider usage, any extra model intent calls, embeddings,
hosting, maintenance and future telephone charges. Logs contain estimates, not
invoices; managed-agent hosted token counts are unavailable. No fixed savings,
profit or latency improvement is claimed. Use the [cost worksheet](business-case.md)
with rates applicable to your chosen provider accounts.

## From this product demo to a deployed business service

| Available in this repository | Additional work for a real deployment |
| --- | --- |
| Local voice session or allowlisted inbound Twilio/ngrok pilot | Production routing, availability and capacity validation |
| Synthetic caller binding | Trusted authentication and authorization for real customers |
| Reference telecom knowledge and tools | Approved business content, persona and domain-specific workflows |
| Local transcripts and diagnostics | Controlled retention, access, monitoring and operational alerts |
| Request records for staff review | Staff work queue, ownership, notifications and human transfer integration |
| One conversation per local process | Concurrency, tenant isolation, budgets and service operations |

Do not present the demo as an automatic payment/refund processor, confirmed
appointment scheduler, parcel dispatcher or human call-transfer system. Those
integrations are not implemented. The reference assistant is not an official
Sun Direct representative, and fictional demo data is not business policy.

## Start with one workflow

Run the [Windows demo](quickstart.md), choose one high-volume support workflow,
and test it with synthetic calls. Review the answers and records, measure staff
follow-up and cost, then decide which production integrations are justified.

**The offer: a working, inspectable starting point for voice support that goes
beyond answering a question and captures the next customer-service action.**
