import pytest

from adapters.slack_worker import DEEP, TRIAGE, Worker
from blackline import pipeline
from blackline.contract import Finding
from blackline.detectors import tier1
from blackline.jobqueue import JobQueue

CARD = "card 4532 0151 1283 0366"
EMOJI = ":four::one::one::one: :one::one::one::one: :one::one::one::one: :one::one::one::one:"


class FakeSlack:
    """Records every API call in one shared list so tests can check the order."""

    def __init__(self, name, calls):
        self.name, self.calls = name, calls

    def __getattr__(self, method):
        def call(**kw):
            self.calls.append((self.name, method, kw))
            return {
                "chat_postMessage": {"ok": True, "ts": f"{len(self.calls)}.000100"},
                "users_info": {
                    "user": {
                        "real_name": "Ivgeni",
                        "is_restricted": False,
                        "profile": {"display_name": "Ivgeni D", "image_72": "https://x/a.png"},
                    }
                },
                "conversations_info": {"channel": {"name": "blackline-test"}},
                "conversations_members": {"members": ["U1"]},
                "conversations_history": {"messages": []},
            }.get(method, {"ok": True})

        return call


@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIT_FILE", str(tmp_path / "audit.jsonl"))
    model = {"calls": [], "single": []}
    monkeypatch.setattr(
        tier1, "detect", lambda t: (model["calls"].append(t) or list(model["single"]), {})
    )
    monkeypatch.setattr(tier1, "detect_window", lambda ts: (model["calls"].append(ts) or [], {}))
    monkeypatch.setattr(tier1, "detect_encoded", lambda t: (model["calls"].append(t) or [], {}))
    pipeline.reload_policy()
    calls = []
    q = JobQueue(":memory:", partitions={"triage": 1, "deep": 1})
    w = Worker(FakeSlack("bot", calls), FakeSlack("user", calls), q)
    return w, q, calls, model


def hold_channel():
    pipeline.policy().channels["#blackline-test"] = {"hold": True}


def event(text, ts="1789229343.000100", **extra):
    return {"type": "message", "channel": "C1", "user": "U1", "ts": ts, "text": text, **extra}


def names(calls):
    return [(who, m) for who, m, _ in calls]


def run_triage(w, q):
    job = q.claim(0, topic=TRIAGE)
    d = w.triage(job)
    q.done(job)
    return d


def run_deep(w, q):
    job = q.claim(0, topic=DEEP)
    d = w.deep(job)
    q.done(job)
    return d


# ---- triage lane ----------------------------------------------------------------------


def test_card_is_deleted_before_any_other_call(setup):
    w, q, calls, model = setup
    w.enqueue(event(CARD))
    d = run_triage(w, q)
    assert d.action == "mask"
    assert names(calls)[0] == ("user", "chat_delete")
    assert model["calls"] == [] and "exposed" in d.timing_ms
    assert q.counts().get("deep/queued") is None  # settled in triage, no deep job


def test_emoji_card_is_deleted_in_triage(setup):
    w, q, calls, model = setup
    w.enqueue(event(EMOJI))
    d = run_triage(w, q)
    assert d.action == "mask" and names(calls)[0] == ("user", "chat_delete")
    posted = [kw["text"] for _, m, kw in calls if m == "chat_postMessage"]
    assert "emoji" in posted[0] and model["calls"] == []


def test_pending_model_check_does_not_delay_a_delete(setup):
    w, q, calls, model = setup
    w.enqueue(event("standup moved to 10:15", ts="1.1"))
    w.enqueue(event(CARD, ts="1.2"))
    assert run_triage(w, q) is None  # clean so far, handed to the deep lane
    assert run_triage(w, q).action == "mask"  # card removed while the model check still waits
    assert ("user", "chat_delete") in names(calls)
    assert model["calls"] == []
    assert q.counts()["deep/queued"] == 1


def test_retry_after_crash_does_not_delete_twice(setup):
    w, q, calls, _ = setup
    w.enqueue(event(CARD))
    job = q.claim(0, topic=TRIAGE)
    q.set_stage(job, "deleted")  # crashed after the delete landed
    w.triage(job)
    assert ("user", "chat_delete") not in names(calls)
    assert ("bot", "chat_postMessage") in names(calls)


def test_split_card_deletes_both_messages_in_triage(setup):
    w, q, calls, _ = setup
    w.enqueue(event("card first digits 4532 second digits 0151", ts="1789229282.822529"))
    w.enqueue(event("card second digits  1283 0366", ts="1789229287.676419"))
    assert run_triage(w, q) is None
    d = run_triage(w, q)
    deleted = [kw["ts"] for _, m, kw in calls if m == "chat_delete"]
    assert d.related_ids and deleted == ["1789229282.822529", "1789229287.676419"]


def test_duplicate_events_are_stored_once(setup):
    w, _, _, _ = setup
    assert w.enqueue(event(CARD))
    assert not w.enqueue(event(CARD))
    assert not w.enqueue({**event(CARD), "bot_id": "B1"})


# ---- deep lane ------------------------------------------------------------------------


def test_clean_message_goes_to_the_deep_lane_and_stays(setup):
    w, q, calls, model = setup
    w.enqueue(event("standup moved to 10:15"))
    run_triage(w, q)
    d = run_deep(w, q)
    assert d.action == "allow" and model["calls"]
    assert ("user", "chat_delete") not in names(calls)


def test_model_finding_is_removed_in_the_deep_lane(setup):
    w, q, calls, model = setup
    text = "her home address is 12 Elm St, Guelph"
    model["single"] = [
        Finding(
            entity="ADDRESS",
            start=20,
            end=len(text),
            confidence=0.95,
            subject="third_party",
            tier=1,
        )
    ]
    w.enqueue(event(text))
    run_triage(w, q)
    d = run_deep(w, q)
    assert d.action == "mask" and ("user", "chat_delete") in names(calls)
    assert "exposed" in d.timing_ms


def test_masked_message_has_manager_restore_button(setup, monkeypatch):
    monkeypatch.setenv("MANAGER_USER_IDS", "UMANAGER")
    w, q, calls, model = setup
    text = "her home address is 12 Elm St, Guelph"
    model["single"] = [
        Finding(
            entity="ADDRESS",
            start=20,
            end=len(text),
            confidence=0.95,
            subject="third_party",
            tier=1,
        )
    ]
    w.enqueue(event(text))
    run_triage(w, q)
    run_deep(w, q)

    [post] = [kw for _, m, kw in calls if m == "chat_postMessage"]

    assert post["blocks"][1]["elements"][0]["action_id"] == "blackline_restore"
    assert post["blocks"][1]["elements"][0]["value"]


def test_third_party_email_has_manager_restore_button(setup, monkeypatch):
    monkeypatch.setenv("MANAGER_USER_IDS", "UMANAGER")
    w, q, calls, model = setup
    text = "Her email is her@example.com"
    model["single"] = [
        Finding(
            entity="EMAIL",
            start=13,
            end=len(text),
            confidence=0.95,
            subject="third_party",
            tier=1,
        )
    ]
    w.enqueue(event(text))
    run_triage(w, q)
    d = run_deep(w, q)

    [post] = [kw for _, m, kw in calls if m == "chat_postMessage"]

    assert d.action == "mask"
    assert post["text"] == "Her email is " + "\u2588" * len("her@example.com")
    assert post["blocks"][1]["elements"][0]["action_id"] == "blackline_restore"


def test_manager_can_restore_a_false_positive(setup, monkeypatch):
    monkeypatch.setenv("MANAGER_USER_IDS", "UMANAGER")
    w, q, calls, model = setup
    text = "her home address is 12 Elm St, Guelph"
    model["single"] = [
        Finding(
            entity="ADDRESS",
            start=20,
            end=len(text),
            confidence=0.95,
            subject="third_party",
            tier=1,
        )
    ]
    w.enqueue(event(text))
    run_triage(w, q)
    run_deep(w, q)
    [post] = [kw for _, m, kw in calls if m == "chat_postMessage"]
    restore_id = post["blocks"][1]["elements"][0]["value"]

    ok, message = w.restore_redaction(restore_id, "UMANAGER")
    restored = [kw for _, m, kw in calls if m == "chat_postMessage"][-1]

    assert ok and "Restored" in message
    assert ("bot", "chat_delete") in names(calls)
    assert restored["text"] == text
    assert restored["username"] == "Ivgeni D (restored by Blackline)"


def test_non_manager_cannot_restore(setup, monkeypatch):
    monkeypatch.setenv("MANAGER_USER_IDS", "UMANAGER")
    w, q, calls, model = setup
    text = "her home address is 12 Elm St, Guelph"
    model["single"] = [
        Finding(
            entity="ADDRESS",
            start=20,
            end=len(text),
            confidence=0.95,
            subject="third_party",
            tier=1,
        )
    ]
    w.enqueue(event(text))
    run_triage(w, q)
    run_deep(w, q)
    [post] = [kw for _, m, kw in calls if m == "chat_postMessage"]
    restore_id = post["blocks"][1]["elements"][0]["value"]

    ok, message = w.restore_redaction(restore_id, "UOTHER")

    assert not ok and "managers" in message
    assert w.restores.consume(restore_id) is not None


# ---- hold mode ------------------------------------------------------------------------


def test_hold_deletes_every_message_first_then_releases_clean_ones(setup):
    w, q, calls, _ = setup
    hold_channel()
    w.enqueue(event("standup moved to 10:15"))
    run_triage(w, q)
    assert ("user", "chat_delete") in names(calls)
    first_delete = names(calls).index(("user", "chat_delete"))
    assert ("bot", "chat_postMessage") not in names(calls)[:first_delete]
    d = run_deep(w, q)
    assert d.action == "allow"
    [release] = [kw for _, m, kw in calls if m == "chat_postMessage"]
    assert release["text"] == "standup moved to 10:15"
    assert release["username"] == "Ivgeni D (via Blackline)"
    assert release["icon_url"] == "https://x/a.png"


def test_hold_with_model_finding_posts_the_masked_copy_not_the_original(setup):
    w, q, calls, model = setup
    hold_channel()
    text = "her home address is 12 Elm St, Guelph"
    model["single"] = [
        Finding(
            entity="ADDRESS",
            start=20,
            end=len(text),
            confidence=0.95,
            subject="third_party",
            tier=1,
        )
    ]
    w.enqueue(event(text))
    run_triage(w, q)
    d = run_deep(w, q)
    posts = [kw["text"] for _, m, kw in calls if m == "chat_postMessage"]
    assert d.action == "mask" and posts == ["her home address is " + "█" * 17]
    assert len([1 for _, m, _ in calls if m == "chat_delete"]) == 1  # held once, never again


def test_release_keeps_thread_replies_in_their_thread(setup):
    w, q, calls, _ = setup
    hold_channel()
    w.enqueue(event("sounds good", ts="2.2", thread_ts="2.1"))
    run_triage(w, q)
    run_deep(w, q)
    [release] = [kw for _, m, kw in calls if m == "chat_postMessage"]
    assert release["thread_ts"] == "2.1"
