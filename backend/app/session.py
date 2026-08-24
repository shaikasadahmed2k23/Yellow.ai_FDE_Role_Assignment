"""
In-memory session store, keyed by session_id. A real deployment would put
this in Redis (multi-instance, TTL, survives restarts) - noted as a
limitation in SOLUTION.md. For a single-process demo this is fine.
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Session:
    session_id: str
    customer_id: Optional[str] = None
    customer_name: Optional[str] = None
    active_order_id: Optional[str] = None
    messages: list = field(default_factory=list)  # full chat history incl. tool turns


_SESSIONS: dict[str, Session] = {}


def get_or_create(session_id: str) -> Session:
    if session_id not in _SESSIONS:
        _SESSIONS[session_id] = Session(session_id=session_id)
    return _SESSIONS[session_id]
