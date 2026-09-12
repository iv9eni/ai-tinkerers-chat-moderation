"""Tier 1: small model with structured output. Runs only when tier 0 is unsure."""

from __future__ import annotations

import json

from blackline import llm
from blackline.contract import Finding

SCHEMA = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "entity": {
                        "type": "string",
                        "enum": [
                            "EMAIL",
                            "PHONE",
                            "PERSON",
                            "ADDRESS",
                            "DATE_OF_BIRTH",
                            "HEALTH",
                            "SPECIAL_CATEGORY",
                        ],
                    },
                    "text": {"type": "string"},
                    "subject": {"type": "string", "enum": ["self", "third_party", "public"]},
                    "confidence": {"type": "number"},
                },
                "required": ["entity", "text", "subject", "confidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["findings"],
    "additionalProperties": False,
}

SYSTEM = (
    "You find personal data in one workplace chat message. "
    "Return exact substrings from the message. "
    "subject=self when the data belongs to the message author, "
    "third_party when it belongs to another private person, "
    "public when it belongs to a company, brand, or public figure. "
    "Return an empty list when there is nothing."
)


def detect(text: str) -> tuple[list[Finding], dict]:
    """Returns findings and metadata {model, usage}."""
    r = llm.client().chat.completions.create(
        model=llm.models()[0],
        extra_body={"models": llm.models(), "provider": {"sort": "latency"}},
        temperature=0,
        max_tokens=300,
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "findings", "strict": True, "schema": SCHEMA},
        },
        messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": text}],
    )
    data = json.loads(r.choices[0].message.content or '{"findings": []}')
    out: list[Finding] = []
    for f in data.get("findings", []):
        i = text.find(f["text"])
        if i < 0:
            continue
        out.append(
            Finding(
                entity=f["entity"],
                start=i,
                end=i + len(f["text"]),
                confidence=float(f["confidence"]),
                subject=f["subject"],
                tier=1,
            )
        )
    meta = {"model": r.model, "usage": r.usage.model_dump() if r.usage else None}
    return out, meta


# ---------------------------------------------------------------------------
# Window check: one author's recent messages read together.
# ---------------------------------------------------------------------------

WINDOW_ENTITIES = ["CREDIT_CARD", "CA_SIN", "US_SSN", "API_KEY"]

WINDOW_SCHEMA = {
    "type": "object",
    "properties": {
        "reconstructed": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "entity": {"type": "string", "enum": WINDOW_ENTITIES},
                    "value": {"type": "string"},
                    "message_numbers": {"type": "array", "items": {"type": "integer"}},
                    "confidence": {"type": "number"},
                },
                "required": ["entity", "value", "message_numbers", "confidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["reconstructed"],
    "additionalProperties": False,
}

WINDOW_SYSTEM = (
    "You read consecutive workplace chat messages from ONE author, numbered oldest first. "
    "People sometimes split a credit card number, Canadian SIN, US SSN, or API key across "
    "several messages to get past a filter: out of order, spelled out, with words between the "
    "parts, or with labels like 'first digits' and 'last four'. "
    "If the messages together contain such a value, rebuild it in its real order and list the "
    "message numbers that hold its parts. Only use characters that appear in the messages. "
    "Return an empty list when the messages do not add up to one of these values."
)


def _verify(entity: str, value: str, texts: list[str], numbers: list[int]) -> bool:
    """Reject anything the model could have invented."""
    from collections import Counter

    from blackline.detectors.tier0 import _digits, _is_card, _is_sin
    from blackline.normalize import normalize

    if not numbers or any(n < 0 or n >= len(texts) for n in numbers):
        return False
    if entity == "API_KEY":
        joined = "".join(normalize(texts[n]) for n in numbers).replace(" ", "")
        parts = [p for p in value.split() if p]
        return len(value) >= 20 and all(p in joined for p in parts)
    digits = _digits(value)
    available = Counter(_digits("".join(normalize(texts[n]) for n in numbers)))
    if Counter(digits) - available:  # a digit the author never typed
        return False
    if entity == "CREDIT_CARD":
        return _is_card(digits)
    if entity == "CA_SIN":
        return _is_sin(digits)
    if entity == "US_SSN":
        return len(digits) == 9 and digits[:3] not in ("000", "666") and digits[0] != "9"
    return False


def detect_window(texts: list[str]) -> tuple[list[tuple[str, list[int], float]], dict]:
    """texts oldest first, current message last. Returns verified (entity, indexes, confidence)."""
    numbered = "\n".join(f"{i}: {t}" for i, t in enumerate(texts))
    r = llm.client().chat.completions.create(
        model=llm.models()[0],
        extra_body={"models": llm.models(), "provider": {"sort": "latency"}},
        temperature=0,
        max_tokens=300,
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "reconstructed", "strict": True, "schema": WINDOW_SCHEMA},
        },
        messages=[
            {"role": "system", "content": WINDOW_SYSTEM},
            {"role": "user", "content": numbered},
        ],
    )
    data = json.loads(r.choices[0].message.content or '{"reconstructed": []}')
    last = len(texts) - 1
    out = []
    for item in data.get("reconstructed", []):
        nums = sorted(set(item["message_numbers"]))
        if last in nums and len(nums) > 1 and _verify(item["entity"], item["value"], texts, nums):
            out.append((item["entity"], nums, float(item["confidence"])))
    meta = {"model": r.model, "usage": r.usage.model_dump() if r.usage else None}
    return out, meta


# ---------------------------------------------------------------------------
# Encoded check: the model names which tokens stand for which digit. Code does the
# substitution and the checksum, so the model never counts or writes the number.
# ---------------------------------------------------------------------------

CODEBOOK_SCHEMA = {
    "type": "object",
    "properties": {
        "tokens": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "token": {"type": "string"},
                    "digit": {"type": "string", "enum": [str(i) for i in range(10)]},
                },
                "required": ["token", "digit"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["tokens"],
    "additionalProperties": False,
}

ENCODED_SYSTEM = (
    "A workplace chat message may hide a number by writing each digit in disguise: emoji or "
    "emoji names (including custom ones like :num_four:), number words in any language, "
    "Roman numerals, look-alike letters, or symbols. List each distinct token in the message "
    "that stands for a single digit, exactly as it appears, with the digit it stands for. "
    "Do not rebuild or repeat the number. Return an empty list if no token stands for a digit."
)


def decode_with(text: str, pairs: list[tuple[str, str]]) -> str:
    """Replace each disguised token with its digit, longest token first."""
    import re

    for token, digit in sorted(pairs, key=lambda p: -len(p[0])):
        if not token.strip():
            continue
        if token.isalpha():
            text = re.sub(rf"(?<!\w){re.escape(token)}(?!\w)", digit, text, flags=re.IGNORECASE)
        else:
            text = text.replace(token, digit)
    return text


def detect_encoded(text: str) -> tuple[list[tuple[str, float]], dict]:
    import re

    from blackline.detectors.tier0 import CARD_CONTEXT, _scan_run
    from blackline.normalize import normalize

    r = llm.client().chat.completions.create(
        model=llm.models()[0],
        extra_body={"models": llm.models(), "provider": {"sort": "latency"}},
        temperature=0,
        max_tokens=300,
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "codebook", "strict": True, "schema": CODEBOOK_SCHEMA},
        },
        messages=[
            {"role": "system", "content": ENCODED_SYSTEM},
            {"role": "user", "content": text},
        ],
    )
    data = json.loads(r.choices[0].message.content or '{"tokens": []}')
    low = text.lower()
    # a token the author never typed cannot contribute a digit
    pairs = [(t["token"], t["digit"]) for t in data.get("tokens", []) if t["token"].lower() in low]
    decoded = normalize(decode_with(text, pairs))
    context = bool(CARD_CONTEXT.search(decoded))
    out: list[tuple[str, float]] = []
    for run in re.findall(r"\d{9,}", decoded):
        entity = _scan_run(run, context)
        if entity:
            out.append((entity, 0.85))
            break
    meta = {"model": r.model, "usage": r.usage.model_dump() if r.usage else None, "tokens": pairs}
    return out, meta
