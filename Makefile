.PHONY: setup run test lint data replay
setup: ; uv sync --extra dev && cp -n .env.example .env || true
run:   ; uv run python -m adapters.slack_app
test:  ; uv run pytest -q
lint:  ; uv run ruff check . && uv run ruff format --check .
data:  ; uv run python data/generate.py
replay:; uv run python scripts/replay.py data/demo_messages.json
