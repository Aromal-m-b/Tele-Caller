# Import a channel table from HTML

The importer extracts only the five TD cells in each table row, preserving
original text, HTML and attributes. Section labels and table headings supply
provenance. It never follows instructions embedded in the document or fetches
external image URLs.

Preview and back up the current catalog:

```powershell
.\.venv-livekit\Scripts\python.exe scripts/import_html_channels.py "C:\path\pretty.html"
```

Apply the validated import:

```powershell
.\.venv-livekit\Scripts\python.exe scripts/import_html_channels.py "C:\path\pretty.html" --apply
```

MongoDB settings come from the existing private `.env`. Exact channel-name and
HD/SD matches reuse their IDs. New channels get deterministic generated IDs;
repeat imports do not duplicate them. Unmatched existing channels are retained
because packages or individual subscriptions may refer to them. No package
membership, subscription, payment or customer record is changed.

Backups, complete planned documents and a summary are saved under the private
`output/imports` folder before mutation. Application uses an Atlas transaction,
checks that the catalog has not changed since planning, compares every imported
field to the plan, and checks package/add-on references before committing.
Invalid rows or ambiguous identities fail closed.

The supplied file contains 254 channels across 19 sections. Its initial merge
updates 30 matches, adds 224 new channels and retains six referenced legacy rows,
for 260 catalog records. No source data is uploaded to GitHub.

Numbers and price strings retain their original formatting. Numeric INR amounts
are stored separately as decimals. The file does not state a price period, so
monthly price and billing period remain null. A language, genre or availability
that the source does not establish is not invented. The catalog-active flag
means listed, not proof that a channel is currently broadcasting.
