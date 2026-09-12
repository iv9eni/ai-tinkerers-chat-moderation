import json

import pytest

from scripts.token_dashboard import load_rows, render, summarize


def test_dashboard_counts_zero_token_and_model_rows(tmp_path):
    audit = tmp_path / "audit.jsonl"
    rows = [
        {"action": "block", "model": None, "entities": ["CREDIT_CARD"], "channel": "eng"},
        {"action": "allow", "model": "openai/gpt-4o-mini", "token_usage": {"total_tokens": 120}},
        {"action": "warn", "model": None, "entities": ["PHONE"], "channel": "support"},
    ]
    audit.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")

    summary = summarize(load_rows(audit), assumed_baseline_tokens=800, grams_co2e_per_1k_tokens=0.2)

    assert summary["total"] == 3
    assert summary["zero_token"] == 2
    assert summary["model_calls"] == 1
    assert summary["local_removed"] == 1
    assert summary["measured_tokens"] == 120
    assert summary["estimated_avoided"] == 1600
    assert summary["estimated_co2e_g"] == pytest.approx(0.32)


def test_dashboard_renders_shareable_html(tmp_path):
    summary = summarize(
        [
            {
                "action": "block",
                "model": None,
                "entities": ["CARD_LIKE"],
                "channel": "blackline-test",
            }
        ],
        assumed_baseline_tokens=500,
        grams_co2e_per_1k_tokens=0.2,
    )

    html = render(summary, tmp_path / "audit.jsonl")

    assert "Token Optimization" in html
    assert "Zero-token decisions" in html
    assert "Estimated CO2e avoided" in html
    assert "CARD_LIKE" in html
