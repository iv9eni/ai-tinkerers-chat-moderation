"""The one shape every part of Blackline agrees on.

Ingest builds a Message. Detectors add Findings. Policy produces a Decision.
Actions consume the Decision. Audit stores all of it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Entity = Literal[
    "CREDIT_CARD",
    "CA_SIN",
    "US_SSN",
    "API_KEY",
    "IBAN",
    "EMAIL",
    "PHONE",
    "PERSON",
    "ADDRESS",
    "DATE_OF_BIRTH",
    "HEALTH",
    "SPECIAL_CATEGORY",
]
Subject = Literal["self", "third_party", "public", "unknown"]
Action = Literal["allow", "log", "warn", "mask", "block", "quarantine"]


class Attachment(BaseModel):
    type: Literal["audio", "image", "file"]
    url: str
    mime: str = ""
    text: str = ""  # filled by transcription / OCR


class Message(BaseModel):
    id: str
    source: str = "slack"
    channel_id: str
    channel_name: str = ""
    channel_has_guests: bool = False
    author_id: str
    author_role: Literal["member", "guest", "external", "admin"] = "member"
    text: str = ""
    attachments: list[Attachment] = Field(default_factory=list)


class Finding(BaseModel):
    entity: Entity
    start: int
    end: int
    confidence: float
    subject: Subject = "unknown"
    tier: int
    note: str = ""


class Decision(BaseModel):
    action: Action
    rule_id: str = ""
    message: str = ""  # shown to the author
    findings: list[Finding] = Field(default_factory=list)
    # ids of every message to remove when data was split across messages
    related_ids: list[str] = Field(default_factory=list)
    timing_ms: dict[str, int | None] = Field(default_factory=dict)
    model: str | None = None
    cost_usd: float | None = None
