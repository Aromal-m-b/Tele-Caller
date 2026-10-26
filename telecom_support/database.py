"""MongoDB connectivity; importing this module never connects or imports seed data."""
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

from .settings import ROOT


@dataclass(frozen=True)
class MongoSettings:
    uri: str = field(repr=False)
    database: str = "telecaller"

    @classmethod
    def load(cls):
        load_dotenv(ROOT / ".env")
        uri = os.getenv("MONGODB_URI", "")
        return cls(uri, os.getenv("MONGODB_DATABASE", "telecaller"))


def connect(settings):
    if not settings.uri:
        raise ValueError("Set MONGODB_URI to enable database tools.")
    from pymongo import MongoClient
    try:
        client = MongoClient(settings.uri, appname="telecom-support-read-tools",
                             serverSelectionTimeoutMS=8000, connectTimeoutMS=8000,
                             socketTimeoutMS=10000, timeoutMS=15000, maxPoolSize=10)
    except Exception:
        raise ValueError("Could not initialize MongoDB. Check the connection configuration and network access.") from None
    return client, client[settings.database]
