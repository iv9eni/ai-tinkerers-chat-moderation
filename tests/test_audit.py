import hashlib
import json

from blackline import audit
from blackline.contract import Decision, Finding, Message
from blackline.jobqueue import JobQueue

CARD = "card 4111 1111 1111 1111"


def test_audit_line_has_no_text_and_a_keyed_hash(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIT_FILE", str(tmp_path / "a.jsonl"))
    monkeypatch.setenv("AUDIT_HMAC_KEY", "k1")
    monkeypatch.setattr(audit, "_key", None)
    msg = Message(id="C1.1", channel_id="C1", author_id="U1", text=CARD)
    f = Finding(entity="CREDIT_CARD", start=5, end=24, confidence=0.97, tier=0)
    audit.record(msg, Decision(action="mask", findings=[f]))
    line = (tmp_path / "a.jsonl").read_text()
    assert "4111" not in line and "1111 1111" not in line
    row = json.loads(line)
    assert row["text_hmac"] != hashlib.sha256(CARD.encode()).hexdigest()[:16]
    assert row["text_hmac"] == audit.fingerprint(CARD)  # stable with the same key


def test_fingerprint_depends_on_the_key(monkeypatch):
    monkeypatch.setattr(audit, "_key", b"a")
    a = audit.fingerprint(CARD)
    monkeypatch.setattr(audit, "_key", b"b")
    assert audit.fingerprint(CARD) != a


def _payload(jq, job_id):
    return jq.db.execute("SELECT payload FROM jobs WHERE id = ?", (job_id,)).fetchone()[0]


def test_queue_forgets_text_when_a_job_finishes():
    jq = JobQueue(":memory:", partitions=1)
    jq.put("a", {"event": {"text": CARD}}, "k", now=0)
    job = jq.claim(0, now=0)
    assert job.payload["event"]["text"] == CARD  # the worker still has it in memory
    jq.done(job)
    assert _payload(jq, job.id) == "{}"


def test_queue_forgets_text_when_a_job_dies():
    jq = JobQueue(":memory:", partitions=1, max_attempts=1)
    jq.put("a", {"event": {"text": CARD}}, "k", now=0)
    job = jq.claim(0, now=0)
    assert jq.fail(job, "boom", now=0) == "dead"
    assert _payload(jq, job.id) == "{}"


def test_scrub_cleans_old_finished_jobs():
    jq = JobQueue(":memory:", partitions=1)
    jq.put("a", {"event": {"text": CARD}}, "k", now=0)
    job = jq.claim(0, now=0)
    jq.db.execute("UPDATE jobs SET status = 'done' WHERE id = ?", (job.id,))  # pre-scrub row
    assert jq.scrub_finished() == 1 and _payload(jq, job.id) == "{}"
