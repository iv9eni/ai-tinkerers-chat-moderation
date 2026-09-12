"""Audit log. One JSON line per decision, to a file and to the application log.

Never stores message text. The text fingerprint is an HMAC with a secret key: a plain
hash of a card number can be reversed by trying every valid card number, a keyed one
cannot without the key. Set AUDIT_HMAC_KEY in production so fingerprints stay stable
across restarts.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time

from blackline.contract import Decision, Message

log = logging.getLogger("blackline.audit")
_lock = threading.Lock()
_key: bytes | None = None


def _hmac_key() -> bytes:
    global _key
    if _key is None:
        configured = os.environ.get("AUDIT_HMAC_KEY", "")
        if configured:
            _key = configured.encode()
        else:
            _key = secrets.token_bytes(32)
            log.warning("AUDIT_HMAC_KEY not set: fingerprints change on every restart")
    return _key


def fingerprint(text: str) -> str:
    return hmac.new(_hmac_key(), text.encode(), hashlib.sha256).hexdigest()[:16]


def record(msg: Message, decision: Decision) -> dict:
    row = {
        "t": time.time(),
        "id": msg.id,
        "channel": msg.channel_name or msg.channel_id,
        "author": msg.author_id,
        "text_hmac": fingerprint(msg.text),
        "action": decision.action,
        "rule": decision.rule_id,
        "entities": [f.entity for f in decision.findings],
        "tier": max((f.tier for f in decision.findings), default=None),
        "related": len(decision.related_ids),
        "timing_ms": decision.timing_ms,
        "model": decision.model,
        "token_usage": decision.token_usage,
        "cost_usd": decision.cost_usd,
    }
    line = json.dumps(row)
    with _lock, open(os.environ.get("AUDIT_FILE", "audit.jsonl"), "a") as fh:
        fh.write(line + "\n")
    log.info(line)
    return row
