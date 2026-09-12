"""Turn a Decision into text. Slack-specific calls live in the adapter."""

from __future__ import annotations

from blackline.contract import Finding

BAR = "█"


def mask(text: str, findings: list[Finding]) -> str:
    for f in sorted(findings, key=lambda f: -f.start):
        span = text[f.start : f.end]
        keep = 4 if f.entity == "CREDIT_CARD" else 0
        text = text[: f.start] + BAR * (len(span) - keep) + span[len(span) - keep :] + text[f.end :]
    return text
