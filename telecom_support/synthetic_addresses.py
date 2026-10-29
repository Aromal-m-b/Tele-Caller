"""Explicit, idempotent backfill of fictional addresses for synthetic fixtures only."""
import secrets

from .addresses import customer_address
from .caller import seed_demo_caller


def seed_synthetic_addresses(database, phone, *, randbelow=secrets.randbelow):
    demo, created = seed_demo_caller(database, phone)
    counts = dict(demo_customer_created=created, addresses_added=0, existing_addresses_preserved=0,
                  partial_addresses_preserved=0)
    projection = {key: 1 for key in ("customer_id", "service_address", "home_address", "delivery_address", "address",
        "house_number", "house_no", "street_address", "street", "city", "state", "country")}
    for row in database.customers.find({"data_type": "SYNTHETIC"}, projection):
        if customer_address(row):
            counts["existing_addresses_preserved"] += 1
            continue
        if any(row.get(key) for key in ("service_address", "home_address", "delivery_address", "address", "house_number", "house_no", "street_address", "street")):
            counts["partial_addresses_preserved"] += 1
            continue
        house, street = 100 + randbelow(900), 100000 + randbelow(900000)
        address = dict(house_number=house, street_address=street)
        address.update({key: row[key] for key in ("city", "state", "country") if row.get(key)})
        result = database.customers.update_one({"_id": row["_id"], "data_type": "SYNTHETIC",
            "service_address": {"$exists": False}}, {"$set": dict(house_number=house, street_address=street,
                service_address=address, address_data_type="SYNTHETIC")})
        counts["addresses_added"] += result.modified_count
    counts["demo_customer_has_saved_address"] = bool(customer_address(database.customers.find_one({"customer_id": demo["customer_id"]})))
    return counts
