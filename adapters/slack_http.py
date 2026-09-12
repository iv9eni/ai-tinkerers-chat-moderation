"""Slack Web API client that keeps its HTTPS connections open.

slack_sdk's WebClient opens a new TCP and TLS connection for every call. Measured from a
laptop that cost about 540 ms per call, against about 57 ms on a reused connection. The
delete is the one call that decides how long a message stays visible, so it gets a pool
of warm connections.

Same call style as WebClient (client.chat_delete(channel=..., ts=...)) and the same
SlackApiError on failure, so the worker does not care which client it has.
"""

from __future__ import annotations

import http.client
import json
import logging
import queue
import ssl
import threading
import time
from collections.abc import Callable
from urllib.parse import urlencode

from slack_sdk.errors import SlackApiError

log = logging.getLogger("blackline")


class _Conn:
    def __init__(self, raw: http.client.HTTPSConnection):
        self.raw = raw
        self.last_used = 0.0


class KeepAliveSlack:
    def __init__(
        self,
        token: str,
        pool_size: int = 4,
        host: str = "slack.com",
        timeout: float = 10.0,
        connect: Callable[[], http.client.HTTPSConnection] | None = None,
    ):
        self.token = token
        self._connect = connect or (
            lambda: http.client.HTTPSConnection(
                host, timeout=timeout, context=ssl.create_default_context()
            )
        )
        self._pool: queue.LifoQueue[_Conn | None] = queue.LifoQueue()  # warmest first
        for _ in range(pool_size):
            self._pool.put(None)
        self.pool_size = pool_size

    # client.chat_delete(...) -> api_call("chat.delete", ...)
    def __getattr__(self, name: str) -> Callable[..., dict]:
        if name.startswith("_"):
            raise AttributeError(name)
        method = name.replace("_", ".", 1)
        return lambda **params: self.api_call(method, **params)

    def api_call(self, method: str, **params) -> dict:
        body = urlencode({k: v for k, v in params.items() if v is not None})
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
        }
        conn = self._pool.get()
        data: dict = {}
        try:
            for attempt in range(3):
                if conn is None:
                    conn = _Conn(self._connect())
                try:
                    conn.raw.request("POST", f"/api/{method}", body=body, headers=headers)
                    resp = conn.raw.getresponse()
                    raw = resp.read()
                    conn.last_used = time.time()
                except (http.client.HTTPException, OSError):
                    conn.raw.close()  # server closed an idle connection, open a new one
                    conn = None
                    if attempt == 2:
                        raise
                    continue
                if resp.status == 429:
                    time.sleep(float(resp.getheader("Retry-After") or 1))
                    continue
                data = json.loads(raw or b"{}")
                break
        finally:
            self._pool.put(conn)
        if not data.get("ok"):
            raise SlackApiError(f"The request to the Slack API failed. (method: {method})", data)
        return data

    def warm(self) -> int:
        """Open or refresh every idle connection. Returns how many were pinged."""
        taken: list[_Conn | None] = []
        pinged = 0
        try:
            while True:
                taken.append(self._pool.get_nowait())
        except queue.Empty:
            pass
        for i, conn in enumerate(taken):
            try:
                if conn is None:
                    conn = _Conn(self._connect())
                conn.raw.request("POST", "/api/api.test", body="", headers={})
                conn.raw.getresponse().read()
                conn.last_used = time.time()
                pinged += 1
            except (http.client.HTTPException, OSError):
                if conn is not None:
                    conn.raw.close()
                conn = None
            taken[i] = conn
        for conn in taken:
            self._pool.put(conn)
        return pinged

    def keep_warm(self, stop: threading.Event, every_s: float = 20.0) -> None:
        while not stop.wait(every_s):
            self.warm()
