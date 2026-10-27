"""Read-only, identity-scoped customer tools and joined package catalog queries."""
from dataclasses import dataclass
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
import re

INDIA = timezone(timedelta(hours=5, minutes=30))
DATA_NOTICE = "Synthetic test dataset. Package and channel prices are illustrative, not verified official tariffs."


class ToolError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


@dataclass(frozen=True)
class CustomerIdentity:
    customer_id: str
    verified: bool = False
    synthetic_demo: bool = False


def truth(value):
    return value is True or str(value).strip().lower() in {"true", "1", "yes"}


def confirmation_text(text):
    return " ".join(re.sub(r"[,.!?]", " ", text.lower()).split())


def explicit_confirmation(text):
    return bool(re.fullmatch(r"(?:yes|yes please|confirm|confirmed|yes confirm|yes continue|continue with it|go ahead)",
                             confirmation_text(text)))


def timestamp(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time(), tzinfo=INDIA)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=INDIA) if parsed.tzinfo is None else parsed
    except (TypeError, ValueError):
        raise ToolError("INVALID_DATE", "A stored account date could not be parsed.") from None


def money(value):
    try:
        if hasattr(value, "to_decimal"):
            value = value.to_decimal()
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0:
            raise ValueError
        return str(amount.quantize(Decimal("0.01")))
    except (InvalidOperation, ValueError, TypeError):
        raise ToolError("INVALID_AMOUNT", "A stored price or payment amount is invalid.") from None


def serial(record):
    result = {}
    for key, value in record.items():
        if key == "_id":
            continue
        if isinstance(value, (date, datetime)):
            result[key] = value.isoformat()
        elif hasattr(value, "to_decimal"):
            result[key] = str(value.to_decimal())
        else:
            result[key] = value
    return result


class AccountTools:
    """All customer IDs come from trusted session identity, never tool arguments."""
    def __init__(self, database, identity=None, *, now=None):
        self.db, self.identity = database, identity
        self.now = now or (lambda: datetime.now(INDIA))
        self.pending_recharge = None
        self.current_user_text = ""
        self.turn_number = 0
        self.caller_session = None
        self.ticket_service = None

    def begin_customer_verification(self):
        if self.caller_session is None:
            raise ToolError("VERIFICATION_UNAVAILABLE", "Caller verification is not configured.")
        return {"verification_event": self.caller_session.begin_verification()}

    def prepare_support_ticket(self, issue_type, description, priority="Normal", contact_method="Phone"):
        if not self.ticket_service:
            raise ToolError("TICKET_UNAVAILABLE", "Ticket registration is not configured.")
        return self.ticket_service.prepare(issue_type, description, priority, contact_method)

    def begin_turn(self, text):
        self.turn_number += 1
        self.current_user_text = text

    def _customer(self):
        identity = self.identity
        if not identity or not identity.verified:
            raise ToolError("AUTHENTICATION_REQUIRED", "Authenticate the customer through the trusted session before reading personal or account data.")
        if not isinstance(identity.customer_id, str) or not identity.customer_id:
            raise ToolError("AUTHENTICATION_REQUIRED", "The session has no valid customer identity.")
        rows = list(self.db.customers.find({"customer_id": identity.customer_id}, {"_id": 0}).limit(2))
        if not rows:
            raise ToolError("CUSTOMER_NOT_FOUND", "No customer matches the authenticated session.")
        if len(rows) != 1:
            raise ToolError("AMBIGUOUS_CUSTOMER", "Duplicate customer identifiers require data correction.")
        customer = rows[0]
        if identity.synthetic_demo and str(customer.get("data_type", "")).upper() != "SYNTHETIC":
            raise ToolError("DEMO_IDENTITY_REJECTED", "Demo mode can only access records explicitly marked SYNTHETIC.")
        return customer

    def _one(self, collection, query, code="RELATED_RECORD_NOT_FOUND"):
        rows = list(self.db[collection].find(query, {"_id": 0}).limit(2))
        if len(rows) != 1:
            raise ToolError(code, "A related record is missing or duplicated; no account action can be inferred.")
        return rows[0]

    def _bouquet(self, bouquet_id):
        record = self._one("bouquets", {"bouquet_id": bouquet_id}, "PACKAGE_NOT_FOUND")
        result = serial(record)
        result["monthly_price_inr"] = money(record.get("monthly_price_inr"))
        result["is_active"] = truth(record.get("is_active"))
        return result

    def _channels(self, bouquet_id):
        mappings = list(self.db.bouquet_channels.find({"bouquet_id": bouquet_id}, {"_id": 0}).limit(1000))
        ids = {row["channel_id"] for row in mappings}
        channels = list(self.db.channels.find({"channel_id": {"$in": list(ids)}}, {"_id": 0}).limit(1000))
        if len(channels) != len(ids):
            raise ToolError("BROKEN_CHANNEL_MAPPING", "The package has missing or duplicate channel records.")
        result = []
        for row in channels:
            item = serial(row)
            item["is_active"] = truth(row.get("is_active"))
            item["is_hd"] = truth(row.get("is_hd"))
            item["monthly_price_inr"] = money(row.get("monthly_price_inr"))
            result.append(item)
        return sorted(result, key=lambda item: item["channel_name"])

    def get_customer_profile(self):
        customer = self._customer()
        fields = ("customer_id", "full_name", "email", "phone", "city", "state", "country", "registered_on", "account_status", "data_type")
        return {"profile": serial({k: customer[k] for k in fields if k in customer})}

    def _subscription_details(self, subscription, customer):
        if subscription.get("customer_id") != customer["customer_id"]:
            raise ToolError("OWNERSHIP_MISMATCH", "Subscription does not belong to the authenticated customer.")
        result = serial(subscription)
        now = self.now()
        start, expiry = timestamp(subscription["start_date"]), timestamp(subscription["expiry_date"])
        if expiry < start:
            raise ToolError("INVALID_VALIDITY", "Subscription expiry precedes its start date.")
        # CSV date-only validity is inclusive of the expiry day in India.
        date_only = isinstance(subscription["expiry_date"], str) and len(subscription["expiry_date"]) == 10
        within = (start.date() <= now.astimezone(INDIA).date() <= expiry.date() if date_only
                  else start <= now <= expiry)
        result.update(is_currently_valid=(within and str(subscription.get("subscription_status", "")).lower() == "active"
                                         and str(customer.get("account_status", "")).lower() == "active"),
                      checked_at=now.isoformat(), auto_renew=truth(subscription.get("auto_renew")),
                      bouquet=self._bouquet(subscription["bouquet_id"]))
        return result

    def get_active_plan(self):
        customer = self._customer()
        records = list(self.db.subscriptions.find({"customer_id": customer["customer_id"]}, {"_id": 0}).limit(500))
        if not records:
            raise ToolError("SUBSCRIPTION_NOT_FOUND", "No subscription records exist for this customer.")
        details = [self._subscription_details(row, customer) for row in records]
        valid = [row for row in details if row["is_currently_valid"]]
        if len(valid) > 1:
            raise ToolError("AMBIGUOUS_ACTIVE_PLAN", "More than one valid subscription exists; a selection is required.")
        selected = valid[0] if valid else max(details, key=lambda row: timestamp(row["start_date"]))
        return {"has_active_plan": bool(valid), "subscription": selected}

    def get_active_channels(self):
        customer = self._customer()
        plan = self.get_active_plan()
        if not plan["has_active_plan"]:
            return {"has_active_plan": False, "channels": [], "subscription": plan["subscription"]}
        subscription = plan["subscription"]
        channels = {row["channel_id"]: row for row in self._channels(subscription["bouquet_id"]) if row["is_active"]}
        additions = self.db.subscription_channels.find({"subscription_id": subscription["subscription_id"]}, {"_id": 0}).limit(1000)
        for addition in additions:
            if str(addition.get("status", "")).lower() != "active" or timestamp(addition["activated_on"]) > self.now():
                continue
            channel = self._one("channels", {"channel_id": addition["channel_id"]})
            if truth(channel.get("is_active")):
                channels[channel["channel_id"]] = serial(channel)
        return {"has_active_plan": True, "subscription_id": subscription["subscription_id"],
                "channels": sorted(channels.values(), key=lambda row: row["channel_name"])}

    def get_payment_history(self, limit=5):
        customer = self._customer()
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
            raise ToolError("INVALID_LIMIT", "Payment history limit must be between 1 and 20.")
        records = list(self.db.payments.find({"customer_id": customer["customer_id"]}, {"_id": 0}).limit(2000))
        records.sort(key=lambda row: (timestamp(row["payment_date"]), str(row["payment_id"])), reverse=True)
        for row in records:
            row["amount_inr"] = money(row["amount_inr"])
        return {"payments": [serial(row) for row in records[:limit]]}

    def check_payment_status(self, payment_id):
        customer = self._customer()
        if not isinstance(payment_id, str):
            raise ToolError("INVALID_PAYMENT_ID", "A payment identifier must be text.")
        row = self._one("payments", {"customer_id": customer["customer_id"], "payment_id": payment_id}, "PAYMENT_NOT_FOUND")
        row["amount_inr"] = money(row["amount_inr"])
        return {"payment": serial(row)}

    def get_previous_recharge(self):
        customer = self._customer()
        self.pending_recharge = None
        records = list(self.db.payments.find({"customer_id": customer["customer_id"]}, {"_id": 0}).limit(2000))
        successful = [row for row in records if str(row.get("payment_status", "")).lower() == "successful"
                      and timestamp(row["payment_date"]) <= self.now()]
        if not successful:
            raise ToolError("SUCCESSFUL_RECHARGE_NOT_FOUND", "No successful previous recharge was found.")
        successful.sort(key=lambda row: timestamp(row["payment_date"]), reverse=True)
        if len(successful) > 1 and timestamp(successful[0]["payment_date"]) == timestamp(successful[1]["payment_date"]):
            raise ToolError("AMBIGUOUS_PREVIOUS_RECHARGE", "Multiple successful payments share the latest timestamp; specify a payment before choosing.")
        payment = successful[0]
        subscription = self._one("subscriptions", {"subscription_id": payment["subscription_id"],
                                                   "customer_id": customer["customer_id"]}, "PAYMENT_SUBSCRIPTION_MISMATCH")
        details = self._subscription_details(subscription, customer)
        payment["amount_inr"] = money(payment["amount_inr"])
        eligible = details["bouquet"]["is_active"] and str(customer.get("account_status", "")).lower() == "active"
        result = {"payment": serial(payment), "subscription": details,
                  "channels": self._channels(subscription["bouquet_id"]),
                  "can_select_for_continuation": eligible, "confirmation_required": True,
                  "payment_executed": False, "subscription_changed": False,
                  "note": "Historical amount is not a renewal quote. Ask the customer whether they want to continue with this package; this tool cannot recharge or renew it."}
        if eligible:
            self.pending_recharge = {"customer_id": customer["customer_id"], "payment_id": payment["payment_id"],
                                     "bouquet_id": subscription["bouquet_id"], "prepared_turn": self.turn_number,
                                     "catalog_price": details["bouquet"]["monthly_price_inr"]}
        return result

    def confirm_recharge_choice(self):
        customer = self._customer()
        if not self.pending_recharge or self.pending_recharge["customer_id"] != customer["customer_id"]:
            raise ToolError("NO_PENDING_SELECTION", "Look up and present the previous recharge first.")
        if self.pending_recharge["prepared_turn"] >= self.turn_number:
            raise ToolError("SELECTION_NOT_YET_PRESENTED", "Present the package details and wait for confirmation in a later turn.")
        if not explicit_confirmation(self.current_user_text):
            raise ToolError("EXPLICIT_CONFIRMATION_REQUIRED", "The current customer turn must explicitly confirm the presented selection.")
        package = self._bouquet(self.pending_recharge["bouquet_id"])
        if (not package["is_active"] or str(customer.get("account_status", "")).lower() != "active"
                or package["monthly_price_inr"] != self.pending_recharge["catalog_price"]):
            self.pending_recharge = None
            raise ToolError("SELECTION_CHANGED", "Package availability, price or account status changed; look up and confirm the details again.")
        selected = dict(self.pending_recharge)
        selected["bouquet_name"] = package["bouquet_name"]
        self.pending_recharge = None
        return {"selection_confirmed": selected, "payment_executed": False,
                "subscription_changed": False, "next_step": "Guide the customer through the official recharge process; no payment or renewal has been performed."}

    def list_bouquets(self, language=""):
        if not isinstance(language, str) or len(language) > 80:
            raise ToolError("INVALID_LANGUAGE", "Use a short language name.")
        items = []
        for row in self.db.bouquets.find({}, {"_id": 0}).limit(200):
            if not truth(row.get("is_active")):
                continue
            if language and str(row.get("primary_language", "")).casefold() not in {language.casefold(), "multi"}:
                continue
            items.append(self._bouquet(row["bouquet_id"]))
        return {"bouquets": items}

    def compare_plans(self, first_bouquet_id, second_bouquet_id):
        if not all(isinstance(value, str) for value in (first_bouquet_id, second_bouquet_id)):
            raise ToolError("INVALID_PACKAGE_ID", "Package identifiers must be text.")
        first, second = self._bouquet(first_bouquet_id), self._bouquet(second_bouquet_id)
        left, right = self._channels(first_bouquet_id), self._channels(second_bouquet_id)
        a, b = {row["channel_id"] for row in left}, {row["channel_id"] for row in right}
        return {"first": {**first, "channels": left}, "second": {**second, "channels": right},
                "shared_channel_ids": sorted(a & b), "first_only_channel_ids": sorted(a - b),
                "second_only_channel_ids": sorted(b - a)}

    def recommend_plan(self, language, genres=None, exclude_genres=None, max_price_inr=None):
        wanted = {str(value).casefold() for value in (genres or [])}
        excluded = {str(value).casefold() for value in (exclude_genres or [])}
        limit = money(max_price_inr) if max_price_inr is not None else None
        candidates = []
        for bouquet in self.list_bouquets(language)["bouquets"]:
            if limit is not None and Decimal(bouquet["monthly_price_inr"]) > Decimal(limit):
                continue
            channels = self._channels(bouquet["bouquet_id"])
            available = [row for row in channels if row["is_active"]]
            all_genres = {str(row.get("genre", "")).casefold() for row in channels}
            if excluded & all_genres:
                continue
            matches = [row for row in available if str(row.get("language", "")).casefold() == language.casefold()
                       and (not wanted or str(row.get("genre", "")).casefold() in wanted)]
            matched_genres = {str(row["genre"]).casefold() for row in matches}
            if matches and wanted <= matched_genres:
                candidates.append({**bouquet, "channels": channels, "matching_channels": matches,
                                   "matched_genres": sorted(matched_genres),
                                   "exclusions_checked_against_all_package_channels": True})
        candidates.sort(key=lambda row: (-len(row["matched_genres"]), Decimal(row["monthly_price_inr"])))
        return {"recommendations": candidates[:5], "exact_match_found": bool(candidates)}

    def execute(self, name, arguments):
        allowed = {"get_customer_profile", "get_active_plan", "get_active_channels", "get_payment_history",
                   "check_payment_status", "get_previous_recharge", "confirm_recharge_choice",
                   "list_bouquets", "compare_plans", "recommend_plan", "begin_customer_verification", "prepare_support_ticket"}
        try:
            if name not in allowed:
                raise ToolError("UNKNOWN_TOOL", "This tool is not registered.")
            if not isinstance(arguments, dict) or "customer_id" in arguments:
                raise ToolError("INVALID_ARGUMENTS", "Customer identity cannot be supplied by a tool call.")
            result = getattr(self, name)(**arguments)
            return {"ok": True, "data_notice": DATA_NOTICE, **result}
        except ToolError as exc:
            return {"ok": False, "error": {"code": exc.code, "message": exc.message}}
        except TypeError:
            return {"ok": False, "error": {"code": "INVALID_ARGUMENTS", "message": "Invalid tool arguments."}}
        except Exception:
            # Connection errors can contain hosts/credentials. Never send their raw text to the model.
            return {"ok": False, "error": {"code": "DATABASE_UNAVAILABLE", "message": "Account data could not be read. Please retry; no payment or subscription changes were made."}}
