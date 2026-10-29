"""Simulated caller identification and local synthetic name/card verification."""
import hmac
import re
import secrets
from datetime import datetime, timezone

from .account_tools import CustomerIdentity


def phone_number(value):
    digits = re.sub(r"\D", "", str(value))
    if len(digits) == 10:
        return "+91" + digits
    if len(digits) == 12 and digits.startswith("91"):
        return "+" + digits
    raise ValueError("Use a ten-digit Indian simulated caller number or its +91 form.")


def seed_demo_caller(database, phone):
    number = phone_number(phone)
    matches = list(database.customers.find({"$or": [
        {"phone_normalized": number}, {"phone": {"$in": [number, number[3:], number[1:]]}}]}).limit(2))
    if matches:
        if len(matches) != 1 or matches[0].get("data_type") != "SYNTHETIC":
            raise ValueError("Caller number already belongs to a non-synthetic or ambiguous record; nothing changed.")
        return matches[0], False
    row = dict(customer_id="CUST_DEMO_CALLER_0001", username="demo_caller", full_name="Demo Caller",
        email="demo.caller@example.invalid", phone=number, phone_normalized=number,
        smartcard_id="SC-DEMO-" + secrets.token_hex(6).upper(), city="Demo", state="Demo", country="India",
        registered_on=datetime.now(timezone.utc).date().isoformat(), account_status="Active", data_type="SYNTHETIC")
    if database.customers.find_one({"customer_id": row["customer_id"]}):
        raise ValueError("The demo customer ID already exists under another caller number; nothing changed.")
    database.customers.insert_one(row)
    return row, True


class CallerSession:
    def __init__(self, service, phone, *, call_id="", audit=None, allow_suspended=False):
        self.service, self.phone, self.call_id, self.audit = service, phone_number(phone), call_id, audit
        self.customer = None
        self.allow_suspended = allow_suspended
        self.stage, self.username, self.attempts = None, None, 0
        if service is not None:
            rows = list(service.db.customers.find({"$or": [{"phone_normalized": self.phone},
                {"phone": {"$in": [self.phone, self.phone[3:], self.phone[1:]]}}]}).limit(2))
            # A configured simulated caller number must never authenticate real accounts.
            if len(rows) == 1 and rows[0].get("data_type") == "SYNTHETIC":
                self.bind(rows[0])

    def bind(self, row):
        statuses = {"active", "suspended"} if self.allow_suspended else {"active"}
        if row.get("data_type") != "SYNTHETIC" or row.get("account_status", "").casefold() not in statuses:
            return False
        self.customer = row
        self.service.identity = CustomerIdentity(row["customer_id"], verified=True, synthetic_demo=True)
        if self.audit:
            self.audit.identity = row["customer_id"]
            self.audit.redact(row.get("smartcard_id", ""))
        return True

    @property
    def display_name(self):
        return str(self.customer.get("full_name", "")) if self.customer else ""

    def begin_verification(self):
        if self.customer:
            return "caller_verified"
        if self.service is None or self.attempts >= 3:
            return "verification_unavailable"
        self.stage = "name"
        return "verify_name"

    def consume_verification(self, text):
        if text.strip().casefold() in {"cancel", "never mind", "not now"}:
            self.stage, self.username = None, None
            return "verification_cancelled"
        if self.stage == "name":
            self.username = re.sub(r"^(?:my (?:user)?name is|i am|i'm|it is|it's)\s+", "", text.strip(), flags=re.I).strip(" .,!?")[:120]
            self.stage = "card"
            if self.audit: self.audit.redact(self.username)
            return "verify_card"
        if self.stage != "card":
            raise ValueError("No verification field is pending")
        card = re.sub(r"^(?:(?:my )?smart\s*card(?: id| number)? (?:is )?)", "", text.strip(), flags=re.I)
        if self.audit: self.audit.redact(card)
        self.attempts += 1
        rows = list(self.service.db.customers.find({"data_type": "SYNTHETIC", "$or": [
            {"username": {"$regex": "^" + re.escape(self.username) + "$", "$options": "i"}},
            {"full_name": {"$regex": "^" + re.escape(self.username) + "$", "$options": "i"}}]}).limit(2))
        self.stage, self.username = None, None
        def normalize_card(value):
            digits = dict(zero="0", one="1", two="2", three="3", four="4", five="5", six="6", seven="7", eight="8", nine="9")
            spoken = re.sub(r"\b(?:zero|one|two|three|four|five|six|seven|eight|nine)\b",
                lambda match: digits[match[0].lower()], str(value), flags=re.I)
            return re.sub(r"[^A-Z0-9]", "", spoken.upper())
        if len(rows) == 1 and hmac.compare_digest(normalize_card(rows[0].get("smartcard_id", "")), normalize_card(card)) and normalize_card(card):
            if self.bind(rows[0]): return "caller_verified"
        return "verification_failed"
