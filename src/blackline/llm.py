"""One client for every model call. OpenRouter in front, OpenAI-compatible."""

from __future__ import annotations

import os

from openai import OpenAI

_client: OpenAI | None = None


def client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.environ["OPENROUTER_API_KEY"],
            default_headers={
                "HTTP-Referer": "https://github.com/iv9eni/ai-tinkerers-chat-moderation",
                "X-Title": "Blackline",
            },
        )
    return _client


def models() -> list[str]:
    primary = os.environ.get("TIER1_MODEL", "openai/gpt-4o-mini")
    fallback = os.environ.get("TIER1_FALLBACK_MODEL")
    return [primary, fallback] if fallback else [primary]
