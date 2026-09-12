"""Durable job queue on SQLite, with lanes.

Every event is written to disk before Slack gets its acknowledgement, so a crash never
loses a message. Jobs in one lane and partition stay in order. Failed jobs retry with
backoff and end up as 'dead' after MAX_ATTEMPTS so nothing disappears silently.

Lanes (topics) have their own partitions and workers:
  triage  partitioned by channel and author, so each author's messages stay in order
  deep    partitioned by message, any order, so slow model calls run side by side

On GCP this file is the only thing to replace: one Pub/Sub topic per lane, with an
ordering key on the triage topic, gives the same guarantees.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import zlib
from dataclasses import dataclass

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id           INTEGER PRIMARY KEY,
    dedupe       TEXT UNIQUE NOT NULL,
    topic        TEXT NOT NULL DEFAULT 'triage',   -- triage | deep
    partition    INTEGER NOT NULL,
    payload      TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'queued',   -- queued | processing | done | dead
    stage        TEXT NOT NULL DEFAULT '',         -- last side effect completed
    attempts     INTEGER NOT NULL DEFAULT 0,
    available_at REAL NOT NULL,
    created_at   REAL NOT NULL,
    claimed_at   REAL,
    finished_at  REAL,
    error        TEXT
);
CREATE TABLE IF NOT EXISTS cursors (
    key     TEXT PRIMARY KEY,
    last_ts TEXT NOT NULL
);
"""

MAX_ATTEMPTS = 5
MAX_BACKOFF_S = 30


@dataclass
class Job:
    id: int
    dedupe: str
    payload: dict
    attempts: int
    stage: str


class JobQueue:
    def __init__(
        self,
        path: str = ":memory:",
        partitions: int | dict[str, int] = 4,
        max_attempts: int = MAX_ATTEMPTS,
    ):
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")  # durable across process crashes
        self.db.executescript(SCHEMA)
        self._migrate()
        if isinstance(partitions, int):
            partitions = {"triage": partitions, "deep": partitions}
        self.partitions = partitions
        self.max_attempts = max_attempts
        self._cv = threading.Condition()

    def _migrate(self) -> None:
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(jobs)")}
        if "topic" not in cols:  # database from before the lanes existed
            self.db.execute("ALTER TABLE jobs ADD COLUMN topic TEXT NOT NULL DEFAULT 'triage'")
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS jobs_lane_head ON jobs (topic, partition, status, id)"
        )

    def partition_of(self, key: str, topic: str = "triage") -> int:
        return zlib.crc32(key.encode()) % self.partitions.get(topic, 1)

    def put(
        self,
        dedupe: str,
        payload: dict,
        partition_key: str,
        now: float | None = None,
        topic: str = "triage",
    ) -> bool:
        """Store a job. Returns False when the same dedupe key was already stored."""
        now = time.time() if now is None else now
        with self._cv:
            cur = self.db.execute(
                "INSERT OR IGNORE INTO jobs"
                " (dedupe, topic, partition, payload, available_at, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    dedupe,
                    topic,
                    self.partition_of(partition_key, topic),
                    json.dumps(payload),
                    now,
                    now,
                ),
            )
            added = cur.rowcount == 1
            if added:
                self._cv.notify_all()
            return added

    def claim(
        self,
        partition: int,
        wait: float = 0.0,
        now: float | None = None,
        topic: str = "triage",
    ) -> Job | None:
        """Take the oldest job in a lane's partition. A job waiting on backoff blocks the ones
        behind it, which keeps each author's messages in order."""
        deadline = time.time() + wait
        with self._cv:
            while True:
                t = time.time() if now is None else now
                row = self.db.execute(
                    "SELECT id, dedupe, payload, attempts, stage, available_at FROM jobs"
                    " WHERE topic = ? AND partition = ? AND status = 'queued'"
                    " ORDER BY id LIMIT 1",
                    (topic, partition),
                ).fetchone()
                if row and row[5] <= t:
                    self.db.execute(
                        "UPDATE jobs SET status = 'processing', attempts = attempts + 1,"
                        " claimed_at = ? WHERE id = ?",
                        (t, row[0]),
                    )
                    return Job(row[0], row[1], json.loads(row[2]), row[3] + 1, row[4])
                remaining = deadline - time.time()
                if remaining <= 0:
                    return None
                self._cv.wait(min(remaining, 0.5))

    def set_stage(self, job: Job, stage: str) -> None:
        with self._cv:
            self.db.execute("UPDATE jobs SET stage = ? WHERE id = ?", (stage, job.id))
        job.stage = stage

    def done(self, job: Job) -> None:
        with self._cv:
            self.db.execute(
                "UPDATE jobs SET status = 'done', finished_at = ?, error = NULL, payload = '{}'"
                " WHERE id = ?",
                (time.time(), job.id),
            )

    def fail(self, job: Job, error: str, now: float | None = None) -> str:
        """Retry later with backoff, or mark dead after max_attempts. Returns the new status."""
        now = time.time() if now is None else now
        status = "dead" if job.attempts >= self.max_attempts else "queued"
        delay = min(MAX_BACKOFF_S, 2**job.attempts)
        with self._cv:
            self.db.execute(
                "UPDATE jobs SET status = ?, available_at = ?, error = ?, finished_at = ?"
                " WHERE id = ?",
                (status, now + delay, error[:500], now if status == "dead" else None, job.id),
            )
            if status == "dead":
                # the message is still in Slack, so the job can be replayed by timestamp;
                # the queue itself keeps no message text
                self.db.execute("UPDATE jobs SET payload = '{}' WHERE id = ?", (job.id,))
            self._cv.notify_all()
        return status

    def recover(self) -> int:
        """After a crash, jobs left in 'processing' go back to the queue."""
        with self._cv:
            cur = self.db.execute("UPDATE jobs SET status = 'queued' WHERE status = 'processing'")
            return cur.rowcount

    def counts(self) -> dict[str, int]:
        with self._cv:
            rows = self.db.execute(
                "SELECT topic, status, COUNT(*) FROM jobs GROUP BY topic, status"
            ).fetchall()
        return {f"{topic}/{status}": n for topic, status, n in rows}

    def dead(self, limit: int = 20) -> list[tuple[int, str, str]]:
        with self._cv:
            return self.db.execute(
                "SELECT id, dedupe, error FROM jobs WHERE status = 'dead' ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()

    def advance_cursor(self, key: str, ts: str) -> None:
        with self._cv:
            self.db.execute(
                "INSERT INTO cursors (key, last_ts) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET"
                " last_ts = CASE WHEN CAST(excluded.last_ts AS REAL) > CAST(cursors.last_ts AS REAL)"
                " THEN excluded.last_ts ELSE cursors.last_ts END",
                (key, ts),
            )

    def cursors(self) -> list[tuple[str, str]]:
        with self._cv:
            return self.db.execute("SELECT key, last_ts FROM cursors").fetchall()

    def scrub_finished(self) -> int:
        """Wipe payloads of finished jobs written before scrubbing existed."""
        with self._cv:
            cur = self.db.execute(
                "UPDATE jobs SET payload = '{}' WHERE status IN ('done', 'dead') AND payload != '{}'"
            )
            return cur.rowcount

    def prune(self, older_than_s: float = 86400) -> int:
        with self._cv:
            cur = self.db.execute(
                "DELETE FROM jobs WHERE status = 'done' AND finished_at < ?",
                (time.time() - older_than_s,),
            )
            return cur.rowcount
