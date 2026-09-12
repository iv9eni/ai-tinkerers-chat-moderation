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
