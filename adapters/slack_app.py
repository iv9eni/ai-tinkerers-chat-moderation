"""Slack entry point. Socket Mode, always on.

The listener only writes the event to the durable queue. Slack gets its acknowledgement
after the write, so a crash at any point after that is recovered on restart. Worker
threads do the real work, one per partition, so each author's messages stay in order.
"""

from __future__ import annotations

import logging
import os
import threading

from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk import WebClient

from adapters.slack_worker import Worker
from blackline.jobqueue import JobQueue

log = logging.getLogger("blackline")


def main() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    workers = int(os.environ.get("WORKERS", "4"))
    queue = JobQueue(os.environ.get("QUEUE_DB", "blackline.db"), partitions=workers)
    worker = Worker(
        WebClient(token=os.environ["SLACK_BOT_TOKEN"]),
        WebClient(token=os.environ["SLACK_USER_TOKEN"]),
        queue,
    )

    requeued = queue.recover()
    pruned = queue.prune()
    missed = worker.catch_up()

    # process_before_response: ack only after the event is safely on disk
    app = App(token=os.environ["SLACK_BOT_TOKEN"], process_before_response=True)

    @app.event("message")
    def on_message(event: dict) -> None:
        worker.enqueue(event)

    stop = threading.Event()
    for p in range(workers):
        threading.Thread(
            target=worker.run_forever, args=(p, stop), name=f"worker-{p}", daemon=True
        ).start()

    log.info(
        "Blackline listening via Socket Mode. workers=%d recovered=%d caught_up=%d pruned=%d queue=%s",
        workers,
        requeued,
        missed,
        pruned,
        queue.counts(),
    )
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()


if __name__ == "__main__":
    main()
