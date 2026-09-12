.PHONY: setup run test lint data replay queue evals evals-model check-policy docker up down logs health

setup:        ; uv sync --extra dev && cp -n .env.example .env || true
run:          ; uv run python -m adapters.slack_app
test:         ; uv run pytest -q
lint:         ; uv run ruff check . && uv run ruff format --check .
data:         ; uv run python data/generate.py
replay:       ; uv run python scripts/replay.py data/demo_messages.json
queue:        ; uv run python scripts/queue_status.py
evals:        ; uv run python evals/run.py
evals-model:  ; uv run python evals/run.py --model
check-policy: ; uv run python -c "from blackline.policy import Policy; Policy.load(); print('policy ok')"
docker:       ; docker build -t blackline:latest .
up:           ; docker compose up -d --build
down:         ; docker compose down
logs:         ; docker compose logs -f
health:       ; curl -s localhost:$${PORT:-8080}/healthz; echo
