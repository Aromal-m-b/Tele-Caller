"""Resolve saved customer addresses without treating a city/state as a house address."""
import re


def text(value):
    if isinstance(value, str): return value.strip()
    if isinstance(value, int) and not isinstance(value, bool): return str(value)
    return ""


def format_address(value):
    if isinstance(value, str):
        result = value.strip()
        if re.sub(r"[^a-z ]", "", result.casefold()).strip() in {
            "my home", "my house", "same address", "saved address", "registered address", "address on file"}:
            return None
        return result if 5 <= len(result) <= 500 else None
    if not isinstance(value, dict): return None
    line1 = text(value.get("address_line1") or value.get("line1"))
    house = text(value.get("house_number") or value.get("house_no"))
    street = text(value.get("street_address") or value.get("street"))
    if not line1 and not street: return None
    if not line1 and not house: return None
    parts = [line1] if line1 else ["House " + house, "Street " + street]
    parts += [text(value.get("address_line2") or value.get("line2")), text(value.get("city")),
              text(value.get("state")), text(value.get("postal_code") or value.get("pincode")), text(value.get("country"))]
    result = ", ".join(part for part in parts if part)
    return result if 5 <= len(result) <= 500 else None


def customer_address(customer, *, delivery=False):
    fields = ("delivery_address", "home_address", "service_address", "address") if delivery else ("service_address", "home_address", "address", "delivery_address")
    for field in fields:
        result = format_address(customer.get(field))
        if result: return result
    return format_address(customer)
