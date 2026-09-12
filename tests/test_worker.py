import pytest

from adapters.slack_worker import Worker
from blackline import pipeline
from blackline.detectors import tier1
from blackline.jobqueue import JobQueue

CARD = "card 4532 0151 1283 0366"


class FakeSlack:
    """Records every API call in one shared list so tests can check the order."""

    def __init__(self, name, calls):
        self.name, self.calls = name, calls

    def __getattr__(self, method):
        def call(**kw):
            self.calls.append((self.name, method, kw))
            return {
                "users_info": {"user": {"real_name": "Ivgeni", "is_restricted": False}},
                "conversations_info": {"channel": {"name": "blackline-test"}},
                "conversations_members": {"members": ["U1"]},
                "conversations_history": {"messages": []},
            }.get(method, {"ok": True})

        return call


@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIT_FILE", str(tmp_path / "audit.jsonl"))
    model_calls = []
    monkeypatch.setattr(tier1, "detect", lambda t: (model_calls.append(t) or [], {}))
    monkeypatch.setattr(tier1, "detect_window", lambda ts: (model_calls.append(ts) or [], {}))
    pipeline.reload_policy()
    calls = []
    q = JobQueue(":memory:", partitions=1)
    w = Worker(FakeSlack("bot", calls), FakeSlack("user", calls), q)
    return w, q, calls, model_calls


def event(text, ts="1789229343.000100"):
    return {"type": "message", "channel": "C1", "user": "U1", "ts": ts, "text": text}


def names(calls):
    return [(who, m) for who, m, _ in calls]


def test_card_is_deleted_before_any_other_call(setup):
    w, q, calls, model_calls = setup
    w.enqueue(event(CARD))
    d = w.handle(q.claim(0))
    assert d.action == "mask"
    assert names(calls)[0] == ("user", "chat_delete")  # nothing slower ran first
    assert ("bot", "conversations_info") not in names(calls)
    assert model_calls == []
    assert "exposed" in d.timing_ms


def test_retry_after_crash_does_not_delete_twice(setup):
    w, q, calls, _ = setup
    w.enqueue(event(CARD))
    job = q.claim(0)
    q.set_stage(job, "deleted")  # crashed after the delete landed
    w.handle(job)
    assert ("user", "chat_delete") not in names(calls)
    assert ("bot", "chat_postMessage") in names(calls)


def test_clean_message_goes_through_the_full_path(setup):
    w, q, calls, model_calls = setup
    w.enqueue(event("standup moved to 10:15"))
    d = w.handle(q.claim(0))
    assert d.action == "allow"
    assert ("user", "chat_delete") not in names(calls)
    assert model_calls  # the model check ran because rules found nothing


def test_split_card_deletes_both_messages(setup):
    w, q, calls, _ = setup
    w.enqueue(event("card first digits 4532 second digits 0151", ts="1789229282.822529"))
    first = q.claim(0)
    assert w.handle(first).action == "allow"
    q.done(first)
    w.enqueue(event("card second digits  1283 0366", ts="1789229287.676419"))
    d = w.handle(q.claim(0))
    deleted = [kw["ts"] for who, m, kw in calls if m == "chat_delete"]
    assert d.related_ids and deleted == ["1789229282.822529", "1789229287.676419"]


def test_duplicate_events_are_stored_once(setup):
    w, _, _, _ = setup
    assert w.enqueue(event(CARD))
    assert not w.enqueue(event(CARD))
    assert not w.enqueue({**event(CARD), "bot_id": "B1"})
