"""Two-lane pipeline for Slack messages. Delete first, think later.

triage lane  ordered per author, rules only, no model
    rule hit        delete now, notify, done
    hold channel    delete every message now, then send it to the deep lane
    otherwise       send to the deep lane
deep lane    any order, model checks, may take a second
    hit             delete (or keep a held message deleted), post a masked copy or notice
    clean and held  release: repost the message under the author's name
    clean           nothing

A slow model call never delays a delete, because the deep lane has its own workers.
Every side effect is recorded as a stage on the job, so a retry after a crash never
deletes, posts, or releases twice.
"""

from __future__ import annotations

import logging
import threading
import time

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from adapters.redaction_restore import RestoreStore, manager_ids
from blackline import audit, pipeline
from blackline.actions import DISGUISED_NOTICE, SPLIT_NOTICE, mask
from blackline.contract import Decision, Message
from blackline.jobqueue import Job, JobQueue
from blackline.window import ConversationWindow

log = logging.getLogger("blackline")
REMOVING = pipeline.REMOVING
TRIAGE, DEEP = "triage", "deep"
STAGES = ["", "held", "deleted", "released", "reposted", "notified"]


def reached(job: Job, stage: str) -> bool:
    return STAGES.index(job.stage or "") >= STAGES.index(stage)


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


def posted_at(ev: dict) -> float:
    return float((ev.get("edited") or {}).get("ts") or ev["ts"])


def ms_since(t: float) -> int:
    return int((time.time() - t) * 1000)


class Worker:
    def __init__(
        self,
        bot: WebClient,
        user: WebClient,
        queue: JobQueue,
        window: ConversationWindow | None = None,
        restores: RestoreStore | None = None,
    ):
        self.bot = bot
        self.user = user
        self.queue = queue
        self.window = window or ConversationWindow()
        self.restores = restores or RestoreStore()
        self._names: dict[str, str] = {}
        self._guests: dict[str, bool] = {}
        self._people: dict[str, dict] = {}

    # ---- intake (runs inside the Slack listener, must be fast) ----------------------

    def enqueue(self, event: dict, received_at: float | None = None) -> bool:
        ev = human_event(event)
        if ev is None:
            return False
        payload = {"event": ev, "received_at": received_at or time.time()}
        return self.queue.put(
            dedupe_key(ev), payload, partition_key=f"{ev['channel']}:{ev['user']}", topic=TRIAGE
        )

    # ---- Slack lookups, cached ------------------------------------------------------

    def channel_name(self, cid: str) -> str:
        if cid not in self._names:
            try:
                self._names[cid] = self.bot.conversations_info(channel=cid)["channel"].get(
                    "name", ""
                )
            except SlackApiError:
                self._names[cid] = ""
        return self._names[cid]

    def channel_guests(self, cid: str) -> bool:
        if cid not in self._guests:
            try:
                members = self.bot.conversations_members(channel=cid)["members"]
                self._guests[cid] = any(self.person(u).get("is_restricted") for u in members[:50])
            except SlackApiError:
                self._guests[cid] = False
        return self._guests[cid]

    def person(self, uid: str) -> dict:
        if uid not in self._people:
            try:
                self._people[uid] = self.bot.users_info(user=uid)["user"]
            except SlackApiError:
                self._people[uid] = {}
        return self._people[uid]

    def display_name(self, uid: str) -> str:
        p = self.person(uid)
        return (p.get("profile") or {}).get("display_name") or p.get("real_name") or "someone"

    @staticmethod
    def to_message(ev: dict, name: str = "", guests: bool = False) -> Message:
        return Message(
            id=f"{ev['channel']}.{ev['ts']}",
            channel_id=ev["channel"],
            channel_name=name,
            channel_has_guests=guests,
            author_id=ev["user"],
            text=ev.get("text", ""),
        )

    # ---- triage lane ----------------------------------------------------------------

    def triage(self, job: Job) -> Decision | None:
        ev = job.payload["event"]
        cid = ev["channel"]
        started = time.time()
        msg = self.to_message(ev, name=self._names.get(cid, ""))  # no network before a delete
        earlier = self.window.recent(msg)

        t = time.perf_counter()
        decision = pipeline.fast_check(msg, earlier)
        if decision is not None:
            decision.timing_ms["decide"] = int((time.perf_counter() - t) * 1000)
            decision.timing_ms["queue"] = int((started - job.payload["received_at"]) * 1000)
            self.remove(job, decision, msg, ev)
            if decision.action == "warn":
                self.window.add(msg)
            self.notify(job, decision, msg, ev)
            audit.record(msg, decision)
            self._log("triage", msg, decision)
            return decision

        msg = msg.model_copy(update={"channel_name": self.channel_name(cid)})
        hold = bool(pipeline.policy().channel_cfg(msg).get("hold"))
        exposed = None
        if hold and not reached(job, "held"):
            if self.delete(cid, [msg.id]):
                exposed = ms_since(posted_at(ev))
                self.queue.set_stage(job, "held")
            else:
                hold = False  # cannot hold without delete rights, check it normally instead
        self.window.add(msg)
        self.queue.put(
            f"deep:{job.dedupe}",
            {
                "event": ev,
                "received_at": job.payload["received_at"],
                "window": [{"id": m.id, "text": m.text} for m in earlier],
                "held": hold,
                "exposed": exposed,
            },
            partition_key=job.dedupe,
            topic=DEEP,
        )
        return None

    # ---- deep lane ------------------------------------------------------------------

    def deep(self, job: Job) -> Decision:
        p = job.payload
        ev = p["event"]
        cid = ev["channel"]
        started = time.time()
        msg = self.to_message(ev, self.channel_name(cid), self.channel_guests(cid))
        chain = [
            Message(id=w["id"], channel_id=cid, author_id=ev["user"], text=w["text"])
            for w in p["window"]
        ]

        t = time.perf_counter()
        decision = pipeline.run(msg, window=chain, audit_log=False)
        decision.timing_ms["decide"] = int((time.perf_counter() - t) * 1000)
        decision.timing_ms["queue"] = int((started - p["received_at"]) * 1000)
        held = p["held"]

        if decision.action in REMOVING:
            if held:
                decision.timing_ms["exposed"] = p["exposed"]
                if not reached(job, "deleted"):
                    others = [i for i in decision.related_ids if i != msg.id]
                    self.delete(cid, others)
                    self.queue.set_stage(job, "deleted")
                self.window.forget(decision.related_ids or [msg.id])
            else:
                self.remove(job, decision, msg, ev)
        elif held:
            decision.timing_ms["exposed"] = p["exposed"]
            self.release(job, msg, ev)

        if decision.action not in ("allow", "log"):
            self.notify(job, decision, msg, ev)
        audit.record(msg, decision)
        self._log("deep", msg, decision, held=held)
        return decision

    # ---- side effects ---------------------------------------------------------------

    def remove(self, job: Job, decision: Decision, msg: Message, ev: dict) -> None:
        targets = decision.related_ids or [msg.id]
        if not reached(job, "deleted"):
            if not self.delete(msg.channel_id, targets):
                decision.action = "warn"  # could not remove, fall back to telling the author
                return
            decision.timing_ms["exposed"] = ms_since(posted_at(ev))
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

    def _thread(self, ev: dict) -> dict:
        ts = ev.get("thread_ts")
        return {"thread_ts": ts} if ts and ts != ev["ts"] else {}

    def release(self, job: Job, msg: Message, ev: dict) -> None:
        """A held message passed every check: put it back under the author's name."""
        if reached(job, "released"):
            return
        icon = (self.person(msg.author_id).get("profile") or {}).get("image_72")
        self.bot.chat_postMessage(
            channel=msg.channel_id,
            text=msg.text,
            username=f"{self.display_name(msg.author_id)} (via Blackline)",
            **({"icon_url": icon} if icon else {"icon_emoji": ":speech_balloon:"}),
            **self._thread(ev),
        )
        self.queue.set_stage(job, "released")

    def notify(self, job: Job, decision: Decision, msg: Message, ev: dict) -> None:
        if decision.action == "mask" and reached(job, "deleted") and not reached(job, "reposted"):
            restore_id = ""
            if decision.related_ids:
                text = SPLIT_NOTICE.format(n=len(decision.related_ids))
            elif any(f.note == "obfuscated" for f in decision.findings):
                text = DISGUISED_NOTICE
            else:
                text = mask(msg.text, [f for f in decision.findings if f.subject != "self"])
                if manager_ids():
                    restore_id = self.restores.create(
                        msg.channel_id,
                        msg.author_id,
                        msg.text,
                        thread_ts=ev.get("thread_ts", ""),
                    )
            response = self.bot.chat_postMessage(**self._redacted_post(msg, ev, text, restore_id))
            if restore_id:
                self.restores.attach_redacted(restore_id, response.get("ts", ""))
            self.queue.set_stage(job, "reposted")
        if not reached(job, "notified"):
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

    def _redacted_post(self, msg: Message, ev: dict, text: str, restore_id: str) -> dict:
        post = {
            "channel": msg.channel_id,
            "text": text,
            "username": f"{self.display_name(msg.author_id)} (redacted by Blackline)",
            "icon_emoji": ":black_square:",
            **self._thread(ev),
        }
        if restore_id:
            post["blocks"] = [
                {"type": "section", "text": {"type": "mrkdwn", "text": text}},
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Restore original"},
                            "action_id": "blackline_restore",
                            "value": restore_id,
                            "confirm": {
                                "title": {"type": "plain_text", "text": "Restore message?"},
                                "text": {
                                    "type": "mrkdwn",
                                    "text": "Only restore false positives. The original text will be visible in this channel again.",
                                },
                                "confirm": {"type": "plain_text", "text": "Restore"},
                                "deny": {"type": "plain_text", "text": "Cancel"},
                            },
                        }
                    ],
                },
            ]
        return post

    def restore_redaction(self, restore_id: str, actor_id: str) -> tuple[bool, str]:
        if actor_id not in manager_ids():
            return False, "Only configured Blackline managers can restore redacted messages."
        ticket = self.restores.consume(restore_id)
        if ticket is None:
            return False, "That restore link expired or was already used."
        if ticket.redacted_ts:
            try:
                self.bot.chat_delete(channel=ticket.channel_id, ts=ticket.redacted_ts)
            except SlackApiError as e:
                log.warning("delete redacted copy failed: %s", e.response.get("error"))
        icon = (self.person(ticket.author_id).get("profile") or {}).get("image_72")
        self.bot.chat_postMessage(
            channel=ticket.channel_id,
            text=ticket.text,
            username=f"{self.display_name(ticket.author_id)} (restored by Blackline)",
            **({"icon_url": icon} if icon else {"icon_emoji": ":speech_balloon:"}),
            **({"thread_ts": ticket.thread_ts} if ticket.thread_ts else {}),
        )
        return True, "Restored the original message."

    def _log(self, lane: str, msg: Message, d: Decision, held: bool = False) -> None:
        log.info(
            "%s lane=%s held=%s %s %s related=%d window=%d %s",
            msg.id,
            lane,
            held,
            d.action,
            d.rule_id or "-",
            len(d.related_ids),
            len(self.window.recent(msg)),
            {k: v for k, v in d.timing_ms.items() if v is not None},
        )

    # ---- workers --------------------------------------------------------------------

    def run_forever(self, topic: str, partition: int, stop: threading.Event) -> None:
        handler = self.triage if topic == TRIAGE else self.deep
        while not stop.is_set():
            job = self.queue.claim(partition, wait=1.0, topic=topic)
            if job is None:
                continue
            try:
                handler(job)
                self.queue.done(job)
                if topic == TRIAGE:
                    ev = job.payload["event"]
                    self.queue.advance_cursor(ev["channel"], ev["ts"])
            except Exception as e:
                status = self.queue.fail(job, repr(e))
                log.exception(
                    "%s job %s failed (attempt %d, now %s)", topic, job.dedupe, job.attempts, status
                )

    # ---- restart recovery -----------------------------------------------------------

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
                    self.window.add(self.to_message(ev), now=float(ev["ts"]))
        return missed
