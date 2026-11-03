"""MongoDB connectivity; importing this module never connects or imports seed data."""
import os
import ipaddress
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
    # dnspython can return an empty resolver list on some Windows/Conda setups
    # even while Windows DNS tools have an active resolver. Allow operators to
    # provide the DNS server explicitly without embedding a network-specific
    # address in the application.
    dns_servers = [part.strip() for part in os.getenv("MONGODB_DNS_SERVERS", "").split(",") if part.strip()]
    for server in dns_servers:
        try:
            ipaddress.ip_address(server)
        except ValueError:
            raise ValueError("MONGODB_DNS_SERVERS must be comma-separated IP addresses.") from None
    if dns_servers:
        try:
            import dns.resolver
            resolver = dns.resolver.get_default_resolver()
            resolver.nameservers = dns_servers
        except Exception:
            raise ValueError("Could not configure the requested MongoDB DNS resolver.") from None
    try:
        client = MongoClient(settings.uri, appname="telecom-support-read-tools",
                             serverSelectionTimeoutMS=30000, connectTimeoutMS=20000,
                             socketTimeoutMS=30000, timeoutMS=45000, maxPoolSize=10)
    except Exception:
        raise ValueError("Could not initialize MongoDB. Check the connection configuration and network access.") from None
    return client, client[settings.database]
