"""Show queue health: job counts by status and the latest dead jobs.

Usage: make queue
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

from blackline.jobqueue import JobQueue

load_dotenv(".env")
q = JobQueue(os.environ.get("QUEUE_DB", "blackline.db"))
print("jobs by status:", q.counts() or "empty")
print("channel cursors:", q.cursors() or "none yet")
for job_id, key, error in q.dead():
    print(f"dead #{job_id} {key}: {error}")
