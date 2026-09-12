"""Run the moderation cases and print coverage by category.

Usage:
  uv run python evals/run.py            rule cases only, no network, used in CI
  uv run python evals/run.py --model    also the cases that need the model (spends credits)
  uv run python evals/run.py --json     machine-readable report
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("AUDIT_FILE", str(Path(tempfile.gettempdir()) / "blackline-evals.jsonl"))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")  # model cases need OPENROUTER_API_KEY

from blackline import pipeline
from blackline.contract import Message

REMOVED = {"mask", "block", "quarantine"}
CASES = Path(__file__).with_name("cases.yaml")


def load_cases() -> list[dict]:
    return yaml.safe_load(CASES.read_text())


def outcome(case: dict, use_model: bool) -> str:
    texts = case.get("messages") or [case["text"]]
    msgs = [
        Message(
            id=f"C1.{i}",
            channel_id="C1",
            channel_name=case.get("channel", "eng"),
            author_id=f"U{i}" if case.get("different_authors") else "U1",
            text=t,
        )
        for i, t in enumerate(texts)
    ]
    window = [] if case.get("different_authors") else msgs[:-1]
    d = pipeline.run(msgs[-1], use_tier1=use_model, window=window, audit_log=False)
    return "removed" if d.action in REMOVED else d.action


def evaluate(use_model: bool = False) -> list[dict]:
    results = []
    for case in load_cases():
        if case.get("needs_model") and not use_model:
            continue
        got = outcome(case, use_model)
        ok = got == case["expect"]
        status = "pass" if ok else "fail"
        if case.get("known_gap"):
            status = "fixed gap" if ok else "known gap"
        results.append({**case, "got": got, "status": status})
    return results


def main() -> int:
    use_model = "--model" in sys.argv
    results = evaluate(use_model)
    if "--json" in sys.argv:
        print(json.dumps(results, indent=1, ensure_ascii=False))
    else:
        by_cat: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for r in results:
            by_cat[r["category"]][r["status"]] += 1
        print(f"{'category':10} {'pass':>5} {'fail':>5} {'known gap':>10} {'fixed gap':>10}")
        for cat, c in by_cat.items():
            print(
                f"{cat:10} {c['pass']:>5} {c['fail']:>5} {c['known gap']:>10} {c['fixed gap']:>10}"
            )
        for r in results:
            if r["status"] in ("fail", "fixed gap"):
                print(f"  {r['status'].upper()}: {r['id']} expected {r['expect']}, got {r['got']}")
            elif r["status"] == "known gap":
                print(f"  known gap: {r['id']} ({r['known_gap']})")
    return 1 if any(r["status"] in ("fail", "fixed gap") for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
