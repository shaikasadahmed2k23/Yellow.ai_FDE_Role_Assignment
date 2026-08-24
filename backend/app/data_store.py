"""
Read-only data access layer over orders.json.

Design note: orders.json is loaded once at startup and never mutated in place.
"Actions" like initiate_return/initiate_exchange write to a separate in-memory
action_log (see actions.py) rather than editing the source dataset, so the
fixed evaluation data stays intact across a whole run.
"""
import json
import os
from typing import Optional

_DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "orders.json")

with open(_DATA_PATH, "r", encoding="utf-8") as f:
    _RAW = json.load(f)

CUSTOMERS = {c["customer_id"]: c for c in _RAW["customers"]}
ORDERS = {o["order_id"]: o for o in _RAW["orders"]}

# secondary indexes for identity resolution
_EMAIL_INDEX = {c["email"].lower(): c["customer_id"] for c in _RAW["customers"]}
_PHONE_INDEX = {c["phone"]: c["customer_id"] for c in _RAW["customers"]}


def find_customer_by_contact(email: Optional[str] = None, phone: Optional[str] = None) -> Optional[dict]:
    """Resolve a customer_id from an email or phone number. Returns the customer
    record (dict) or None if no match. This is the only identity entry point —
    every order-specific tool downstream requires a resolved customer_id."""
    cid = None
    if email:
        cid = _EMAIL_INDEX.get(email.strip().lower())
    if not cid and phone:
        cid = _PHONE_INDEX.get(phone.strip())
    return CUSTOMERS.get(cid) if cid else None


def get_order(order_id: str) -> Optional[dict]:
    return ORDERS.get(order_id.strip().upper())


def get_orders_for_customer(customer_id: str) -> list[dict]:
    return [o for o in ORDERS.values() if o["customer_id"] == customer_id]


def order_belongs_to_customer(order_id: str, customer_id: str) -> bool:
    """The core anti-leakage check. Every tool that touches a specific order
    must call this before returning any data about it."""
    order = get_order(order_id)
    return bool(order) and order["customer_id"] == customer_id
