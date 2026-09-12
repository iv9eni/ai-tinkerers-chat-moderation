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

## Disguised numbers

People hide card numbers and SINs from filters. Blackline handles this in three layers.

**1. Fold, then check with rules (no model, under 1 ms).** `src/blackline/normalize.py` turns every known disguise into plain digits before tier 0 runs:

| Disguise | Example | Folds to |
|---|---|---|
| Emoji shortcodes (what Slack actually sends) | `:four::one:` | `41` |
| Keycap emoji | 4️⃣1️⃣ | `41` |
| Other scripts and styles | ٤١ ४१ ４１ 𝟒𝟏 ④① ➍➊ ⁴¹ | `41` |
| Invisible characters | zero-width space, bidi marks | removed |
| Number words, English, French, Spanish | four, quatre, cuatro, forty five, double one | digits |
| Look-alike letters, only next to real digits | `4lll`, `O5S1` | `4111`, `0551` |
| Separators and formatting | `4*1_1~1`, `` `1111` ``, `4.1.1.1` | joined |

A hit on folded text removes the whole message and posts a notice instead of a masked copy, because spans in the emoji text cannot be masked one by one.

**2. Model codebook for unknown disguises.** Custom emoji, other languages, and Roman numerals are not in the tables. When a message looks encoded (8+ emoji, 8+ digits that only appear after folding, or 13+ words in a row from 10 or fewer distinct values), the model is asked which tokens stand for which digit. It never writes the number. Code substitutes the tokens into the real text and runs the same checksum rules. An earlier version asked the model for the number itself and it miscounted repeated emoji one time in three.

**3. Across messages.** The same folding runs inside split-message detection, so a disguised number spread over several messages is also caught.

**Not covered yet.** Numbers inside images, numbers described in riddles ("my birth year then my house number"), and two people each posting half.

## Delivery guarantees

- **Nothing is lost.** The Slack listener writes each event to a SQLite queue (`blackline.db`) before Slack gets its acknowledgement. Jobs left half done by a crash are picked up again on restart.
- **Downtime is covered.** On startup the bot reads channel history since the last message it finished, and queues anything it missed.
- **No double actions.** Each message is stored once, keyed by channel and timestamp. Each side effect (delete, repost, notice) is recorded on the job, so a retry skips what already happened.
- **Order per author.** Jobs are partitioned by channel and author, and a job waiting on retry blocks the ones behind it. Split-message detection depends on this.
- **Failures are visible.** A job retries with backoff up to 5 times, then is marked dead. `make queue` shows counts and dead jobs.
- **Delete first, two lanes.** The triage lane runs the rules with no network calls and deletes on a hit before any lookup or model call. Everything else moves to the deep lane, which runs the model checks on its own workers, so a slow model call never delays a delete. `timing_ms.exposed` in `audit.jsonl` is how long the message was visible.
- **Hold mode, per channel.** With `hold: true` in the policy, every message is deleted on arrival and reposted under the author's name, marked "via Blackline", once every check passes. Nothing sensitive is visible for longer than one delete call, even findings that need the model. The cost: messages come back as bot posts, so authors cannot edit them and reactions start fresh. `#blackline-hold` in `policies/default.yaml` is the example.

On GCP, `src/blackline/jobqueue.py` is the one file to swap for Pub/Sub with an ordering key.

## Working together

- `main` always passes CI and is what gets deployed. Nobody pushes to it directly.
- Each person works on their own branch, named `<name>/<topic>`, for example `ivgeni/hardening`.
- Open a pull request into `main`. CI runs lint, tests, the policy check, the moderation cases, and a Docker build. Merge when it is green.
- Pull `main` into your branch before you start something new: `git pull --rebase origin main`.
- Keep pull requests small, one change each, so the other person can review in a few minutes.

## Production

**Run it**

```bash
make check-policy     # the policy file is valid
make evals            # moderation coverage, rules only, no network
make token-dashboard  # writes reports/token_optimization.html from audit.jsonl
make up               # docker compose: container + volume for the queue and audit log
make health           # {"status": "ok", "slack_connected": true, ...}
```

Deploy to GCP, run by someone with gcloud access: first `PROJECT=<id> ./deploy/secrets.sh` to copy the tokens from `.env` into Secret Manager, then `PROJECT=<id> ./deploy/gce.sh`. The VM reads tokens from Secret Manager at startup; they are not in its configuration. It builds the image with Cloud Build, then runs it on an always-on e2-small VM with a persistent disk. A VM rather than Cloud Run, because Socket Mode needs one process that stays connected and the SQLite queue needs a disk that survives restarts. `./deploy/gce.sh update` ships a new image to the same VM.

The VM runs Container-Optimized OS. `deploy/vm-startup.sh` mounts the persistent disk and starts the image with Docker on every boot, so starting the VM starts the bot. Only one copy may be connected to Slack, so stop the laptop bot first.

```bash
make gcp-start      # start the VM, the bot connects about a minute later
make gcp-logs       # app logs from Cloud Logging
make gcp-health     # /healthz on the VM, over SSH
make gcp-stop       # stop the VM before testing on a laptop again
make gcp-deploy     # build the current commit, ship it, restart the VM
```

Set `GCLOUD=~/path/to/gcloud` if gcloud is not on your PATH.

**What makes it safe to run**

| Concern | How it is handled |
|---|---|
| Sensitive data at rest | The queue wipes message text when a job finishes or dies. The audit log stores an HMAC fingerprint, never text. Set `AUDIT_HMAC_KEY`. |
| Leaked credentials | GitHub secret scanning is on. The fake keys in `evals/cases.yaml` and `tests/` are excluded in `.github/secret_scanning.yml`. `.env`, the queue database, and the audit log are gitignored and were never committed. |
| Tokens | Read from Secret Manager when `SECRETS_PROJECT` is set, otherwise from `.env`. `deploy/secrets.sh` uploads and rotates them. |
| Bad configuration | The app refuses to start with missing or swapped tokens, or an invalid policy. Every policy error is listed with its location. |
| Policy changes | Edits to the policy file apply within 2 seconds, no restart. An invalid edit is rejected and logged, and the previous policy stays. |
| Model outage | Model checks time out after 15 s. On failure the rules still run, and `timing_ms.model_error` counts it. |
| Token optimization | Obvious findings are removed by rules with `model: null`. `make token-dashboard` turns `audit.jsonl` into a local report showing zero-token decisions, model call rate, and estimated tokens avoided. |
| False-positive recovery | Set `MANAGER_USER_IDS` to show a manager-only restore button on redacted `mask` posts. Originals are held only in memory for `REDACTION_RESTORE_TTL_SECONDS`, never in the durable queue or audit log. |
| Crashes and deploys | SIGTERM stops new events, lets workers finish their job, and exits. Unfinished jobs resume on start. |
| Health | `GET /healthz` returns 503 when Slack is disconnected or 50+ jobs have failed. The container has a HEALTHCHECK. |
| Logs | `LOG_FORMAT=json` gives one JSON object per line, which Cloud Logging parses. |
| Container | Runs as an unprivileged user. Only `/data` is writable state. |
| Coverage | `evals/cases.yaml` holds labelled cases, including known gaps. CI fails if a case regresses or a known gap is quietly fixed. |

**Before real customer data**

- Replace the SQLite queue with Pub/Sub for more than one instance.
- Distribute the Slack app with OAuth so each workspace installs it, instead of one owner token.
- Get a privacy review of the audit log retention period.

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
