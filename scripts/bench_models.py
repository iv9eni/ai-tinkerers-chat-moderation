"""Compare tier-1 models on accuracy and latency through OpenRouter.

Usage: uv run python scripts/bench_models.py
"""

from __future__ import annotations

import json
import statistics
import time
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv

from blackline import llm
from blackline.detectors.tier1 import SCHEMA, SYSTEM

load_dotenv(".env")

CASES = [
    ("her home address is 12 Elm St, Guelph", {"ADDRESS": "third_party"}),
    ("my email is me@example.com", {"EMAIL": "self"}),
    ("standup moved to 10:15", {}),
    ("call Priya Shah at 519-555-0142 about her claim", {"PHONE": "third_party"}),
    ("Tim Cook announced the new iPhone", {}),
]
MODELS = [
    "openai/gpt-4o-mini",
    "openai/gpt-4.1-mini",
    "openai/gpt-5-mini",
    "google/gemini-2.5-flash-lite",
    "meta-llama/llama-3.3-70b-instruct",
]
SCORED = ("ADDRESS", "EMAIL", "PHONE")


def run(model: str):
    lat, correct, errs = [], 0, []
    for text, want in CASES:
        t = time.perf_counter()
        try:
            r = llm.client().chat.completions.create(
                model=model,
                extra_body={"provider": {"sort": "latency"}},
                temperature=0,
                max_tokens=400,
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "findings", "strict": True, "schema": SCHEMA},
                },
                messages=[
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": text},
                ],
            )
            lat.append(int((time.perf_counter() - t) * 1000))
            data = json.loads(r.choices[0].message.content or '{"findings": []}')
            got = {
                f["entity"]: f["subject"]
                for f in data["findings"]
                if f["subject"] != "public" and f["entity"] in SCORED
            }
            if got == want:
                correct += 1
            else:
                errs.append(f"{text[:24]}.. got {got}")
        except Exception as e:  # noqa: BLE001
            errs.append(f"{type(e).__name__}: {str(e)[:90]}")
    p50 = statistics.median(lat) if lat else None
    return model, correct, p50, max(lat) if lat else None, errs


if __name__ == "__main__":
    with ThreadPoolExecutor(len(MODELS)) as ex:
        rows = list(ex.map(run, MODELS))
    print(f"{'model':40} {'ok':>5} {'p50 ms':>7} {'max ms':>7}")
    for m, ok, p50, mx, errs in sorted(rows, key=lambda r: (-r[1], r[2] or 1e9)):
        print(f"{m:40} {ok}/{len(CASES):<3} {p50!s:>7} {mx!s:>7}")
        for e in errs:
            print("    ", e)
