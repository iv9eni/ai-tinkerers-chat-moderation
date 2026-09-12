"""Generate a static token-optimization dashboard from audit.jsonl.

Usage:
  uv run python scripts/token_dashboard.py
  uv run python scripts/token_dashboard.py audit.jsonl --out reports/token_optimization.html
"""

from __future__ import annotations

import argparse
import html
import json
import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

REMOVED = {"mask", "block", "quarantine"}


def load_rows(path: Path) -> list[dict[str, Any]]:
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def token_usage(row: dict[str, Any]) -> dict[str, int]:
    usage = row.get("token_usage") or row.get("tokens") or row.get("usage") or {}
    if not isinstance(usage, dict):
        return {}
    out = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = usage.get(key)
        if isinstance(value, int):
            out[key] = value
    return out


def summarize(
    rows: list[dict[str, Any]],
    assumed_baseline_tokens: int,
    grams_co2e_per_1k_tokens: float,
    usd_per_1m_tokens: float,
) -> dict[str, Any]:
    total = len(rows)
    model_rows = [r for r in rows if r.get("model")]
    zero_token = total - len(model_rows)
    measured_tokens = sum(token_usage(r).get("total_tokens", 0) for r in model_rows)
    unknown_model_usage = sum(1 for r in model_rows if not token_usage(r).get("total_tokens"))
    measured_cost_usd = sum(float(r.get("cost_usd") or 0) for r in model_rows)
    local_removed = sum(1 for r in rows if not r.get("model") and r.get("action") in REMOVED)
    estimated_avoided = zero_token * assumed_baseline_tokens
    estimated_avoided_usd = estimated_avoided / 1_000_000 * usd_per_1m_tokens
    estimated_co2e_g = estimated_avoided / 1000 * grams_co2e_per_1k_tokens
    baseline = total * assumed_baseline_tokens
    call_rate = (len(model_rows) / total * 100) if total else 0
    zero_rate = (zero_token / total * 100) if total else 0

    actions = Counter(str(r.get("action", "unknown")) for r in rows)
    channels = Counter(str(r.get("channel", "unknown")) for r in rows)
    models = Counter(str(r.get("model")) for r in model_rows)
    entities: Counter[str] = Counter()
    for row in rows:
        for entity in row.get("entities") or []:
            entities[str(entity)] += 1

    return {
        "total": total,
        "model_calls": len(model_rows),
        "zero_token": zero_token,
        "zero_rate": zero_rate,
        "call_rate": call_rate,
        "local_removed": local_removed,
        "measured_tokens": measured_tokens,
        "measured_cost_usd": measured_cost_usd,
        "unknown_model_usage": unknown_model_usage,
        "assumed_baseline_tokens": assumed_baseline_tokens,
        "estimated_avoided": estimated_avoided,
        "estimated_avoided_usd": estimated_avoided_usd,
        "usd_per_1m_tokens": usd_per_1m_tokens,
        "estimated_co2e_g": estimated_co2e_g,
        "grams_co2e_per_1k_tokens": grams_co2e_per_1k_tokens,
        "baseline_tokens": baseline,
        "actions": actions,
        "channels": channels,
        "models": models,
        "entities": entities,
    }


def pct(part: float, total: float) -> float:
    return (part / total * 100) if total else 0


def fmt_int(value: float) -> str:
    return f"{round(value):,}"


def fmt_co2e(grams: float) -> str:
    if grams >= 1000:
        return f"{grams / 1000:,.2f} kg"
    return f"{grams:,.1f} g"


def fmt_usd(value: float) -> str:
    if value < 0.01:
        return f"${value:,.4f}"
    return f"${value:,.2f}"


def bar_row(label: str, value: int, total: int) -> str:
    width = pct(value, total)
    safe_label = html.escape(label)
    return f"""
      <div class="bar-row">
        <div class="bar-label"><span>{safe_label}</span><strong>{value:,}</strong></div>
        <div class="track" role="img" aria-label="{safe_label}: {value:,}">
          <div class="fill" style="width: {width:.2f}%"></div>
        </div>
      </div>"""


def table_rows(counter: Counter[str], total: int, limit: int = 8) -> str:
    if not counter:
        return '<tr><td colspan="3">No data yet</td></tr>'
    rows = []
    for label, value in counter.most_common(limit):
        rows.append(
            "<tr>"
            f"<td>{html.escape(label)}</td>"
            f"<td>{value:,}</td>"
            f"<td>{pct(value, total):.1f}%</td>"
            "</tr>"
        )
    return "\n".join(rows)


def render(summary: dict[str, Any], audit_path: Path) -> str:
    total = summary["total"]
    funnel = "\n".join(
        [
            bar_row("Zero-token local decisions", summary["zero_token"], total),
            bar_row("Model-routed decisions", summary["model_calls"], total),
            bar_row("Local removals before model", summary["local_removed"], total),
        ]
    )
    generated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    unknown_note = ""
    if summary["unknown_model_usage"]:
        unknown_note = (
            f'<p class="note">{summary["unknown_model_usage"]:,} model decision(s) were logged '
            "before token usage capture or without provider usage data.</p>"
        )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Blackline Token Optimization</title>
  <style>
    :root {{
      color-scheme: light dark;
      --bg: light-dark(#f7f8fb, #111315);
      --surface: light-dark(#ffffff, #1a1d21);
      --ink: light-dark(#1c2024, #f3f5f7);
      --muted: light-dark(#667085, #a4acb9);
      --line: light-dark(#d9dee7, #333842);
      --accent: light-dark(#2357c6, #83a7ff);
      --good: light-dark(#0f7b4f, #55d69c);
      --warn: light-dark(#a45f00, #f7bc53);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Inter, Segoe UI, Arial, sans-serif;
      background: var(--bg);
      color: var(--ink);
    }}
    main {{
      max-width: 1120px;
      margin: 0 auto;
      padding: 28px;
    }}
    header {{
      display: flex;
      justify-content: space-between;
      gap: 20px;
      align-items: end;
      margin-bottom: 22px;
    }}
    h1, h2 {{ margin: 0; font-weight: 650; letter-spacing: 0; }}
    h1 {{ font-size: clamp(28px, 4vw, 44px); }}
    h2 {{ font-size: 18px; }}
    p {{ color: var(--muted); line-height: 1.5; margin: 8px 0 0; }}
    .meta {{ text-align: right; font-size: 13px; }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
      gap: 14px;
      margin-bottom: 18px;
    }}
    .card {{
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
    }}
    .label {{
      color: var(--muted);
      font-size: 13px;
      margin-bottom: 8px;
    }}
    .value {{
      font-size: 30px;
      font-weight: 700;
      font-variant-numeric: tabular-nums;
    }}
    .good {{ color: var(--good); }}
    .warn {{ color: var(--warn); }}
    .layout {{
      display: grid;
      grid-template-columns: 1.2fr .8fr;
      gap: 18px;
      align-items: start;
    }}
    .carbon-layout {{
      display: grid;
      grid-template-columns: minmax(240px, .85fr) 1.15fr;
      gap: 18px;
      align-items: center;
      margin-bottom: 18px;
    }}
    .carbon-art {{
      width: 100%;
      max-width: 360px;
      display: block;
      margin: 0 auto;
    }}
    .carbon-art text {{
      fill: var(--ink);
      font-family: Inter, Segoe UI, Arial, sans-serif;
      font-weight: 700;
    }}
    .impact-grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
      margin-top: 14px;
    }}
    .impact {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      min-height: 132px;
    }}
    .impact svg {{
      width: 44px;
      height: 44px;
      display: block;
      margin-bottom: 10px;
    }}
    .impact strong {{
      display: block;
      font-size: 14px;
      margin-bottom: 4px;
    }}
    .impact span {{
      color: var(--muted);
      font-size: 13px;
      line-height: 1.35;
    }}
    .bar-row {{ margin-top: 16px; }}
    .bar-label {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      font-size: 14px;
      margin-bottom: 7px;
    }}
    .track {{
      height: 13px;
      background: light-dark(#e8edf5, #272c34);
      border-radius: 999px;
      overflow: hidden;
    }}
    .fill {{
      height: 100%;
      background: var(--accent);
    }}
    .fill.good {{ background: var(--good); }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin-top: 12px;
      font-size: 14px;
    }}
    th, td {{
      text-align: left;
      padding: 9px 0;
      border-bottom: 1px solid var(--line);
    }}
    th {{ color: var(--muted); font-weight: 600; }}
    td:nth-child(2), td:nth-child(3), th:nth-child(2), th:nth-child(3) {{
      text-align: right;
      font-variant-numeric: tabular-nums;
    }}
    .note {{
      padding: 10px 12px;
      border-left: 3px solid var(--warn);
      background: light-dark(#fff8eb, #2b2417);
      color: var(--ink);
      margin-top: 14px;
    }}
    code {{
      color: var(--ink);
      background: light-dark(#eef1f6, #262b33);
      padding: 2px 5px;
      border-radius: 4px;
    }}
    @media (max-width: 820px) {{
      main {{ padding: 18px; }}
      header, .layout, .carbon-layout {{ display: block; }}
      .meta {{ text-align: left; margin-top: 12px; }}
      .grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .card {{ margin-bottom: 14px; }}
      .impact-grid {{ grid-template-columns: 1fr; }}
    }}
    @media (max-width: 480px) {{
      .grid {{ grid-template-columns: 1fr; }}
      .value {{ font-size: 26px; }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>Token Optimization</h1>
        <p>Blackline routes obvious sensitive messages through deterministic checks, saving model calls for ambiguous cases.</p>
      </div>
      <div class="meta">
        <div>Generated {html.escape(generated)}</div>
        <div>Source <code>{html.escape(str(audit_path))}</code></div>
      </div>
    </header>

    <section class="grid" aria-label="Summary metrics">
      <div class="card">
        <div class="label">Decisions audited</div>
        <div class="value">{summary["total"]:,}</div>
      </div>
      <div class="card">
        <div class="label">Zero-token decisions</div>
        <div class="value good">{summary["zero_token"]:,}</div>
        <p>{summary["zero_rate"]:.1f}% avoided the model</p>
      </div>
      <div class="card">
        <div class="label">Model call rate</div>
        <div class="value">{summary["call_rate"]:.1f}%</div>
        <p>{summary["model_calls"]:,} routed to tier 1</p>
      </div>
      <div class="card">
        <div class="label">Estimated tokens avoided</div>
        <div class="value good">{fmt_int(summary["estimated_avoided"])}</div>
        <p>vs {summary["assumed_baseline_tokens"]:,} tokens/message baseline</p>
      </div>
      <div class="card">
        <div class="label">Estimated spend avoided</div>
        <div class="value good">{fmt_usd(summary["estimated_avoided_usd"])}</div>
        <p>Scenario estimate at {fmt_usd(summary["usd_per_1m_tokens"])} / 1M tokens</p>
      </div>
      <div class="card">
        <div class="label">Estimated CO2e avoided</div>
        <div class="value good">{fmt_co2e(summary["estimated_co2e_g"])}</div>
        <p>Scenario estimate at {summary["grams_co2e_per_1k_tokens"]:g} g CO2e / 1K tokens</p>
      </div>
    </section>

    <section class="card carbon-layout" aria-label="Carbon footprint visual">
      <svg class="carbon-art" viewBox="0 0 320 220" role="img" aria-labelledby="co2-title co2-desc">
        <title id="co2-title">Estimated carbon reduction from avoided model calls</title>
        <desc id="co2-desc">A cloud labelled CO2 with a downward arrow, connected to local filtering and fewer model calls.</desc>
        <defs>
          <linearGradient id="carbon-grad" x1="0" x2="1" y1="0" y2="1">
            <stop offset="0%" stop-color="var(--good)" stop-opacity=".35"/>
            <stop offset="100%" stop-color="var(--accent)" stop-opacity=".25"/>
          </linearGradient>
        </defs>
        <rect x="18" y="22" width="284" height="176" rx="18" fill="url(#carbon-grad)"/>
        <path d="M89 115c-20 0-36-15-36-34s16-34 36-34c9 0 17 3 23 8 8-18 27-30 49-30 30 0 54 22 56 50 19 2 34 18 34 37 0 21-18 38-40 38H89z"
          fill="var(--surface)" stroke="var(--line)" stroke-width="2"/>
        <text x="160" y="98" text-anchor="middle" font-size="38">CO2e</text>
        <path d="M160 122v45" stroke="var(--good)" stroke-width="10" stroke-linecap="round"/>
        <path d="M136 150l24 24 24-24" fill="none" stroke="var(--good)" stroke-width="10" stroke-linecap="round" stroke-linejoin="round"/>
        <circle cx="69" cy="166" r="20" fill="var(--surface)" stroke="var(--line)" stroke-width="2"/>
        <path d="M59 166l8 8 16-19" fill="none" stroke="var(--good)" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>
        <circle cx="252" cy="166" r="20" fill="var(--surface)" stroke="var(--line)" stroke-width="2"/>
        <path d="M242 168h20M252 158v20" stroke="var(--accent)" stroke-width="5" stroke-linecap="round"/>
      </svg>
      <div>
        <h2>Lower Footprint By Routing Less To The Model</h2>
        <p>The CO2e number is an estimate, but the routing signal is real: every zero-token decision avoids a model request that a naive always-model design would have made.</p>
        <div class="impact-grid">
          <div class="impact">
            <svg viewBox="0 0 48 48" role="img" aria-label="Local rules">
              <rect x="8" y="10" width="32" height="28" rx="5" fill="none" stroke="var(--accent)" stroke-width="3"/>
              <path d="M16 24l6 6 12-14" fill="none" stroke="var(--good)" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
            <strong>{summary["zero_token"]:,} local decisions</strong>
            <span>Handled by rules, checksums, and policy before any model call.</span>
          </div>
          <div class="impact">
            <svg viewBox="0 0 48 48" role="img" aria-label="Avoided tokens">
              <path d="M10 16h20M10 24h28M10 32h14" stroke="var(--accent)" stroke-width="4" stroke-linecap="round"/>
              <path d="M35 30l5 5 5-5M40 12v22" stroke="var(--good)" stroke-width="4" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
            <strong>{fmt_int(summary["estimated_avoided"])} tokens avoided</strong>
            <span>Estimated versus sending every audited message to a model.</span>
          </div>
          <div class="impact">
            <svg viewBox="0 0 48 48" role="img" aria-label="Estimated CO2e avoided">
              <path d="M16 30c-6 0-10-4-10-9s4-9 10-9c3 0 5 1 7 3 3-5 8-8 14-7 8 1 13 7 13 15 5 1 8 5 8 10 0 6-5 11-12 11H16z"
                fill="none" stroke="var(--accent)" stroke-width="3"/>
              <path d="M22 31v9M16 35l6 6 6-6" fill="none" stroke="var(--good)" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
            <strong>{fmt_co2e(summary["estimated_co2e_g"])} CO2e avoided</strong>
            <span>Scenario estimate using the factor shown above.</span>
          </div>
        </div>
      </div>
    </section>

    <section class="layout">
      <div class="card">
        <h2>Routing Funnel</h2>
        <p>Use this to show the optimization: most messages stop before the model, and urgent removals happen locally.</p>
        {funnel}
      </div>

      <div class="card">
        <h2>Measured Model Tokens</h2>
        <div class="value">{fmt_int(summary["measured_tokens"])}</div>
        <p>Captured from provider usage on new model calls. Measured model spend in audited rows: <strong>{fmt_usd(summary["measured_cost_usd"])}</strong>.</p>
        {unknown_note}
      </div>
    </section>

    <section class="layout" style="margin-top: 18px;">
      <div class="card">
        <h2>Actions</h2>
        <table>
          <thead><tr><th>Action</th><th>Count</th><th>Share</th></tr></thead>
          <tbody>{table_rows(summary["actions"], total)}</tbody>
        </table>
      </div>
      <div class="card">
        <h2>Entities</h2>
        <table>
          <thead><tr><th>Entity</th><th>Count</th><th>Share</th></tr></thead>
          <tbody>{table_rows(summary["entities"], max(sum(summary["entities"].values()), 1))}</tbody>
        </table>
      </div>
    </section>

    <section class="layout" style="margin-top: 18px;">
      <div class="card">
        <h2>Channels</h2>
        <table>
          <thead><tr><th>Channel</th><th>Count</th><th>Share</th></tr></thead>
          <tbody>{table_rows(summary["channels"], total)}</tbody>
        </table>
      </div>
      <div class="card">
        <h2>Models</h2>
        <table>
          <thead><tr><th>Model</th><th>Count</th><th>Share</th></tr></thead>
          <tbody>{table_rows(summary["models"], max(summary["model_calls"], 1))}</tbody>
        </table>
      </div>
    </section>
  </main>
</body>
</html>
"""


def main() -> int:
    load_dotenv(".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "audit",
        nargs="?",
        default=os.environ.get("AUDIT_FILE", "audit.jsonl"),
        help="Path to audit JSONL file.",
    )
    parser.add_argument(
        "--out",
        default="reports/token_optimization.html",
        help="Dashboard HTML output path.",
    )
    parser.add_argument(
        "--assumed-baseline-tokens",
        type=int,
        default=800,
        help="Estimated tokens per message if every message went to the model.",
    )
    parser.add_argument(
        "--grams-co2e-per-1k-tokens",
        type=float,
        default=0.2,
        help=(
            "Scenario estimate for grams of CO2e per 1,000 avoided tokens. "
            "Tune this for your model/provider/grid assumptions."
        ),
    )
    parser.add_argument(
        "--usd-per-1m-tokens",
        type=float,
        default=0.15,
        help=(
            "Scenario estimate for blended model cost per 1,000,000 avoided tokens. "
            "Tune this for your model/provider pricing assumptions."
        ),
    )
    args = parser.parse_args()

    audit_path = Path(args.audit)
    out_path = Path(args.out)
    rows = load_rows(audit_path)
    summary = summarize(
        rows,
        args.assumed_baseline_tokens,
        args.grams_co2e_per_1k_tokens,
        args.usd_per_1m_tokens,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render(summary, audit_path), encoding="utf-8")
    print(f"wrote {out_path} from {len(rows):,} audit rows")
    print(
        f"zero-token={summary['zero_token']:,} "
        f"model-routed={summary['model_calls']:,} "
        f"estimated-avoided={summary['estimated_avoided']:,} "
        f"estimated-spend={fmt_usd(summary['estimated_avoided_usd'])} "
        f"estimated-co2e={fmt_co2e(summary['estimated_co2e_g'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
