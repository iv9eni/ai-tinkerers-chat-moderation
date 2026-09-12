from blackline.jobqueue import JobQueue


def q(**kw):
    return JobQueue(":memory:", partitions=1, **kw)


def test_put_is_idempotent():
    jq = q()
    assert jq.put("C1.1", {"n": 1}, "C1:U1")
    assert not jq.put("C1.1", {"n": 1}, "C1:U1")
    assert jq.counts() == {"queued": 1}


def test_claim_in_order_and_done():
    jq = q()
    jq.put("a", {"n": 1}, "k", now=0)
    jq.put("b", {"n": 2}, "k", now=0)
    j1 = jq.claim(0, now=1)
    assert j1.payload == {"n": 1} and j1.attempts == 1
    jq.done(j1)
    assert jq.claim(0, now=1).payload == {"n": 2}


def test_failed_job_blocks_its_partition_until_backoff_then_retries():
    jq = q()
    jq.put("a", {"n": 1}, "k", now=0)
    jq.put("b", {"n": 2}, "k", now=0)
    j = jq.claim(0, now=0)
    assert jq.fail(j, "boom", now=0) == "queued"
    assert jq.claim(0, now=1) is None  # backoff 2 s, and b must not jump the queue
    retry = jq.claim(0, now=3)
    assert retry.dedupe == "a" and retry.attempts == 2


def test_dead_after_max_attempts():
    jq = q(max_attempts=2)
    jq.put("a", {}, "k", now=0)
    jq.fail(jq.claim(0, now=0), "boom", now=0)
    assert jq.fail(jq.claim(0, now=100), "boom again", now=100) == "dead"
    assert jq.dead()[0][1] == "a"
    assert jq.claim(0, now=1000) is None


def test_recover_requeues_jobs_from_a_crash(tmp_path):
    path = str(tmp_path / "q.db")
    jq = JobQueue(path, partitions=1)
    jq.put("a", {"n": 1}, "k", now=0)
    job = jq.claim(0, now=0)
    jq.set_stage(job, "deleted")
    del jq  # process dies mid-job
    jq2 = JobQueue(path, partitions=1)
    assert jq2.recover() == 1
    again = jq2.claim(0, now=1)
    assert again.dedupe == "a" and again.stage == "deleted"


def test_cursor_only_moves_forward():
    jq = q()
    jq.advance_cursor("C1", "1789229343.562509")
    jq.advance_cursor("C1", "1789229000.000000")
    assert jq.cursors() == [("C1", "1789229343.562509")]
