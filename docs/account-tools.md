# Read-only MongoDB account tools

Account/subscription/payment operations remain read-only. A separate confirmed
write now creates `support_tickets` and a local DOCX. See
[callers, tickets and logs](callers-tickets-logs.md). Caller-number/name/card checks
are a synthetic simulation, not production telephone authentication.

The archived CSV loader was inspected but never executed. Its deployed database is
`telecaller`, which contains all seven collections and record counts described in
`metadata_csv.md`. The other documented name, `sundirect_support`, was empty when
checked. The schema header now reflects the deployed name.

Connection settings are in the project `.env`:

```dotenv
MONGODB_URI=<the existing cluster connection string>
MONGODB_DATABASE=telecaller
```

The original loader's connection string was reused locally without printing it or
running its inserts. Application code reads environment configuration; it does not
import the archived loader. Dependencies are included in `requirements.txt`.

## Try the customer flow

From the project folder in PowerShell:

```powershell
.\.venv-voice\Scripts\python.exe main.py --demo-customer CUST00001
```

Speak the following requests and read the replies in the terminal:

```text
Get me the previous one I recharged. I need to continue with it.
yes
What is the validity of my package?
Show my payment history.
```

Wait for the package details to appear before saying `yes`. A confirmation spoken
while those details were still being prepared is not applied; the app asks you to
confirm again. For keyboard testing, add `--text` and choose `1` from the menu.

The first lookup selects the latest **successful** payment, joins its owned
subscription and bouquet, and shows the package, amount paid, payment date/method,
included channels and current validity. A newer failed payment is not selected.
The historical payment amount is distinguished from the illustrative catalog price.
An inactive package or suspended account cannot be offered for continuation.

`yes` on the next turn confirms the package **choice only**. No payment is made,
subscription renewed, or database record changed. `no` cancels the pending choice.
Confirmation is handled locally, so it does not need another model call. Catalog
availability and price are rechecked before confirming. Same-turn confirmation
without first presenting the package is rejected.

## Registered tools

| Tool | Purpose |
| --- | --- |
| `get_customer_profile` | Authenticated customer's personal details and account status |
| `get_active_plan` | Current subscription, package and date-based validity |
| `get_active_channels` | Valid package channels plus active add-ons, deduplicated |
| `get_payment_history` | Newest-first payments with success/failure status |
| `check_payment_status` | A payment scoped to the authenticated customer |
| `get_previous_recharge` | Latest successful recharge joined to package and validity |
| `confirm_recharge_choice` | Confirm the displayed package choice in session memory only |
| `list_bouquets` | Active catalog packages by language |
| `compare_plans` | Prices, channel counts, shared and exclusive channel IDs |
| `recommend_plan` | Packages meeting language, genre, exclusion and budget requirements |

The dataset is explicitly synthetic and catalog prices are illustrative, not live
Tech Five tariffs. Package recommendations use `bouquet_channels` membership,
not guesses based on independent channel records. Genre exclusions are checked
against every channel in a package. Required genres must all be represented among
the requested language's active channels.

## Identity and data flow

Personal tools have no customer-ID argument. They use `CustomerIdentity` attached
by trusted application code. A customer ID spoken or typed in chat does not grant
access. Without a verified session, personal tools return `AUTHENTICATION_REQUIRED`.

`--demo-customer` is an explicit test-only operator selection. It works only for
customers marked `data_type=SYNTHETIC`; it is not production authentication. A real
login/verification service must attach the authenticated customer identity before
these tools can be exposed to real customers. That login service is outside this
change. `--no-database` disables the database tools entirely.

Gemini receives tool schemas and chooses read operations. Customer database values
are retrieved and formatted locally. They are not sent back as model tool results,
put in prompt history, or archived in compressed conversation memory. History keeps
only a generic note naming the lookup; follow-ups re-query the database for fresh
facts. The customer sees the actual result directly. Ordinary customer-written
chat messages continue through the existing Gemini text flow.

Automatic approval review rejected an implementation that sent database-returned
personal/payment data to Gemini. Local result rendering was used instead.

## Validation

```powershell
.\.venv-voice\Scripts\python.exe -m pytest
.\.venv-voice\Scripts\python.exe -m tests.qualify_accounts
.\.venv-voice\Scripts\python.exe -m tests.qualify_account_conversation
```

The integration runners use the explicitly synthetic `CUST00001` fixture, perform
reads only, and save pass/fail metadata rather than personal profiles or payment
values. See `reports/account-tools-integration.json` and
`reports/account-conversation-integration.json`.
