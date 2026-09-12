"""run(message) -> Decision. The only function adapters call."""

from __future__ import annotations

import time

from blackline import audit
from blackline.contract import Decision, Message
from blackline.detectors import tier0, tier1
from blackline.policy import Policy

_policy: Policy | None = None


def policy() -> Policy:
    global _policy
    if _policy is None:
        _policy = Policy.load()
    return _policy


def reload_policy() -> None:
    global _policy
    _policy = Policy.load()


def run(msg: Message, use_tier1: bool = True) -> Decision:
    timing: dict[str, int | None] = {}
    text = msg.text + "".join("\n" + a.text for a in msg.attachments if a.text)

    t = time.perf_counter()
    findings = tier0.detect(text)
    timing["tier0"] = int((time.perf_counter() - t) * 1000)
    timing["tier1"] = None
    model = None

    trigger = policy().tier1_trigger(msg)
    need_tier1 = use_tier1 and trigger != "never" and (trigger == "always" or not findings)
    if need_tier1 and text.strip():
        t = time.perf_counter()
        more, meta = tier1.detect(text)
        timing["tier1"] = int((time.perf_counter() - t) * 1000)
        findings += more
        model = meta.get("model")

    decision = policy().decide(msg, findings)
    decision.timing_ms = timing
    decision.model = model
    audit.record(msg, decision)
    return decision
