# Missing-channel diagnosis and recommendations

Both Gemini Live and Deepgram Voice Agent can call `diagnose_channel_access`
with a channel name or ID. Natural-language intent and follow-ups belong to the
model; there is no phrase list for detecting a missing-channel complaint.
The existing webpage and dial code do not change. Restart your Python server
after updating the code.

The server obtains the synthetic identity from the configured simulated caller
number locally, then reads these existing MongoDB collections:

- `customers`: caller binding and account state.
- `subscriptions`: all owned package or individual-only subscription periods.
- `subscription_channels`: individual channel activation/status and optional expiry.
- `bouquets`, `bouquet_channels`, `channels`: package membership and catalog prices.

No new collections or seeded subscription purchases are necessary. A caller with
no subscription records is reported as having no current subscription. The tool
does not invent an active plan for the demo customer.

The eligible set is the deduplicated union of all date-valid active subscriptions
and their active, already-started add-ons. Suspended/inactive accounts do not grant
access. Date-only expiry is inclusive in India. Legacy add-ons without a separate
expiry inherit their parent subscription's end; an optional add-on `expiry_date`
can shorten that validity. An individual-only subscription uses a missing/null
`bouquet_id` and its channels come from `subscription_channels`. Standalone add-ons
without an owned parent subscription cannot grant access.

The result differentiates an entitled channel, a noncurrent subscription/add-on,
no current subscription, missing membership, unavailable catalog channel and an
inactive account. Unknown/ambiguous names require clarification, including SD/HD
variants. A subscription lookup cannot diagnose an actual signal or broadcast
outage. Already-entitled callers are offered troubleshooting or a complaint,
not another paid subscription.

For a missing entitlement, available individual-channel pricing and up to five
active packages containing the channel are returned, with packages ordered by
monthly catalog price. Each package lists actual included/extra channels and
channels lost if replacing all current plans. Adding a package and replacing
plans are different actions; the catalog package price is not an upgrade delta.
Zero-priced channels must not be pitched as paid add-ons.

The model can ask about budget and viewing interests and offer a more suitable
higher-value package with factual benefits. It must also present the individual
option and cheapest suitable package. No fabricated discounts, false urgency,
hidden trade-offs or continued pressure after a refusal. Prices are synthetic,
illustrative monthly amounts; taxes, base fees, proration, final quotes and
activation eligibility are not verified. Negotiation means discussing documented
value and alternatives, not authority to alter prices.

This tool is read-only. No payment, recharge, renewal or account change occurs.
Callers who want to proceed receive guidance to the official recharge process.
No phone, customer/subscription IDs, contact details or verification secrets are
included in its returned view. Deepgram retains the existing
`AGENT_SHARE_SYNTHETIC_SERVICE_DETAILS` sharing gate; Gemini Live exposes only this
scoped channel view. Unknown callers and failed reads never fall back to another
customer or a guessed entitlement. Duplicate identifiers, broken mappings,
invalid prices and excessive result sets fail closed.
