"""Health endpoint for the hosting platform and for people.

GET /healthz returns 200 when the Slack connection is up and few jobs have failed for good,
503 otherwise, with a JSON body that says why.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from blackline.jobqueue import JobQueue


class Health:
    def __init__(self, queue: JobQueue, is_connected: Callable[[], bool], dead_limit: int = 50):
        self.queue = queue
        self.is_connected = is_connected
        self.dead_limit = dead_limit
        self.started = time.time()
        self.last_event: float | None = None

    def mark_event(self) -> None:
        self.last_event = time.time()

    def snapshot(self) -> tuple[int, dict]:
        counts = self.queue.counts()
        dead = sum(n for k, n in counts.items() if k.endswith("/dead"))
        queued = sum(n for k, n in counts.items() if k.endswith("/queued"))
        try:
            connected = bool(self.is_connected())
        except Exception:  # noqa: BLE001
            connected = False
        problems = []
        if not connected:
            problems.append("not connected to Slack")
        if dead >= self.dead_limit:
            problems.append(f"{dead} jobs failed for good")
        body = {
            "status": "degraded" if problems else "ok",
            "problems": problems,
            "slack_connected": connected,
            "queued": queued,
            "dead": dead,
            "jobs": counts,
            "uptime_s": int(time.time() - self.started),
            "last_event_age_s": None
            if self.last_event is None
            else int(time.time() - self.last_event),
        }
        return (503 if problems else 200), body

    def serve(self, port: int, host: str = "0.0.0.0") -> ThreadingHTTPServer:
        health = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path not in ("/", "/healthz"):
                    self.send_response(404)
                    self.end_headers()
                    return
                code, body = health.snapshot()
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args) -> None:
                pass

        server = ThreadingHTTPServer((host, port), Handler)
        threading.Thread(target=server.serve_forever, name="health", daemon=True).start()
        return server
