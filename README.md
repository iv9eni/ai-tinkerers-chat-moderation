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

## Delivery guarantees

- **Nothing is lost.** The Slack listener writes each event to a SQLite queue (`blackline.db`) before Slack gets its acknowledgement. Jobs left half done by a crash are picked up again on restart.
- **Downtime is covered.** On startup the bot reads channel history since the last message it finished, and queues anything it missed.
- **No double actions.** Each message is stored once, keyed by channel and timestamp. Each side effect (delete, repost, notice) is recorded on the job, so a retry skips what already happened.
- **Order per author.** Jobs are partitioned by channel and author, and a job waiting on retry blocks the ones behind it. Split-message detection depends on this.
- **Failures are visible.** A job retries with backoff up to 5 times, then is marked dead. `make queue` shows counts and dead jobs.
- **Delete first.** Workers run the rules with no network calls. On a hit they delete before any lookup or model call. `timing_ms.exposed` in `audit.jsonl` is how long the message was visible.

On GCP, `src/blackline/jobqueue.py` is the one file to swap for Pub/Sub with an ordering key.

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
