"""Tier 0: rules only. Regex plus checksums. No network, under 5 ms."""

from __future__ import annotations

import math
import re
from collections import Counter

from blackline.contract import Finding
from blackline.normalize import normalize


def luhn_ok(digits: str) -> bool:
    total = 0
    for i, d in enumerate(reversed(digits)):
        n = int(d)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def shannon_entropy(s: str) -> float:
    counts = Counter(s)
    return -sum((c / len(s)) * math.log2(c / len(s)) for c in counts.values())


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def _is_card(m: str) -> bool:
    d = _digits(m)
    return 13 <= len(d) <= 19 and d[0] in "3456" and luhn_ok(d)


def _is_sin(m: str) -> bool:
    d = _digits(m)
    return len(d) == 9 and d[0] not in "08" and luhn_ok(d)


def _is_ssn(m: str) -> bool:
    d = _digits(m)
    area = d[:3]
    return len(d) == 9 and area not in ("000", "666") and not area.startswith("9")


def _is_secret(m: str) -> bool:
    return len(m) >= 20 and shannon_entropy(m) > 3.5


RULES: list[tuple[str, re.Pattern[str], object, float]] = [
    ("CREDIT_CARD", re.compile(r"\b(?:\d[ -]?){13,19}\b"), _is_card, 0.97),
    ("CA_SIN", re.compile(r"\b\d{3}[ -]\d{3}[ -]\d{3}\b|\b\d{9}\b"), _is_sin, 0.90),
    ("US_SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), _is_ssn, 0.95),
    (
        "API_KEY",
        re.compile(
            r"\b(?:sk-(?:or-|ant-)?[A-Za-z0-9_-]{20,}|AKIA[A-Z0-9]{16}|ghp_[A-Za-z0-9]{36}"
            r"|xox[abp]-[A-Za-z0-9-]{20,}|AIza[0-9A-Za-z_-]{35})\b"
        ),
        _is_secret,
        0.99,
    ),
    ("IBAN", re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){3,7}\b"), lambda m: True, 0.85),
]


def detect(text: str) -> list[Finding]:
    out: list[Finding] = []
    taken: list[tuple[int, int]] = []
    for entity, rx, check, conf in RULES:
        for m in rx.finditer(text):
            span = (m.start(), m.end())
            if any(a < span[1] and span[0] < b for a, b in taken):
                continue
            if check(m.group()):
                taken.append(span)
                out.append(
                    Finding(entity=entity, start=span[0], end=span[1], confidence=conf, tier=0)
                )
    return out


CARD_CONTEXT = re.compile(
    r"\b(card|cc|visa|master ?card|amex|credit|debit|digits|number|num|exp|cvv|cvc|payment)\b",
    re.IGNORECASE,
)


def _scan_run(run: str, context: bool) -> str | None:
    if 13 <= len(run) <= 19:
        return "CREDIT_CARD" if _is_card(run) else ("CA_SIN" if _is_sin(run) else None)
    if len(run) == 9:
        return "CA_SIN" if _is_sin(run) else None
    if len(run) > 19 and context:
        # a card glued to other digits: only the start or end of the run, to limit false alarms
        for n in range(13, 20):
            if _is_card(run[:n]) or _is_card(run[-n:]):
                return "CREDIT_CARD"
    return None


def detect_folded(text: str) -> list[Finding]:
    """Rules on the folded text: emoji, other scripts, number words, look-alike letters.
    Spans cannot be mapped back, so a hit covers the whole message."""
    folded = normalize(text)
    if folded == text:
        return []
    context = bool(CARD_CONTEXT.search(folded))
    out: list[Finding] = []
    for run in re.findall(r"\d{9,}", folded):
        entity = _scan_run(run, context)
        if entity and entity not in {f.entity for f in out}:
            out.append(
                Finding(
                    entity=entity, start=0, end=len(text), confidence=0.9, tier=0, note="obfuscated"
                )
            )
    return out
