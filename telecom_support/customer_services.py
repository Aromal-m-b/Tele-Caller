"""Caller-scoped drafts, confirmed MongoDB writes and allowlisted service views."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
import re
import threading
import time
from typing import Literal
import uuid

from bson.decimal128 import Decimal128
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .account_tools import ToolError
from .service_schema import mongo_transaction

PRODUCTS = {"DISH": "Satellite dish", "SET_TOP_BOX": "Set-top box", "COAXIAL_CABLE": "Coaxial cable",
    "DUPLICATE_SMARTCARD": "Duplicate smartcard", "REMOTE": "Remote control", "CONNECTOR": "Cable connector",
    "POWER_ADAPTER": "Power adapter"}
ProductCode = Literal["DISH", "SET_TOP_BOX", "COAXIAL_CABLE", "DUPLICATE_SMARTCARD", "REMOTE", "CONNECTOR", "POWER_ADAPTER"]


class StrictArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ComplaintArgs(StrictArgs):
    category: Literal["payment_not_reflected", "no_signal", "technician_misconduct", "poor_support", "pricing_concern", "picture_quality", "billing_dispute", "other"]
    description: str = Field(min_length=5, max_length=2000)
    priority: Literal["Low", "Normal", "High", "Urgent"] = "Normal"
    order_id: str | None = Field(default=None, pattern=r"^ORD-[A-F0-9]{16}$")
    delivery_id: str | None = Field(default=None, pattern=r"^DLV-[A-F0-9]{16}$")


class ServiceArgs(StrictArgs):
    service_type: Literal["service_booking", "new_connection", "relocation", "cancellation"]
    description: str = Field(min_length=5, max_length=2000)
    location_address: str | None = Field(default=None, min_length=5, max_length=500)
    preferred_window: str | None = Field(default=None, min_length=3, max_length=150)
    contact_name: str | None = Field(default=None, min_length=2, max_length=100)

    @model_validator(mode="after")
    def address_for_visit(self):
        if self.service_type in {"new_connection", "relocation", "service_booking"} and not self.location_address:
            raise ValueError("A visit/installation address is required")
        return self


class OrderItem(StrictArgs):
    product_code: ProductCode
    quantity: int = Field(default=1, ge=1, le=10, strict=True)
    length_meters: float | None = Field(default=None, gt=0, le=300, allow_inf_nan=False, strict=True)

    @model_validator(mode="after")
    def cable_length(self):
        if self.product_code == "COAXIAL_CABLE" and self.length_meters is None:
            raise ValueError("Please specify cable length in meters")
        if self.product_code != "COAXIAL_CABLE" and self.length_meters is not None:
            raise ValueError("Only cable has a length")
        return self


class OrderArgs(StrictArgs):
    items: list[OrderItem] = Field(min_length=1, max_length=6)
    delivery_address: str = Field(min_length=5, max_length=500)
    notes: str = Field(default="", max_length=1000)
    service_request_id: str | None = Field(default=None, pattern=r"^REQ-[A-F0-9]{16}$")


class RequestLookup(StrictArgs):
    request_id: str | None = Field(default=None, pattern=r"^REQ-[A-F0-9]{16}$")
    limit: int = Field(default=3, ge=1, le=5, strict=True)


class OrderLookup(StrictArgs):
    order_id: str | None = Field(default=None, pattern=r"^ORD-[A-F0-9]{16}$")
    limit: int = Field(default=3, ge=1, le=5, strict=True)


class DeliveryLookup(StrictArgs):
    delivery_id: str | None = Field(default=None, pattern=r"^DLV-[A-F0-9]{16}$")
    order_id: str | None = Field(default=None, pattern=r"^ORD-[A-F0-9]{16}$")


DEFINITIONS = {
    "get_service_context": (StrictArgs, "Check locally whether the caller number matches a synthetic customer. Never ask for a customer ID or phone number."),
    "draft_complaint": (ComplaintArgs, "Prepare a complaint form: missing payment, no signal, technician misconduct, support, billing or pricing. Draft only; ask for explicit submission confirmation."),
    "draft_service_request": (ServiceArgs, "Prepare a service visit, new connection, relocation or cancellation REQUEST. Collect address for a visit and preferred window if supplied. No subscription changes or confirmed appointment."),
    "draft_equipment_order": (OrderArgs, "Prepare a dish, set-top box, cable, duplicate smartcard, remote, connector or adapter order REQUEST. Collect quantities, cable length and delivery address. Prices/stock are not known; no payment or dispatch."),
    "get_service_requests": (RequestLookup, "Read this caller's service requests/complaints, optionally by request reference. Omit ID to list recent requests."),
    "get_orders": (OrderLookup, "Read this caller's equipment orders and linked delivery records. Omit ID to list recent orders."),
    "track_delivery": (DeliveryLookup, "Read this caller's recorded delivery location, ETA, holder/courier, charge, item IDs and address. Omit IDs for the latest delivery. Unknown fields stay unknown."),
}


def tool_definitions():
    return [dict(name=name, description=description, parameters=schema.model_json_schema(), defer_until_eot=True)
            for name, (schema, description) in DEFINITIONS.items()]


def json_safe(value):
    if isinstance(value, datetime): return value.replace(tzinfo=value.tzinfo or timezone.utc).isoformat()
    if isinstance(value, Decimal128): return str(value.to_decimal())
    if isinstance(value, dict): return {k: json_safe(v) for k, v in value.items() if k != "_id"}
    if isinstance(value, list): return [json_safe(v) for v in value]
    return value


def scalar(value):
    """Reject nested/unexpected values in fields documented as scalar service facts."""
    if isinstance(value, (dict, list, tuple)): return None
    if isinstance(value, float) and not math.isfinite(value): return None
    if isinstance(value, Decimal128) and not value.to_decimal().is_finite(): return None
    if isinstance(value, str): return value[:4000]
    if value is None or isinstance(value, (datetime, Decimal128, int, float, bool)): return json_safe(value)
    return None


def affirmative(text):
    value = " ".join(re.sub(r"[^\w\s']", " ", text.casefold()).split())
    action = r"(?:submit|register|file|place|book|confirm) (?:it|(?:(?:the|this) )?(?:complaint|request|form|order|booking|appointment))"
    return bool(re.fullmatch(r"(?:yes|yeah|yep|sure|absolutely|of course|(?:please )?go ahead|please do|please " + action + r"|confirm|confirmed)"
        r"(?: (?:yes|now|please|go ahead|of course|and|" + action + r"|confirm|thanks|thank you))*", value))


def submission_question(text):
    value = " ".join(text.casefold().split())
    return value.endswith("?") and bool(re.search(r"\b(?:shall i|should i|can i|may i|would you like|do you want|please confirm|is it (?:okay|ok) (?:if|to)|do i have your (?:permission|confirmation))\b.{0,100}"
        r"\b(?:submit|register|file|place|book|proceed|confirm)\b", value))


class CustomerServices:
    def __init__(self, account, caller, *, call_id="", share_details=False, transact=mongo_transaction, clock=time.monotonic):
        self.account, self.caller, self.db = account, caller, account.db
        self.call_id, self.share_details, self.transact, self.clock = call_id, share_details, transact, clock
        self.pending, self.armed_at = None, float("inf")
        self.lock = threading.RLock()

    def customer(self):
        row = self.account._customer()
        if row.get("data_type") != "SYNTHETIC" or row.get("account_status", "").casefold() not in {"active", "suspended"}:
            raise ToolError("SYNTHETIC_CUSTOMER_REQUIRED", "This simulated caller cannot access that customer record.")
        if not self.caller.customer or row["customer_id"] != self.caller.customer["customer_id"]:
            raise ToolError("CALLER_MISMATCH", "The bound caller identity does not match this session.")
        return row

    def owned(self, collection, key, identifier, customer, *, session=None):
        options = {"session": session} if session is not None else {}
        row = self.db[collection].find_one({key: identifier, "customer_id": customer["customer_id"], "data_type": "SYNTHETIC"}, {"_id": 0}, **options)
        if not row: raise ToolError("NOT_FOUND", "That reference was not found for this caller.")
        return row

    def execute(self, name, arguments):
        if name not in DEFINITIONS: raise ToolError("UNKNOWN_ACTION", "Unknown service action.")
        args = DEFINITIONS[name][0].model_validate(arguments).model_dump()
        with self.lock:
            if name == "get_service_context":
                return {"ok": True, "recognized_synthetic_customer": bool(self.caller.customer),
                        "new_connection_enquiries_allowed": True, "phone_lookup_is_local": True}
            if name.startswith("draft_"):
                return self.draft(name, args)
            customer = self.customer()
            base = {"customer_id": customer["customer_id"], "data_type": "SYNTHETIC"}
            if name == "get_service_requests":
                if args["request_id"]: base["request_id"] = args["request_id"]
                rows = list(self.db.service_requests.find(base, {"_id": 0}).sort("created_at", -1).limit(args["limit"]))
                return {"ok": True, "service_requests": [self.view("service_requests", r) for r in rows]}
            if name == "get_orders":
                if args["order_id"]: base["order_id"] = args["order_id"]
                rows = list(self.db.orders.find(base, {"_id": 0}).sort("created_at", -1).limit(args["limit"]))
                result = []
                for row in rows:
                    order = self.view("orders", row)
                    if row.get("delivery_id"):
                        delivery = self.owned("deliveries", "delivery_id", row["delivery_id"], customer)
                        if delivery["order_id"] != row["order_id"] or set(delivery["item_ids"]) != {item["item_id"] for item in row["items"]}:
                            raise ToolError("BROKEN_LINK", "Order tracking requires data correction.")
                        order["delivery"] = self.view("deliveries", delivery)
                    result.append(order)
                return {"ok": True, "orders": result}
            if args["delivery_id"]: base["delivery_id"] = args["delivery_id"]
            if args["order_id"]: base["order_id"] = args["order_id"]
            row = self.db.deliveries.find_one(base, {"_id": 0}, sort=[("created_at", -1)])
            if row:
                order = self.owned("orders", "order_id", row["order_id"], customer)
                if order.get("delivery_id") != row["delivery_id"] or set(row["item_ids"]) != {item["item_id"] for item in order["items"]}:
                    raise ToolError("BROKEN_LINK", "Order tracking requires data correction.")
            return {"ok": True, "delivery": self.view("deliveries", row) if row else None,
                    "selection": "latest" if not args["delivery_id"] and not args["order_id"] else "requested reference"}

    def draft(self, name, args):
        new_customer = None
        if name == "draft_service_request" and args["service_type"] == "new_connection" and not self.caller.customer:
            phone = self.caller.phone
            existing = self.db.customers.find_one({"$or": [{"phone_normalized": phone}, {"phone": {"$in": [phone, phone[3:], phone[1:]]}}]})
            if existing: raise ToolError("CALLER_UNAVAILABLE", "Account access for this number is unavailable; no new identity was created.")
            if not args["contact_name"]: raise ToolError("CONTACT_NAME_REQUIRED", "Please provide the new connection contact name.")
            customer_id = "CUST-" + uuid.uuid4().hex[:16].upper()
            new_customer = dict(customer_id=customer_id, full_name=args["contact_name"], phone=phone,
                phone_normalized=phone, data_type="SYNTHETIC", account_status="Active", customer_lifecycle="Prospect")
        else:
            customer_id = self.customer()["customer_id"]
        if self.pending and self.pending["name"] == name and self.pending["args"] == args and self.pending["expires_at"] > self.clock():
            self.armed_at = float("inf")
            return self.preview()
        customer = {"customer_id": customer_id}
        if name == "draft_complaint":
            if args["order_id"]: self.owned("orders", "order_id", args["order_id"], customer)
            if args["delivery_id"]:
                delivery = self.owned("deliveries", "delivery_id", args["delivery_id"], customer)
                if args["order_id"] and delivery["order_id"] != args["order_id"]:
                    raise ToolError("BROKEN_LINK", "The supplied order and delivery do not match.")
                args["order_id"] = delivery["order_id"]
        if name == "draft_equipment_order" and args["service_request_id"]:
            self.owned("service_requests", "request_id", args["service_request_id"], customer)
        draft_id = uuid.uuid4().hex
        self.pending = dict(name=name, args=args, customer_id=customer_id, caller_phone=self.caller.phone,
            new_customer=new_customer, draft_id=draft_id, expires_at=self.clock() + 600,
            request_id="REQ-" + uuid.uuid4().hex[:16].upper(), order_id="ORD-" + uuid.uuid4().hex[:16].upper(),
            delivery_id="DLV-" + uuid.uuid4().hex[:16].upper(), created_at=datetime.now(timezone.utc))
        self.armed_at = float("inf")
        return self.preview()

    def preview(self):
        draft = self.pending
        fields = {key: value for key, value in draft["args"].items() if key != "contact_name" and value is not None}
        return {"ok": True, "draft_id": draft["draft_id"], "draft": fields, "requires_confirmation": True,
            "instruction": "Read back the supplied details and explicitly ask whether to submit this draft. No record, payment, dispatch or subscription change has occurred. Prices, appointment and delivery times are unconfirmed."}

    def arm(self, draft_id, at):
        with self.lock:
            if self.pending and self.pending["draft_id"] == draft_id: self.armed_at = at

    def resolve_reply(self, text, started_at):
        """Called only for real final caller text, never a model function argument."""
        with self.lock:
            if not self.pending: return None
            if self.pending["expires_at"] <= self.clock():
                self.pending = None
                return {"ok": False, "message": "The draft expired. Please prepare it again."}
            value = " ".join(re.sub(r"[^\w\s']", " ", text.casefold()).split())
            if value in {"no", "no thanks", "cancel", "cancel it", "not now", "never mind"}:
                self.pending = None
                return {"ok": True, "status": "DraftCancelled", "message": "The unsubmitted draft was cancelled. No existing order or subscription was cancelled."}
            if affirmative(text):
                if started_at < self.armed_at:
                    return {"ok": False, "message": "Explicit confirmation is needed after the complete draft question has played. Ask again; nothing submitted."}
                return self.confirm()
            self.armed_at = float("inf")
            return None

    def confirm(self):
        """Internal only. The agent tool surface deliberately has no confirm function."""
        if self.pending: self.pending.setdefault("submitted_at", datetime.now(timezone.utc))
        draft = deepcopy(self.pending)
        if not draft: raise ToolError("NO_DRAFT", "No pending form exists.")
        if draft["caller_phone"] != self.caller.phone: raise ToolError("CALLER_MISMATCH", "Caller changed; prepare a new draft.")
        if not draft["new_customer"] and self.customer()["customer_id"] != draft["customer_id"]:
            raise ToolError("CALLER_MISMATCH", "Caller changed; prepare a new draft.")
        base = dict(customer_id=draft["customer_id"], data_type="SYNTHETIC", schema_version=1,
            idempotency_key=draft["draft_id"], call_id=self.call_id, status="Submitted", created_at=draft["submitted_at"],
            updated_at=draft["submitted_at"])
        args = draft["args"]
        def persist(session):
            kwargs = {"session": session} if session is not None else {}
            if draft["new_customer"]:
                row = draft["new_customer"]
                number = row["phone_normalized"]
                other = self.db.customers.find_one({"$or": [{"phone_normalized": number},
                    {"phone": {"$in": [number, number[3:], number[1:]]}}]}, **kwargs)
                if other and other["customer_id"] != row["customer_id"]:
                    raise ToolError("CALLER_CHANGED", "A customer was registered for this number. Restart the call before submitting.")
                self.db.customers.update_one({"customer_id": row["customer_id"]}, {"$setOnInsert": row}, upsert=True, **kwargs)
            else:
                matches = list(self.db.customers.find({"customer_id": draft["customer_id"], "data_type": "SYNTHETIC"}, **kwargs).limit(2))
                if len(matches) != 1 or matches[0].get("account_status", "").casefold() not in {"active", "suspended"}:
                    raise ToolError("CALLER_CHANGED", "The customer is no longer available for submission.")
            if draft["name"] == "draft_complaint":
                if args["order_id"]: self.owned("orders", "order_id", args["order_id"], base, session=session)
                if args["delivery_id"]:
                    linked = self.owned("deliveries", "delivery_id", args["delivery_id"], base, session=session)
                    if args["order_id"] and linked["order_id"] != args["order_id"]:
                        raise ToolError("BROKEN_LINK", "The supplied order and delivery do not match.")
            if draft["name"] == "draft_equipment_order" and args["service_request_id"]:
                self.owned("service_requests", "request_id", args["service_request_id"], base, session=session)
            if draft["name"] == "draft_equipment_order":
                items = [dict(item, item_id=f"{draft['order_id']}-ITEM-{index+1}", product_name=PRODUCTS[item["product_code"]], unit_price_inr=None)
                         for index, item in enumerate(args["items"])]
                order = dict(base, order_id=draft["order_id"], items=items, delivery_id=draft["delivery_id"],
                    service_request_id=args["service_request_id"], delivery_address=args["delivery_address"], notes=args["notes"],
                    status="AwaitingQuote", total_price_inr=None, currency="INR")
                delivery = dict(base, delivery_id=draft["delivery_id"], order_id=draft["order_id"],
                    service_request_id=args["service_request_id"], item_ids=[item["item_id"] for item in items],
                    status="NotDispatched", delivery_address=args["delivery_address"], current_location=None,
                    current_holder=None, courier=None, estimated_arrival_at=None, delivery_charge_inr=None, currency="INR", tracking_events=[])
                self.db.orders.update_one({"order_id": order["order_id"], "customer_id": base["customer_id"]}, {"$setOnInsert": order}, upsert=True, **kwargs)
                self.db.deliveries.update_one({"delivery_id": delivery["delivery_id"], "customer_id": base["customer_id"]}, {"$setOnInsert": delivery}, upsert=True, **kwargs)
                saved_order = self.db.orders.find_one({"order_id": order["order_id"], "customer_id": base["customer_id"]}, **kwargs)
                saved_delivery = self.db.deliveries.find_one({"delivery_id": delivery["delivery_id"], "customer_id": base["customer_id"]}, **kwargs)
                return {"ok": True, "order": self.view("orders", saved_order), "delivery": self.view("deliveries", saved_delivery),
                    "message": "Order request recorded; quote, stock, dispatch and delivery time are not confirmed. No payment taken."}
            request_type = "complaint" if draft["name"] == "draft_complaint" else args["service_type"]
            row = dict(base, request_id=draft["request_id"], request_type=request_type,
                description=args["description"], category=args.get("category"), priority=args.get("priority", "Normal"),
                order_id=args.get("order_id"), delivery_id=args.get("delivery_id"), location_address=args.get("location_address"),
                preferred_window=args.get("preferred_window"), scheduled_at=None)
            self.db.service_requests.update_one({"request_id": row["request_id"], "customer_id": base["customer_id"]}, {"$setOnInsert": row}, upsert=True, **kwargs)
            saved = self.db.service_requests.find_one({"request_id": row["request_id"], "customer_id": base["customer_id"]}, **kwargs)
            return {"ok": True, "service_request": self.view("service_requests", saved),
                "message": "Request registered for review. No payment, refund, subscription cancellation, relocation or appointment has been performed or guaranteed."}
        result = self.transact(self.db, persist)
        if draft["new_customer"]: self.caller.bind(draft["new_customer"])
        self.pending, self.armed_at = None, float("inf")
        return result

    @staticmethod
    def view(collection, row):
        keys = {
            "service_requests": ("request_id", "request_type", "description", "category", "priority", "status", "order_id", "delivery_id", "location_address", "preferred_window", "scheduled_at", "created_at", "updated_at"),
            "orders": ("order_id", "items", "delivery_id", "service_request_id", "status", "total_price_inr", "currency", "delivery_address", "notes", "created_at", "updated_at"),
            "deliveries": ("delivery_id", "order_id", "service_request_id", "item_ids", "status", "current_location", "current_holder", "courier", "estimated_arrival_at", "delivery_charge_inr", "currency", "delivery_address", "tracking_events", "created_at", "updated_at")}
        result = {key: scalar(row.get(key)) for key in keys[collection]}
        if collection == "orders":
            item_keys = ("item_id", "product_code", "product_name", "quantity", "length_meters", "unit_price_inr")
            result["items"] = [{k: scalar(item.get(k)) for k in item_keys} for item in row.get("items", []) if isinstance(item, dict)]
        if collection == "deliveries":
            result["item_ids"] = [value[:100] for value in row.get("item_ids", []) if isinstance(value, str)]
            event_keys = ("at", "status", "location", "holder", "note")
            result["tracking_events"] = [{k: scalar(event.get(k)) for k in event_keys} for event in row.get("tracking_events", []) if isinstance(event, dict)][-10:]
        return json_safe(result)

    def for_agent(self, result):
        if self.share_details: return json.dumps(json_safe(result), ensure_ascii=True)
        if result.get("requires_confirmation"): return json.dumps(result, ensure_ascii=True)
        return json.dumps({"ok": result.get("ok", False), "message": "The local service result is shown on screen; private database values are not shared in this session."})
