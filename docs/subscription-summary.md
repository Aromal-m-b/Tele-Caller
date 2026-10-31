# Plan summaries and channel drill-down

Both voice backends expose `get_subscription_summary` for the locally bound
synthetic caller. No customer or phone argument is accepted. The tool uses the
same date/status checks and deduplicated package/add-on union as missing-channel
diagnosis.

With `{}` it returns active package names, validity, total eligible channel count
and counts by language, genre, HD/SD and Free/Paid. It returns no channel list.
Noncurrent plans are labeled separately and never counted as active entitlements.
A caller without subscription records receives empty plan lists and zero channels.
The tool never assigns a plan or changes a subscription.

For a detailed follow-up, the model supplies any combination of:

```json
{"language":"Malayalam","genre":"Movies","quality":"SD","channel_type":"Paid","include_channels":true,"limit":10,"offset":0}
```

All filters are combined. Omit unknown or irrelevant filters. Follow `next_offset`
for additional pages; the maximum page size is 20. Totals refer to distinct active
channels, not duplicate package membership rows. Model conversation context carries
preferences between turns; no static speech phrase list selects these tools.

Missing language, genre or format stays `Unspecified`. In particular, the imported
Malayalam section establishes a language but does not establish individual movie,
news or entertainment genres. The generic HD section does not establish languages.
An empty genre-filter result means no matching *recorded* genre, not proof that
the customer's package has no such content. Free/Paid refers to the channel's
catalog price classification, not the overall package or service fee.

Tool results exclude phone numbers, customer/subscription identifiers, smartcards,
addresses, emails and profile names. Deepgram retains its synthetic service sharing
gate. Gemini Live receives the allowlisted summary directly. Both models are
instructed to summarize counts first and fetch names only for deeper questions.
