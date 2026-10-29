"""Backfill fictional fixture addresses without changing existing complete addresses."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    from telecom_support.database import MongoSettings, connect
    from telecom_support.synthetic_addresses import seed_synthetic_addresses
    settings = MongoSettings.load()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--caller-number", default=os.getenv("CALLER_PHONE"), help="Existing simulated caller number; defaults to CALLER_PHONE")
    args = parser.parse_args()
    if not args.caller_number: parser.error("Set CALLER_PHONE or pass --caller-number.")
    client, database = connect(settings)
    try:
        print(json.dumps(seed_synthetic_addresses(database, args.caller_number), indent=2))
    finally:
        client.close()


if __name__ == "__main__": main()
