"""Every rule case in evals/cases.yaml is a test. Known gaps must keep failing: if one starts
passing, strict xfail turns it red so the gap gets removed from the file."""

import pytest

from blackline import pipeline
from evals.run import load_cases, outcome


def params():
    out = []
    for case in load_cases():
        if case.get("needs_model"):
            continue
        marks = (
            [pytest.mark.xfail(strict=True, reason=case["known_gap"])]
            if case.get("known_gap")
            else []
        )
        out.append(pytest.param(case, id=case["id"], marks=marks))
    return out


@pytest.fixture(autouse=True)
def fresh_policy(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIT_FILE", str(tmp_path / "a.jsonl"))
    pipeline.reload_policy()


@pytest.mark.parametrize("case", params())
def test_case(case):
    assert outcome(case, use_model=False) == case["expect"]
