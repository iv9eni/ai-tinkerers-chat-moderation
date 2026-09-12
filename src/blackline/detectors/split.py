"""Find card numbers and SINs that were split across one author's recent messages.

Deterministic and free. Joins neighbouring digit groups across messages and checks
them with the same validators as tier 0. A context word is required, so unrelated
numbers in a busy chat do not combine into a false alarm.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from blackline.detectors.tier0 import _is_card, _is_sin
from blackline.normalize import digit_groups, normalize

CONTEXT = {
    "CREDIT_CARD": re.compile(
        r"\b(card|cc|visa|master ?card|amex|credit|debit|digits|number|num|exp|cvv|cvc|payment)\b",
        re.IGNORECASE,
    ),
    "CA_SIN": re.compile(r"\b(sin|social insurance|t4|payroll|digits|number)\b", re.IGNORECASE),
}
MAX_GROUPS = 8


@dataclass
class SplitHit:
    entity: str
    message_indexes: list[int]
    confidence: float


def detect(texts: list[str]) -> SplitHit | None:
    """texts: one author's recent messages, oldest first, current message last."""
    groups: list[tuple[str, int]] = []
    for i, t in enumerate(texts):
        groups += [(g, i) for g in digit_groups(t)]
    if len(groups) < 2:
        return None
    context = " ".join(normalize(t) for t in texts)
    last = len(texts) - 1
    for start in range(len(groups)):
        digits, used = "", []
        for g, idx in groups[start : start + MAX_GROUPS]:
            digits += g
            used.append(idx)
            if len(digits) > 19:
                break
            spans_messages = len(set(used)) > 1
            # only report hits that include the current message, older ones were already judged
            if not spans_messages or last not in used:
                continue
            for entity, check in (("CREDIT_CARD", _is_card), ("CA_SIN", _is_sin)):
                if check(digits) and CONTEXT[entity].search(context):
                    return SplitHit(entity, sorted(set(used)), 0.9)
    return None


def worth_asking_model(texts: list[str]) -> bool:
    """Cheap gate for the tier-1 window check: digits in 2+ messages, 9+ digits in total."""
    with_digits = [t for t in texts if digit_groups(t)]
    total = sum(len(g) for t in texts for g in digit_groups(t))
    return (
        len(texts) >= 2 and bool(digit_groups(texts[-1])) and len(with_digits) >= 2 and total >= 9
    )
