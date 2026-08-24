"""
Mock action store. Real deployment would write to Trendly's order-management
system / a ticketing queue (Zendesk, etc.) - this is a stand-in so the demo
is stateful and idempotent without touching orders.json.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import itertools

_counter = itertools.count(1)

# key: (order_id, sku) -> list of open action dicts, so we can check "already
# has an open return" before creating a duplicate (idempotency)
_ACTIONS: dict[str, list[dict]] = {}
_TICKETS: list[dict] = []


def _key(order_id: str, sku: str) -> str:
    return f"{order_id}:{sku}"


def find_open_action(order_id: str, sku: str, action_type: str) -> Optional[dict]:
    for a in _ACTIONS.get(_key(order_id, sku), []):
        if a["type"] == action_type and a["status"] == "open":
            return a
    return None


def create_action(order_id: str, sku: str, action_type: str, **details) -> dict:
    action = {
        "id": f"ACT-{next(_counter):04d}",
        "order_id": order_id,
        "sku": sku,
        "type": action_type,
        "status": "open",
        "created_at": datetime.utcnow().isoformat(),
        **details,
    }
    _ACTIONS.setdefault(_key(order_id, sku), []).append(action)
    return action


def create_escalation(order_id: Optional[str], reason: str, summary: str, priority: str = "normal") -> dict:
    ticket = {
        "id": f"ESC-{next(_counter):04d}",
        "order_id": order_id,
        "reason": reason,
        "summary": summary,
        "priority": priority,
        "created_at": datetime.utcnow().isoformat(),
        "status": "open",
    }
    _TICKETS.append(ticket)
    return ticket


def all_actions_for_order(order_id: str) -> list[dict]:
    out = []
    for acts in _ACTIONS.values():
        out.extend(a for a in acts if a["order_id"] == order_id)
    return out
