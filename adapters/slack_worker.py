"""Processes queued Slack events. Delete first, think later.

Order of work for one message:
  1. rules only on the text and the author's recent messages, no network
  2. if that removes the message: delete now, then notify
  3. otherwise: channel lookup, model checks, then act
Every side effect is recorded as a stage on the job, so a retry after a crash never
deletes twice or posts the notice twice.
"""

from __future__ import annotations

import logging
import threading
import time

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from blackline import audit, pipeline
from blackline.actions import DISGUISED_NOTICE, SPLIT_NOTICE, mask
from blackline.contract import Decision, Message
from blackline.jobqueue import Job, JobQueue
from blackline.window import ConversationWindow

log = logging.getLogger("blackline")
REMOVING = pipeline.REMOVING


def human_event(event: dict) -> dict | None:
    """The message a person wrote, or None for bots, joins, and other system events."""
    if event.get("subtype") == "message_changed":
        inner = event.get("message") or {}
        if inner.get("bot_id") or inner.get("subtype") or not inner.get("user"):
            return None
        return {**inner, "channel": event["channel"]}
    if event.get("subtype") or event.get("bot_id") or not event.get("user"):
        return None
    return event


def dedupe_key(ev: dict) -> str:
    edited = (ev.get("edited") or {}).get("ts")
    base = f"{ev['channel']}.{ev['ts']}"
    return f"{base}.e{edited}" if edited else base


class Worker:
    def __init__(
        self,
        bot: WebClient,
        user: WebClient,
        queue: JobQueue,
        window: ConversationWindow | None = None,
    ):
        self.bot = bot
        self.user = user
        self.queue = queue
        self.window = window or ConversationWindow()
        self._channels: dict[str, dict] = {}

    # ---- intake (runs inside the Slack listener, must be fast) ----------------------

    def enqueue(self, event: dict, received_at: float | None = None) -> bool:
        ev = human_event(event)
        if ev is None:
            return False
        payload = {"event": ev, "received_at": received_at or time.time()}
        return self.queue.put(
            dedupe_key(ev), payload, partition_key=f"{ev['channel']}:{ev['user']}"
        )

    # ---- Slack lookups ---------------------------------------------------------------

    def channel(self, channel_id: str, lookup: bool) -> dict:
        if channel_id in self._channels or not lookup:
            return self._channels.get(channel_id, {"name": "", "guests": False})
        try:
            info = self.bot.conversations_info(channel=channel_id)["channel"]
            members = self.bot.conversations_members(channel=channel_id)["members"]
            guests = any(
                self.bot.users_info(user=u)["user"].get("is_restricted") for u in members[:50]
            )
            result = {"name": info.get("name", ""), "guests": guests}
        except SlackApiError:
            result = {"name": "", "guests": False}
        self._channels[channel_id] = result
        return result

    def to_message(self, ev: dict, lookup: bool) -> Message:
        ch = self.channel(ev["channel"], lookup)
        return Message(
            id=f"{ev['channel']}.{ev['ts']}",
            channel_id=ev["channel"],
            channel_name=ch["name"],
            channel_has_guests=ch["guests"],
            author_id=ev["user"],
            text=ev.get("text", ""),
        )

    def author_name(self, user_id: str) -> str:
        try:
            return self.bot.users_info(user=user_id)["user"].get("real_name") or "someone"
        except SlackApiError:
            return "someone"

    # ---- processing ------------------------------------------------------------------

    def handle(self, job: Job) -> Decision:
        ev = job.payload["event"]
        started = time.time()
        msg = self.to_message(ev, lookup=False)  # cached channel info only, no network
        earlier = self.window.recent(msg)

        t = time.perf_counter()
        decision = pipeline.fast_check(msg, earlier)
        path = "fast"
        if decision is None:
            msg = self.to_message(ev, lookup=True)
            decision = pipeline.run(msg, window=earlier, audit_log=False)
            path = "full"
        decision.timing_ms["decide"] = int((time.perf_counter() - t) * 1000)
        decision.timing_ms["queue"] = int((started - job.payload["received_at"]) * 1000)

        if decision.action in REMOVING:
            self.remove(job, decision, msg, ev)
        if decision.action in ("allow", "log", "warn"):
            self.window.add(msg)
        if decision.action not in ("allow", "log"):
            self.notify(job, decision, msg)

        audit.record(msg, decision)
        log.info(
            "%s %s %s path=%s related=%d %s",
            msg.id,
            decision.action,
            decision.rule_id or "-",
            path,
            len(decision.related_ids),
            decision.timing_ms,
        )
        return decision

    def remove(self, job: Job, decision: Decision, msg: Message, ev: dict) -> None:
        targets = decision.related_ids or [msg.id]
        if job.stage == "":
            if not self.delete(msg.channel_id, targets):
                decision.action = "warn"  # could not remove, fall back to telling the author
                return
            posted_at = float((ev.get("edited") or {}).get("ts") or ev["ts"])
            decision.timing_ms["exposed"] = int((time.time() - posted_at) * 1000)
            self.queue.set_stage(job, "deleted")
        self.window.forget(targets)

    def delete(self, channel: str, message_ids: list[str]) -> bool:
        ok = True
        for mid in message_ids:
            try:
                self.user.chat_delete(channel=channel, ts=mid.split(".", 1)[1])
            except SlackApiError as e:
                if e.response.get("error") == "message_not_found":
                    continue  # already gone, a retry is fine
                log.warning("delete %s failed: %s", mid, e.response.get("error"))
                ok = False
        return ok

    def notify(self, job: Job, decision: Decision, msg: Message) -> None:
        if decision.action == "mask" and job.stage == "deleted":
            if decision.related_ids:
                text = SPLIT_NOTICE.format(n=len(decision.related_ids))
            elif any(f.note == "obfuscated" for f in decision.findings):
                text = DISGUISED_NOTICE
            else:
                text = mask(msg.text, [f for f in decision.findings if f.subject != "self"])
            self.bot.chat_postMessage(
                channel=msg.channel_id,
                text=text,
                username=f"{self.author_name(msg.author_id)} (redacted by Blackline)",
                icon_emoji=":black_square:",
            )
            self.queue.set_stage(job, "reposted")
        if job.stage != "notified":
            entities = ", ".join(sorted({f.entity for f in decision.findings}))
            n = len(decision.related_ids)
            across = f" across {n} messages" if n else ""
            try:
                self.bot.chat_postEphemeral(
                    channel=msg.channel_id,
                    user=msg.author_id,
                    text=f"Blackline: {decision.action}{across}. {decision.message} ({entities})",
                )
            except SlackApiError as e:
                log.warning("ephemeral failed: %s", e.response.get("error"))
            self.queue.set_stage(job, "notified")

    def run_forever(self, partition: int, stop: threading.Event) -> None:
        while not stop.is_set():
            job = self.queue.claim(partition, wait=1.0)
            if job is None:
                continue
            try:
                self.handle(job)
                self.queue.done(job)
                ev = job.payload["event"]
                self.queue.advance_cursor(ev["channel"], ev["ts"])
            except Exception as e:
                status = self.queue.fail(job, repr(e))
                log.exception(
                    "job %s failed (attempt %d, now %s)", job.dedupe, job.attempts, status
                )

    # ---- restart recovery ------------------------------------------------------------

    def catch_up(self, max_age_s: float = 180, max_messages: int = 1000) -> int:
        """Queue anything posted while the bot was down, and rebuild each author's recent
        window from messages that are still visible."""
        now = time.time()
        missed = 0
        for channel, last_ts in self.queue.cursors():
            oldest = min(float(last_ts), now - max_age_s)
            messages: list[dict] = []
            cursor = None
            try:
                while len(messages) < max_messages:
                    r = self.bot.conversations_history(
                        channel=channel, oldest=f"{oldest:.6f}", limit=200, cursor=cursor
                    )
                    messages += r.get("messages", [])
                    cursor = (r.get("response_metadata") or {}).get("next_cursor")
                    if not cursor:
                        break
            except SlackApiError as e:
                log.warning("catch-up %s failed: %s", channel, e.response.get("error"))
                continue
            for m in sorted(messages, key=lambda m: float(m["ts"])):
                ev = human_event({**m, "channel": channel})
                if ev is None:
                    continue
                if float(ev["ts"]) > float(last_ts):
                    missed += self.enqueue(ev)
                else:
                    self.window.add(self.to_message(ev, lookup=False), now=float(ev["ts"]))
        return missed
