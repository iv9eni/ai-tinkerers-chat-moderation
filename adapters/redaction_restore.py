"""Short-lived in-memory restore tickets for false-positive redactions.

The original message is intentionally not written to disk. A restart clears every ticket,
which keeps the durable queue and audit log free of raw sensitive text.
"""

from __future__ import annotations

import os
import secrets
import threading
import time
from dataclasses import dataclass


@dataclass
class RestoreTicket:
    id: str
    channel_id: str
    author_id: str
    text: str
    created_at: float
    redacted_ts: str = ""
    thread_ts: str = ""


class RestoreStore:
    def __init__(self, ttl_s: int | None = None):
        self.ttl_s = ttl_s or int(os.environ.get("REDACTION_RESTORE_TTL_SECONDS", "900"))
        self._items: dict[str, RestoreTicket] = {}
        self._lock = threading.Lock()

    def create(self, channel_id: str, author_id: str, text: str, thread_ts: str = "") -> str:
        self.prune()
        ticket_id = secrets.token_urlsafe(18)
        with self._lock:
            self._items[ticket_id] = RestoreTicket(
                id=ticket_id,
                channel_id=channel_id,
                author_id=author_id,
                text=text,
                created_at=time.time(),
                thread_ts=thread_ts,
            )
        return ticket_id

    def attach_redacted(self, ticket_id: str, redacted_ts: str) -> None:
        with self._lock:
            if ticket := self._items.get(ticket_id):
                ticket.redacted_ts = redacted_ts

    def consume(self, ticket_id: str) -> RestoreTicket | None:
        with self._lock:
            ticket = self._items.pop(ticket_id, None)
        if ticket and time.time() - ticket.created_at <= self.ttl_s:
            return ticket
        return None

    def prune(self) -> int:
        cutoff = time.time() - self.ttl_s
        with self._lock:
            expired = [k for k, v in self._items.items() if v.created_at < cutoff]
            for key in expired:
                del self._items[key]
        return len(expired)


def manager_ids() -> set[str]:
    raw = os.environ.get("MANAGER_USER_IDS", "")
    return {part.strip() for part in raw.replace(";", ",").split(",") if part.strip()}
