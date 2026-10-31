# Synthetic memberships from the imported HTML

The user selected rebuilding the demo packages using the HTML sections. These
are generated Tech Five demo memberships, not package contents published by the
HTML's original provider. Existing illustrative package prices are unchanged.

| Package | Source rule | Channels |
|---|---|---:|
| Tamil Starter | Tamil: Free SD only | 9 |
| Tamil Family | Tamil section | 27 |
| Malayalam Starter | Malayalam: Free SD only | 11 |
| Malayalam Family | Malayalam section | 21 |
| Telugu Starter | Telugu: Free SD only | 4 |
| Telugu Family | Telugu section | 22 |
| Kannada Family | Kannada section | 15 |
| Hindi Entertainment | Hindi section, including formats listed there | 20 |
| Sports Plus | Sports section, including formats listed there | 10 |
| South Combo | Tamil, Telugu, Kannada, Malayalam sections | 85 |
| Kids and Learning | Kids and Infotainment sections | 10 |
| All-Rounder | All imported sections, including HD | 254 |

The total is 488 package-channel links. Channel features are taken only from the
original section and TD data. No language is guessed for channels in the general
HD section. All-Rounder includes those entries as listed.

Subscriptions retain their existing package IDs and join the rebuilt
`bouquet_channels` records. All 100 synthetic subscriptions receive a catalog
revision marker. Dates, status, customer, billing cycle and payment records are
not changed. This rebuild does not create a subscription for a caller with none.

All 25 individual add-ons remain. Two same-format synthetic alias migrations
point to imported records: Flowers TV to Flowers, and Sony Entertainment Television
to SET. Their previous IDs and migration basis are recorded; activation dates and
statuses remain unchanged. Exact format matching prevents an SD-to-HD upgrade.
The six historical catalog records remain for reference; two have canonical
pointers, and unmapped historical labels require clarification instead of a sale.
All package and add-on links now target imported channel records.

Preview, back up, then optionally apply:

```powershell
.\.venv-livekit\Scripts\python.exe scripts/rebuild_demo_memberships.py
.\.venv-livekit\Scripts\python.exe scripts/rebuild_demo_memberships.py --apply
```

This command supports only the twelve reviewed demo package labels and explicitly
synthetic subscription owners. Backups and plans are private files under
`output/membership-rebuilds`. The Atlas transaction checks for concurrent changes,
replaces memberships, updates aliases and revision markers, and verifies original
subscription terms, add-on statuses and catalog source fields before committing.
It never infers actual purchased entitlements for real customers.
