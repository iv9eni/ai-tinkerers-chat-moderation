import json
import urllib.error
import urllib.request

from adapters.health import Health
from adapters.slack_app import JsonFormatter, check_env
from blackline.jobqueue import JobQueue


def get(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_healthz_ok_and_degraded():
    q = JobQueue(":memory:", partitions=1)
    state = {"up": True}
    h = Health(q, is_connected=lambda: state["up"])
    server = h.serve(0, host="127.0.0.1")
    port = server.server_address[1]
    try:
        code, body = get(port)
        assert code == 200 and body["status"] == "ok" and body["slack_connected"]
        state["up"] = False
        code, body = get(port)
        assert code == 503 and body["problems"] == ["not connected to Slack"]
    finally:
        server.shutdown()


def test_check_env_catches_missing_and_swapped_tokens(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxp-wrong-kind")
    monkeypatch.delenv("SLACK_APP_TOKEN", raising=False)
    monkeypatch.setenv("SLACK_USER_TOKEN", "xoxp-ok")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-ok")
    monkeypatch.setenv("WORKERS", "four")
    problems = check_env()
    assert "SLACK_BOT_TOKEN should start with xoxb-" in problems
    assert "SLACK_APP_TOKEN is not set" in problems
    assert any("WORKERS" in p for p in problems)


def test_json_logs():
    import logging

    rec = logging.LogRecord("blackline", logging.INFO, __file__, 1, "hello %s", ("x",), None)
    assert json.loads(JsonFormatter().format(rec))["message"] == "hello x"
