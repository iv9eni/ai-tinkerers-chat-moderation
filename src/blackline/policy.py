"""Policy engine. Loads YAML, maps findings to one action. First matching rule wins."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from blackline.contract import Action, Decision, Entity, Finding, Message

Trigger = Literal["never", "on_uncertain", "always"]


class PolicyError(ValueError):
    """The policy file is invalid. The message lists every problem found."""


class RuleSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a misspelled key is an error, not ignored
    id: str = ""
    entity: Entity
    action: Action
    subject: Literal["self", "third_party", "public", "unknown"] | None = None
    min_confidence: float | None = Field(default=None, ge=0, le=1)
    only_with_guests: bool = False
    message: str = ""
    source_url: str | None = None
    notify: list[str] | None = None
    review_channel: str | None = None
    mask: dict | None = None


class ChannelSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tier1_trigger: Trigger | None = None
    overrides: list[RuleSpec] = []
    hold: bool = False
    risk: Literal["low", "normal", "high"] | None = None


class DefaultsSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tier1_trigger: Trigger = "on_uncertain"
    confidence_threshold: float = Field(default=0.85, ge=0, le=1)
    subject_policy: dict[Literal["self", "third_party", "public"], str] = {}
    tier1_model: str | None = None
    tier2_model: str | None = None


class PolicySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = 1
    workspace: str = ""
    defaults: DefaultsSpec = DefaultsSpec()
    rules: list[RuleSpec]
    channels: dict[str, ChannelSpec] = {}
    media: dict | None = None

    @field_validator("channels")
    @classmethod
    def _channel_names(cls, v: dict) -> dict:
        bad = [k for k in v if not k.startswith("#")]
        if bad:
            raise ValueError(f"channel names must start with #: {bad}")
        return v

    @field_validator("rules")
    @classmethod
    def _unique_ids(cls, v: list[RuleSpec]) -> list[RuleSpec]:
        ids = [r.id for r in v if r.id]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate rule ids: {dupes}")
        return v


def validate(raw: object, source: str = "policy") -> dict:
    if not isinstance(raw, dict):
        raise PolicyError(f"{source}: expected a mapping at the top level")
    try:
        PolicySpec.model_validate(raw)
    except ValidationError as e:
        problems = [
            f"{'.'.join(str(p) for p in err['loc']) or 'policy'}: {err['msg']}"
            for err in e.errors()
        ]
        raise PolicyError(
            f"{source} has {len(problems)} problem(s): " + "; ".join(problems)
        ) from None
    return raw


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
        try:
            raw = yaml.safe_load(p.read_text())
        except (OSError, yaml.YAMLError) as e:
            raise PolicyError(f"{p}: {e}") from None
        return cls(validate(raw, str(p)))

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
