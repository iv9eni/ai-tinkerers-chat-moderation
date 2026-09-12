"""run(message, window) -> Decision. The only function adapters call."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from blackline import audit
from blackline.contract import Decision, Finding, Message
from blackline.detectors import split, tier0, tier1
from blackline.policy import Policy

_policy: Policy | None = None
_pool = ThreadPoolExecutor(max_workers=8)


def policy() -> Policy:
    global _policy
    if _policy is None:
        _policy = Policy.load()
    return _policy


def reload_policy() -> None:
    global _policy
    _policy = Policy.load()


def _ms(t: float) -> int:
    return int((time.perf_counter() - t) * 1000)


def _split_decision(
    msg: Message, chain: list[Message], entity: str, idx: list[int], conf: float, tier: int
) -> Decision:
    f = Finding(
        entity=entity,
        start=0,
        end=len(msg.text),
        confidence=conf,
        tier=tier,
        subject="unknown",
        note=f"split across {len(idx)} messages",
    )
    d = policy().decide(msg, [f])
    if d.action != "allow":
        d.related_ids = [chain[i].id for i in idx]
    return d


REMOVING = ("mask", "block", "quarantine")


def fast_check(msg: Message, window: list[Message] | None = None) -> Decision | None:
    """Rules only, no network calls. Returns a decision only when it removes the message,
    so the caller can delete before doing anything slower."""
    timing: dict[str, int | None] = {"tier0": None, "split": None, "tier1": None, "window": None}
    t = time.perf_counter()
    findings = tier0.detect_all(msg.text)
    timing["tier0"] = _ms(t)
    decision = policy().decide(msg, findings) if findings else None
    if decision is None or decision.action not in REMOVING:
        chain = [*(window or []), msg]
        if len(chain) > 1:
            t = time.perf_counter()
            hit = split.detect([m.text for m in chain])
            timing["split"] = _ms(t)
            if hit:
                decision = _split_decision(
                    msg, chain, hit.entity, hit.message_indexes, hit.confidence, 0
                )
    if decision is None or decision.action not in REMOVING:
        return None
    decision.timing_ms = timing
    return decision


def run(
    msg: Message,
    use_tier1: bool = True,
    window: list[Message] | None = None,
    audit_log: bool = True,
) -> Decision:
    """window: the same author's earlier messages in this channel, oldest first.
    audit_log=False when the caller records the audit line itself, after acting."""
    timing: dict[str, int | None] = {"tier0": None, "split": None, "tier1": None, "window": None}
    timing["encoded"] = None
    model = None
    text = msg.text

    # 1. rules on this message alone
    t = time.perf_counter()
    findings = tier0.detect_all(text)
    timing["tier0"] = _ms(t)
    decision = policy().decide(msg, findings) if findings else None

    chain = [*(window or []), msg]
    texts = [m.text for m in chain]

    # 2. rules across the author's recent messages
    if (decision is None or decision.action == "allow") and len(chain) > 1:
        t = time.perf_counter()
        hit = split.detect(texts)
        timing["split"] = _ms(t)
        if hit:
            decision = _split_decision(
                msg, chain, hit.entity, hit.message_indexes, hit.confidence, 0
            )

    # 3. model, only when rules found nothing. Single-message and window checks run in parallel.
    if decision is None or decision.action == "allow":
        trigger = policy().tier1_trigger(msg)
        single = use_tier1 and trigger != "never" and text.strip()
        windowed = use_tier1 and trigger != "never" and split.worth_asking_model(texts)
        jobs = {}
        t = time.perf_counter()
        if single:
            jobs["single"] = _pool.submit(tier1.detect, text)
        if windowed:
            jobs["window"] = _pool.submit(tier1.detect_window, texts)
        if use_tier1 and trigger != "never" and split.looks_encoded(text):
            jobs["encoded"] = _pool.submit(tier1.detect_encoded, text)
        if "window" in jobs:
            hits, meta = jobs["window"].result()
            timing["window"] = _ms(t)
            model = meta.get("model")
            if hits:
                entity, idx, conf = hits[0]
                decision = _split_decision(msg, chain, entity, idx, conf, 1)
        if "encoded" in jobs:
            hits, meta = jobs["encoded"].result()
            timing["encoded"] = _ms(t)
            model = model or meta.get("model")
            if hits and (decision is None or decision.action == "allow"):
                entity, conf = hits[0]
                f = Finding(
                    entity=entity,
                    start=0,
                    end=len(text),
                    confidence=conf,
                    tier=1,
                    note="obfuscated",
                )
                decision = policy().decide(msg, [f])
        if "single" in jobs:
            more, meta = jobs["single"].result()
            timing["tier1"] = _ms(t)
            model = model or meta.get("model")
            if decision is None or decision.action == "allow":
                findings += more
                decision = policy().decide(msg, findings)

    decision = decision or policy().decide(msg, findings)
    decision.timing_ms = timing
    decision.model = model
    if audit_log:
        audit.record(msg, decision)
    return decision
