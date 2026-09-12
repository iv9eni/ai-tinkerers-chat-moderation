"""Find card numbers and SINs that were split across one author's recent messages.

Deterministic and free. Joins neighbouring digit groups across messages and checks
them with the same validators as tier 0. A context word is required, so unrelated
numbers in a busy chat do not combine into a false alarm.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from blackline.detectors.tier0 import CARD_CONTEXT, _is_card, _is_sin
from blackline.normalize import digit_groups, disguised_digit_count, normalize

CONTEXT = {
    "CREDIT_CARD": CARD_CONTEXT,
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
    # digits hidden as emoji or unicode, or messages that hold nothing but digits, need no
    # context word: sending a card in bare chunks is itself the signal
    disguised = [disguised_digit_count(t) > 0 for t in texts]
    bare = [not re.search(r"[^\W\d_]", normalize(t)) for t in texts]
    # the message under review is the last one; when it holds no digits ("card number
    # above ^") it may still be the context word that turns earlier digits into a hit, so
    # the hit must include the latest message that does carry digits
    last = len(texts) - 1
    new_context = not digit_groups(texts[-1])
    if new_context:
        # earlier digits were already judged, so only the new word can change the answer
        last = groups[-1][1]
        context = normalize(texts[-1])
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
            hidden = all(disguised[i] for i in used)
            only_digits = all(bare[i] or disguised[i] for i in used)
            for entity, check in (("CREDIT_CARD", _is_card), ("CA_SIN", _is_sin)):
                if not check(digits):
                    continue
                # a 9-digit SIN is too short to trust bare chunks, so only hidden digits count
                no_context_ok = not new_context and (
                    hidden or (entity == "CREDIT_CARD" and only_digits)
                )
                if no_context_ok or CONTEXT[entity].search(context):
                    return SplitHit(entity, sorted(set(used)), 0.9)
    return None


def worth_asking_model(texts: list[str]) -> bool:
    """Cheap gate for the tier-1 window check: digits in 2+ messages, 9+ digits in total."""
    with_digits = [t for t in texts if digit_groups(t)]
    total = sum(len(g) for t in texts for g in digit_groups(t))
    return (
        len(texts) >= 2 and bool(digit_groups(texts[-1])) and len(with_digits) >= 2 and total >= 9
    )


_EMOJI_CODE = re.compile(r":[a-z0-9_+\-]+:")
_TOKEN = re.compile(r":[a-z0-9_+\-]+:|[^\W\d_]+", re.IGNORECASE)


def _digit_like_run(text: str, length: int = 13, max_distinct: int = 10) -> bool:
    """13+ words or emoji in a row drawn from 10 or fewer distinct values. A card written
    as words looks like this in any language; ordinary sentences almost never do."""
    toks = [t.lower() for t in _TOKEN.findall(text)]
    return any(
        len(set(toks[i : i + length])) <= max_distinct for i in range(len(toks) - length + 1)
    )


def looks_encoded(text: str) -> bool:
    """Cheap gate for the model's decode check: many emoji, many digits that only appear after
    folding, or a long run that uses few distinct words (a number spelled in any language)."""
    return (
        len(_EMOJI_CODE.findall(text)) >= 8
        or disguised_digit_count(text) >= 8
        or _digit_like_run(text)
    )
