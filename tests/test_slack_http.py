import json

import pytest
from slack_sdk.errors import SlackApiError

from adapters.slack_http import KeepAliveSlack


class FakeResp:
    def __init__(self, status, body, headers=None):
        self.status, self._body, self._headers = status, body, headers or {}

    def read(self):
        return json.dumps(self._body).encode()

    def getheader(self, name):
        return self._headers.get(name)


class FakeConn:
    """Plays back scripted responses. An exception in the script is raised on request."""

    opened = 0

    def __init__(self, script):
        FakeConn.opened += 1
        self.script, self.requests, self.closed = script, [], False

    def request(self, verb, path, body, headers):
        self.requests.append((path, body))
        if isinstance(self.script[0], Exception):
            raise self.script.pop(0)

    def getresponse(self):
        return self.script.pop(0)

    def close(self):
        self.closed = True


def client(script, pool_size=1):
    FakeConn.opened = 0
    conns = []

    def connect():
        c = FakeConn(script)
        conns.append(c)
        return c

    return KeepAliveSlack("xoxp-test", pool_size=pool_size, connect=connect), conns


def test_calls_reuse_one_connection():
    ok = FakeResp(200, {"ok": True})
    c, conns = client([ok, ok, ok])
    c.chat_delete(channel="C1", ts="1.1")
    c.chat_delete(channel="C1", ts="1.2")
    c.conversations_info(channel="C1")
    assert FakeConn.opened == 1
    assert [p for p, _ in conns[0].requests] == [
        "/api/chat.delete",
        "/api/chat.delete",
        "/api/conversations.info",
    ]
    assert conns[0].requests[0][1] == "channel=C1&ts=1.1"


def test_reconnects_when_server_closed_the_idle_connection():
    c, conns = client([ConnectionResetError(), FakeResp(200, {"ok": True})])
    assert c.chat_delete(channel="C1", ts="1.1")["ok"]
    assert FakeConn.opened == 2 and conns[0].closed


def test_rate_limit_waits_and_retries():
    c, _ = client([FakeResp(429, {}, {"Retry-After": "0"}), FakeResp(200, {"ok": True})])
    assert c.chat_delete(channel="C1", ts="1.1")["ok"]


def test_slack_error_raises_like_webclient():
    c, _ = client([FakeResp(200, {"ok": False, "error": "message_not_found"})])
    with pytest.raises(SlackApiError) as e:
        c.chat_delete(channel="C1", ts="1.1")
    assert e.value.response.get("error") == "message_not_found"


def test_warm_opens_every_connection():
    ok = FakeResp(200, {"ok": True})
    c, _ = client([ok, ok, ok], pool_size=3)
    assert c.warm() == 3 and FakeConn.opened == 3
