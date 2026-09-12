"""Audit log. JSONL now, Firestore later. Never store raw sensitive spans."""

from __future__ import annotations

import hashlib
import json
import os
import time

from blackline.contract import Decision, Message


def record(msg: Message, decision: Decision) -> dict:
    row = {
        "t": time.time(),
        "id": msg.id,
        "channel": msg.channel_name or msg.channel_id,
        "author": msg.author_id,
        "text_sha": hashlib.sha256(msg.text.encode()).hexdigest()[:16],
        "action": decision.action,
        "rule": decision.rule_id,
        "entities": [f.entity for f in decision.findings],
        "tier": max((f.tier for f in decision.findings), default=None),
        "timing_ms": decision.timing_ms,
        "model": decision.model,
        "cost_usd": decision.cost_usd,
    }
    with open(os.environ.get("AUDIT_FILE", "audit.jsonl"), "a") as fh:
        fh.write(json.dumps(row) + "\n")
    return row
