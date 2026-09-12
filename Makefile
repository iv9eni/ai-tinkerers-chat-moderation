.PHONY: gcp-start gcp-stop gcp-status gcp-logs gcp-health gcp-deploy setup run test lint data replay queue evals evals-model check-policy docker up down logs health token-dashboard

setup:        ; uv sync --extra dev && cp -n .env.example .env || true
run:          ; uv run python -m adapters.slack_app
test:         ; uv run pytest -q
lint:         ; uv run ruff check . && uv run ruff format --check .
data:         ; uv run python data/generate.py
replay:       ; uv run python scripts/replay.py data/demo_messages.json
queue:        ; uv run python scripts/queue_status.py
token-dashboard: ; uv run python scripts/token_dashboard.py
evals:        ; uv run python evals/run.py
evals-model:  ; uv run python evals/run.py --model
check-policy: ; uv run python -c "from blackline.policy import Policy; Policy.load(); print('policy ok')"
docker:       ; docker build -t blackline:latest .
up:           ; docker compose up -d --build
down:         ; docker compose down
logs:         ; docker compose logs -f
health:       ; curl -s localhost:$${PORT:-8080}/healthz; echo

# ---- Google Cloud VM (one copy of the bot at a time: stop the laptop bot before gcp-start)
GCLOUD      ?= gcloud
GCP_PROJECT ?= backline-508417
GCP_ZONE    ?= us-east1-b
GCP_VM      ?= blackline
VM_ARGS      = $(GCP_VM) --zone $(GCP_ZONE) --project $(GCP_PROJECT)

gcp-start:  ; $(GCLOUD) compute instances start $(VM_ARGS)
gcp-stop:   ; $(GCLOUD) compute instances stop $(VM_ARGS)
gcp-status: ; $(GCLOUD) compute instances describe $(VM_ARGS) --format="value(status)"
gcp-logs:   ; $(GCLOUD) logging read 'logName:"gcplogs-docker-driver"' --project $(GCP_PROJECT) --freshness=1h --limit 50 --format="value(timestamp,jsonPayload.message)"
gcp-health: ; $(GCLOUD) compute ssh $(VM_ARGS) --command 'curl -s localhost:8080/healthz'
gcp-deploy: ; PROJECT=$(GCP_PROJECT) GCLOUD=$(GCLOUD) ./deploy/gce.sh update
