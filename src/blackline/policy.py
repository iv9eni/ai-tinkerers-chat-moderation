"""Policy engine. Loads YAML, maps findings to one action. First matching rule wins."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from blackline.contract import Decision, Finding, Message

ORDER = ["quarantine", "block", "mask", "warn", "log", "allow"]


class Policy:
    def __init__(self, raw: dict):
        self.raw = raw
        self.defaults = raw.get("defaults", {})
        self.rules = raw.get("rules", [])
        self.channels = raw.get("channels", {})

    @classmethod
    def load(cls, path: str | None = None) -> Policy:
        p = Path(path or os.environ.get("POLICY_FILE", "policies/default.yaml"))
        return cls(yaml.safe_load(p.read_text()))

    def channel_cfg(self, msg: Message) -> dict:
        return self.channels.get(f"#{msg.channel_name}", {})

    def tier1_trigger(self, msg: Message) -> str:
        return self.channel_cfg(msg).get(
            "tier1_trigger", self.defaults.get("tier1_trigger", "on_uncertain")
        )

    def _match(self, rule: dict, f: Finding, msg: Message) -> bool:
        if rule.get("entity") != f.entity:
            return False
        if "subject" in rule and rule["subject"] != f.subject:
            return False
        if f.confidence < rule.get(
            "min_confidence", self.defaults.get("confidence_threshold", 0.85)
        ):
            return False
        return not (rule.get("only_with_guests") and not msg.channel_has_guests)

    def decide(self, msg: Message, findings: list[Finding]) -> Decision:
        best: tuple[str, dict] | None = None
        overrides = self.channel_cfg(msg).get("overrides", [])
        for f in findings:
            # self data is allowed unless a rule says otherwise
            if (
                f.subject == "self"
                and self.defaults.get("subject_policy", {}).get("self", "allow") == "allow"
            ):
                continue
            for rule in [*overrides, *self.rules]:
                if self._match(rule, f, msg):
                    action = rule["action"]
                    if best is None or ORDER.index(action) < ORDER.index(best[0]):
                        best = (action, rule)
                    break
        if best is None:
            return Decision(action="allow", findings=findings)
        action, rule = best
        return Decision(
            action=action,
            rule_id=rule.get("id", ""),
            message=rule.get("message", f"{action} by policy"),
            findings=findings,
        )
