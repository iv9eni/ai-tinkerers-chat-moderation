# Blackline

Governance agent for Slack. Finds card numbers, SINs, secrets, and other people's personal data in messages, then masks, blocks, or quarantines them before anyone else reads them. Text only for the MVP. Voice and images are a later converter in front of the same pipeline.

Built at AI Tinkerers "Agents, Everywhere" (OpenAI), September 12, 2026.

## Why

Slack's own data-loss prevention only exists on Enterprise Grid, cannot scan attachments, cannot redact part of a message, and has no per-channel rules. Blackline runs on any plan.

## How it works

```
Slack event ──> adapters/slack_app.py ──> Message
                                            │
                                   pipeline.run(message)
                                            │
                    ┌───────────────────────┼───────────────────────┐
                 tier 0                  tier 1                  policy
            regex + checksums      small model via OpenRouter   YAML rules
               < 5 ms, free         structured output, ~500 ms  first match wins
                    └───────────────────────┼───────────────────────┘
                                            │
                                         Decision ──> delete / repost masked / warn
                                            │
                                        audit.jsonl
```

- `src/blackline/contract.py` is the one shape everything agrees on. Read it first.
- `src/blackline/detectors/tier0.py` rules. `tier1.py` model. Add detectors here.
- `src/blackline/policy.py` loads `policies/default.yaml` and picks one action.
- `adapters/slack_app.py` is the only Slack-specific file. A Teams adapter would be a sibling.

## Run

```bash
make setup            # uv sync, copies .env.example to .env
# fill .env: Slack tokens from slack-manifest.yaml install, OpenRouter key
make test
make data             # synthetic labeled messages, never real data
make replay           # precision / recall / latency on data/demo_messages.json, tier 0 only
uv run python scripts/replay.py data/holdout.json --tier1
make run              # always-on Socket Mode listener
```

Slack app: create from `slack-manifest.yaml`, install as workspace owner (the user token deletes other people's messages), invite the bot to a channel.

## Policy

`policies/default.yaml`. Rules are framed on PCI DSS requirement 3 (cards), PIPEDA (SIN, Canadian PII), and GDPR Articles 4 and 9 (personal data, special categories). Entity names follow the Presidio taxonomy. Channel blocks override workspace rules.

## Team

- core pipeline and detectors
- ingest, deploy (Cloud Run), OpenRouter routing and cost log
- policy catalog, demo data, governance story
