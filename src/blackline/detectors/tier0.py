"""Tier 0: rules only. Regex plus checksums. No network, under 5 ms."""

from __future__ import annotations

import math
import re
from collections import Counter
from itertools import pairwise

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


STRICT_KEY_PREFIXES = ("AKIA", "ghp_", "xoxb-", "xoxp-", "xoxa-", "AIza")


def _is_secret(m: str) -> bool:
    # strict formats are already specific enough; entropy only guards loose ones like sk-
    if m.startswith(STRICT_KEY_PREFIXES):
        return True
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


# ---------------------------------------------------------------------------
# Digit groups with filler between them: "4111 xx 1111 vv 1111 nn 1111"
# ---------------------------------------------------------------------------

MAX_GAP_CHARS = 8  # spaces plus one short filler word


def _chains(text: str) -> list[list[tuple[int, int, str]]]:
    """Runs of digit groups where each gap is short, on one line, and at most one word."""
    chains: list[list[tuple[int, int, str]]] = []
    for m in re.finditer(r"\d+", text):
        g = (m.start(), m.end(), m.group())
        if chains and chains[-1]:
            gap = text[chains[-1][-1][1] : g[0]]
            if len(gap) <= MAX_GAP_CHARS and "\n" not in gap and len(gap.split()) <= 1:
                chains[-1].append(g)
                continue
        chains.append([g])
    return [c for c in chains if len(c) >= 2]


def _has_filler(text: str, chain: list[tuple[int, int, str]]) -> bool:
    return any(re.search(r"[^\W\d_]", text[a[1] : b[0]]) for a, b in pairwise(chain))


def _standalone_card_shape(text: str, chain: list[tuple[int, int, str]]) -> bool:
    """A message that is only grouped card-shaped digits is suspicious, even if
    the checksum fails or the first digit is not a real card prefix."""
    start, end = chain[0][0], chain[-1][1]
    if text[:start].strip() or text[end:].strip():
        return False
    return all(re.fullmatch(r"[\s-]+", text[a[1] : b[0]]) for a, b in pairwise(chain))


def detect_grouped(text: str, whole_span: bool = False) -> list[Finding]:
    """Cards whose groups are split by filler words, and card-shaped numbers that fail the
    checksum. Card-shaped needs evidence of intent: filler between groups or a card word."""
    context = bool(CARD_CONTEXT.search(text))
    for chain in _chains(text):
        for i in range(len(chain)):
            for j in range(i + 1, len(chain)):
                part = chain[i : j + 1]
                digits = "".join(g[2] for g in part)
                if len(digits) > 19:
                    break
                shape = [len(g[2]) for g in part]
                start, end = (0, len(text)) if whole_span else (part[0][0], part[-1][1])
                if _is_card(digits):
                    return [
                        Finding(
                            entity="CREDIT_CARD",
                            start=start,
                            end=end,
                            confidence=0.9,
                            tier=0,
                            note="grouped",
                        )
                    ]
                card_shaped = shape in ([4, 4, 4, 4], [4, 6, 5])
                if card_shaped and (
                    context or _has_filler(text, part) or _standalone_card_shape(text, part)
                ):
                    return [
                        Finding(
                            entity="CARD_LIKE",
                            start=start,
                            end=end,
                            confidence=0.75,
                            tier=0,
                            note="grouped",
                        )
                    ]
    return []


def detect_all(text: str) -> list[Finding]:
    """Every rule layer, cheapest first. Raw text keeps exact spans for masking."""
    found = detect(text) or detect_grouped(text) or detect_folded(text)
    if not found:
        folded = normalize(text)
        if folded != text:
            found = [
                f.model_copy(update={"note": "obfuscated"})
                for f in detect_grouped(folded, whole_span=True)
            ]
            for f in found:
                f.end = len(text)
    return found
