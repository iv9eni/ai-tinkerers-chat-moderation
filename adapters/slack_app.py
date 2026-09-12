"""Slack entry point. Socket Mode, always on.

The listener only writes the event to the durable queue. Slack gets its acknowledgement
after the write, so a crash at any point after that is recovered on restart.

Two lanes of worker threads:
  triage  one thread per partition, rules only, deletes first, keeps each author in order
  deep    model checks, its own threads, so a slow call never delays a delete

Production behaviour:
  - reads tokens from Secret Manager when SECRETS_PROJECT is set
  - refuses to start with missing or wrong-looking tokens, or an invalid policy
  - GET /healthz on $PORT for the hosting platform
  - SIGTERM or Ctrl+C: stop taking new events, let workers finish their current job, exit
  - LOG_FORMAT=json for one JSON object per log line
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading

from dotenv import dotenv_values, find_dotenv, load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from adapters.health import Health
from adapters.slack_http import KeepAliveSlack
from adapters.slack_worker import DEEP, TRIAGE, Worker
from blackline import pipeline, secrets
from blackline.jobqueue import JobQueue
from blackline.policy import PolicyError

log = logging.getLogger("blackline")

TOKEN_PREFIXES = {
    "SLACK_BOT_TOKEN": "xoxb-",
    "SLACK_APP_TOKEN": "xapp-",
    "SLACK_USER_TOKEN": "xoxp-",
    "OPENROUTER_API_KEY": "sk-or-",
}


def check_env() -> list[str]:
    problems = []
    for name, prefix in TOKEN_PREFIXES.items():
        value = os.environ.get(name, "")
        if not value:
            problems.append(f"{name} is not set")
        elif not value.startswith(prefix):
            problems.append(f"{name} should start with {prefix}")
    for name in ("WORKERS", "DEEP_WORKERS", "PORT"):
        value = os.environ.get(name)
        if value is not None and not value.isdigit():
            problems.append(f"{name} must be a whole number, got {value!r}")
    return problems


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "severity": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "thread": record.threadName,
        }
        if record.exc_info:
            out["exception"] = self.formatException(record.exc_info)
        return json.dumps(out)


def setup_logging(fmt: str | None = None) -> None:
    handler = logging.StreamHandler()
    if (fmt or os.environ.get("LOG_FORMAT")) == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), handlers=[handler], force=True)


def load_config(env_path: str, file_values: dict, client=None) -> list[str]:
    """Shell environment first, then Secret Manager, then the .env file.
    Secrets load before .env, and load_dotenv never overrides a variable that is set."""
    project = os.environ.get("SECRETS_PROJECT") or file_values.get("SECRETS_PROJECT") or ""
    loaded = secrets.load(project, client=client)
    if env_path:
        load_dotenv(env_path)
    return loaded


def main() -> None:
    env_path = find_dotenv(usecwd=True)
    file_values = dotenv_values(env_path) if env_path else {}
    setup_logging(os.environ.get("LOG_FORMAT") or file_values.get("LOG_FORMAT"))
    try:
        loaded = load_config(env_path, file_values)
    except secrets.SecretsError as e:
        log.error("cannot start: %s", e)
        sys.exit(2)
    if loaded:
        log.info("loaded %s from Secret Manager", ", ".join(loaded))

    problems = check_env()
    if problems:
        log.error("cannot start: %s", "; ".join(problems))
        sys.exit(2)
    try:
        pipeline.policy()
    except PolicyError as e:
        log.error("cannot start: %s", e)
        sys.exit(2)

    workers = int(os.environ.get("WORKERS", "4"))
    deep_workers = int(os.environ.get("DEEP_WORKERS", "8"))
    queue = JobQueue(
        os.environ.get("QUEUE_DB", "blackline.db"),
        partitions={"triage": workers, "deep": deep_workers},
    )
    # warm, kept-alive connections: the delete decides how long a message stays visible
    bot = KeepAliveSlack(os.environ["SLACK_BOT_TOKEN"], pool_size=workers + deep_workers)
    user = KeepAliveSlack(os.environ["SLACK_USER_TOKEN"], pool_size=workers + 2)
    bot.warm()
    user.warm()
    worker = Worker(bot, user, queue)

    requeued = queue.recover()
    scrubbed = queue.scrub_finished()
    pruned = queue.prune()
    missed = worker.catch_up()

    # process_before_response: ack only after the event is safely on disk
    app = App(token=os.environ["SLACK_BOT_TOKEN"], process_before_response=True)
    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    health = Health(queue, is_connected=lambda: handler.client.is_connected())

    @app.event("message")
    def on_message(event: dict) -> None:
        health.mark_event()
        worker.enqueue(event)

    @app.action("blackline_restore")
    def on_restore(ack, body: dict, respond) -> None:
        ack()
        actor = (body.get("user") or {}).get("id", "")
        action = (body.get("actions") or [{}])[0]
        ok, text = worker.restore_redaction(action.get("value", ""), actor)
        respond(text=text, response_type="ephemeral", replace_original=False)
        if ok:
            health.mark_event()

    stop = threading.Event()
    threads: list[threading.Thread] = []
    for client in (bot, user):
        threading.Thread(target=client.keep_warm, args=(stop,), daemon=True).start()
    for topic, n in ((TRIAGE, workers), (DEEP, deep_workers)):
        for p in range(n):
            t = threading.Thread(
                target=worker.run_forever, args=(topic, p, stop), name=f"{topic}-{p}", daemon=True
            )
            t.start()
            threads.append(t)

    port = int(os.environ.get("PORT", "8080"))
    server = health.serve(port)

    def shutdown(signum: int, _frame: object) -> None:
        log.info("received %s, shutting down", signal.Signals(signum).name)
        stop.set()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    handler.connect()
    log.info(
        "Blackline listening via Socket Mode. triage=%d deep=%d recovered=%d scrubbed=%d"
        " caught_up=%d pruned=%d health=:%d/healthz queue=%s",
        workers,
        deep_workers,
        requeued,
        scrubbed,
        missed,
        pruned,
        port,
        queue.counts(),
    )
    while not stop.wait(1.0):
        pass

    handler.close()  # no new events; anything Slack sends now is redelivered or caught up
    for t in threads:
        t.join(timeout=15)  # let each worker finish the job it holds
    server.shutdown()
    log.info("stopped cleanly. queue=%s", queue.counts())


if __name__ == "__main__":
    main()
