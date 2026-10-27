"""Create the explicitly configured synthetic caller once; never overwrite a customer."""
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

def main():
    from telecom_support.database import MongoSettings, connect
    from telecom_support.caller import seed_demo_caller
    settings = MongoSettings.load()
    phone = os.getenv("CALLER_PHONE")
    if not phone: raise ValueError("Set CALLER_PHONE in your private .env first.")
    client, database = connect(settings)
    try:
        row, created = seed_demo_caller(database, phone)
        print({"created": created, "customer_id": row["customer_id"], "username": row.get("username"),
               "display_name": row["full_name"], "smartcard_id": row.get("smartcard_id"), "data_type": row["data_type"]})
    finally: client.close()

if __name__ == "__main__": main()
