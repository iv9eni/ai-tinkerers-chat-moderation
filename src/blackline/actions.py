"""Turn a Decision into text. Slack-specific calls live in the adapter."""

from __future__ import annotations

from blackline.contract import Finding

SPLIT_NOTICE = "[sensitive data split across {n} messages was removed by Blackline]"

BAR = "█"


def mask(text: str, findings: list[Finding]) -> str:
    for f in sorted(findings, key=lambda f: -f.start):
        span = text[f.start : f.end]
        # every digit is hidden, including a card's last 4: partial digits help
        # someone rebuild a number that was split across messages
        text = text[: f.start] + BAR * len(span) + text[f.end :]
    return text
