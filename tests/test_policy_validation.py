import os
import time

import pytest
import yaml

from blackline import pipeline
from blackline.contract import Message
from blackline.detectors import tier1
from blackline.policy import Policy, PolicyError, validate

with open("policies/default.yaml") as _fh:
    GOOD = yaml.safe_load(_fh)


def with_rule(**rule):
    return {**GOOD, "rules": [*GOOD["rules"], rule]}


def test_default_policy_is_valid():
    Policy.load("policies/default.yaml")


@pytest.mark.parametrize(
    "raw, needle",
    [
        (with_rule(entity="CREDIT_CARDS", action="mask"), "entity"),
        (with_rule(entity="CREDIT_CARD", action="delete"), "action"),
        (with_rule(entity="CREDIT_CARD", acton="mask"), "acton"),
        (with_rule(entity="PHONE", action="warn", subject="thirdparty"), "subject"),
        (with_rule(id="pci.card.default", entity="CREDIT_CARD", action="mask"), "duplicate"),
        ({**GOOD, "channels": {"support": {"hold": True}}}, "must start with #"),
        ({**GOOD, "channels": {"#support": {"tier1_trigger": "sometimes"}}}, "tier1_trigger"),
    ],
)
def test_mistakes_are_reported(raw, needle):
    with pytest.raises(PolicyError) as e:
        validate(raw)
    assert needle in str(e.value)


def test_edits_reload_and_bad_edits_are_rejected(tmp_path, monkeypatch):
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump(GOOD))
    monkeypatch.setenv("POLICY_FILE", str(path))
    pipeline.reload_policy()
    first = pipeline.policy()

    changed = {**GOOD, "channels": {"#new": {"hold": True}}}
    path.write_text(yaml.safe_dump(changed))
    os.utime(path, (time.time() + 5, time.time() + 5))
    monkeypatch.setattr(pipeline, "_policy_checked", 0.0)
    second = pipeline.policy()
    assert second is not first and "#new" in second.channels

    path.write_text("rules: [{entity: NOPE, action: mask}]")
    os.utime(path, (time.time() + 10, time.time() + 10))
    monkeypatch.setattr(pipeline, "_policy_checked", 0.0)
    assert pipeline.policy() is second  # bad edit ignored, previous policy kept
    pipeline.reload_policy()


def test_model_outage_falls_back_to_rules(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIT_FILE", str(tmp_path / "a.jsonl"))
    pipeline.reload_policy()

    def down(*a, **k):
        raise ConnectionError("openrouter unreachable")

    monkeypatch.setattr(tier1, "detect", down)
    msg = Message(id="C1.1", channel_id="C1", author_id="U1", text="her address is 12 Elm St")
    d = pipeline.run(msg)
    assert d.action == "allow" and d.timing_ms["model_error"] == 1
    card = Message(id="C1.2", channel_id="C1", author_id="U1", text="card 4111 1111 1111 1111")
    assert pipeline.run(card).action == "block"  # rules still work during the outage
