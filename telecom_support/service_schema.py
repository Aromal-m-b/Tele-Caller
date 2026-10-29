"""Non-destructive setup of the three synthetic customer-service collections."""
from pymongo.errors import CollectionInvalid

COLLECTION_KEYS = {"service_requests": "request_id", "orders": "order_id", "deliveries": "delivery_id"}


def validator(collection):
    key = COLLECTION_KEYS[collection]
    required = [key, "customer_id", "status", "data_type", "created_at", "schema_version"]
    properties = {key: {"bsonType": "string"}, "customer_id": {"bsonType": "string"},
        "status": {"bsonType": "string"}, "data_type": {"enum": ["SYNTHETIC"]},
        "created_at": {"bsonType": "date"}, "schema_version": {"enum": [1]}}
    if collection == "service_requests":
        required += ["request_type", "description"]
        properties["request_type"] = {"enum": ["complaint", "service_booking", "new_connection", "relocation", "cancellation"]}
    elif collection == "orders":
        required += ["items", "delivery_id"]
        properties.update(items={"bsonType": "array", "minItems": 1}, delivery_id={"bsonType": "string"})
    else:
        required += ["order_id", "item_ids", "delivery_address"]
        properties.update(order_id={"bsonType": "string"}, item_ids={"bsonType": "array", "minItems": 1},
                          delivery_address={"bsonType": "string"})
    return {"$jsonSchema": {"bsonType": "object", "required": required, "properties": properties}}


def ensure_service_schema(database):
    """Create absent collections, never replace data or existing validation rules."""
    existing = set(database.list_collection_names())
    for name, key in COLLECTION_KEYS.items():
        if name not in existing:
            try:
                database.create_collection(name, validator=validator(name))
            except CollectionInvalid:
                pass  # Another startup may have created it concurrently.
        collection = database[name]
        collection.create_index(key, unique=True)
        collection.create_index("idempotency_key", unique=True, sparse=True)
        collection.create_index([("customer_id", 1), ("created_at", -1)])
        collection.create_index([("customer_id", 1), ("status", 1)])
    database.deliveries.create_index("order_id", unique=True)
    database.customers.create_index("phone_normalized", unique=True,
        partialFilterExpression={"phone_normalized": {"$type": "string"}})


def mongo_transaction(database, callback):
    """Atomic cross-collection writes; deployments without transactions fail closed."""
    from pymongo.write_concern import WriteConcern
    with database.client.start_session() as session:
        return session.with_transaction(callback, write_concern=WriteConcern("majority"), max_commit_time_ms=10000)
