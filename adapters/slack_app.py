"""Slack adapter. Socket Mode, always on. Converts events to Message, applies Decision."""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from blackline import pipeline
from blackline.actions import SPLIT_NOTICE, mask
from blackline.contract import Decision, Message
from blackline.window import ConversationWindow

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("blackline")

app = App(token=os.environ["SLACK_BOT_TOKEN"])
user_client = WebClient(token=os.environ["SLACK_USER_TOKEN"])
window = ConversationWindow()

_channel_cache: dict[str, dict] = {}


def channel_info(client: WebClient, channel_id: str) -> dict:
    if channel_id not in _channel_cache:
        try:
            info = client.conversations_info(channel=channel_id)["channel"]
            members = client.conversations_members(channel=channel_id)["members"]
            guests = any(
                client.users_info(user=u)["user"].get("is_restricted") for u in members[:50]
            )
            _channel_cache[channel_id] = {"name": info.get("name", ""), "guests": guests}
        except SlackApiError:
            _channel_cache[channel_id] = {"name": "", "guests": False}
    return _channel_cache[channel_id]


def to_message(event: dict, client: WebClient) -> Message:
    ch = channel_info(client, event["channel"])
    return Message(
        id=f"{event['channel']}.{event['ts']}",
        channel_id=event["channel"],
        channel_name=ch["name"],
        channel_has_guests=ch["guests"],
        author_id=event["user"],
        text=event.get("text", ""),
    )


def ts_of(message_id: str) -> str:
    return message_id.split(".", 1)[1]


def delete(channel: str, message_ids: list[str]) -> bool:
    ok = True
    for mid in message_ids:
        try:
            user_client.chat_delete(channel=channel, ts=ts_of(mid))
        except SlackApiError as e:
            log.warning("delete %s failed: %s", mid, e.response.get("error"))
            ok = False
    return ok


def author_name(client: WebClient, user: str) -> str:
    try:
        return client.users_info(user=user)["user"].get("real_name") or "someone"
    except SlackApiError:
        return "someone"


def apply(decision: Decision, msg: Message, client: WebClient) -> None:
    removing = decision.action in ("mask", "block", "quarantine")
    targets = decision.related_ids or [msg.id]

    if removing and not delete(msg.channel_id, targets):
        decision.action = "warn"
        removing = False

    if removing:
        window.forget(targets)
    if decision.action == "mask":
        if decision.related_ids:
            text = SPLIT_NOTICE.format(n=len(decision.related_ids))
        else:
            # mask only what the policy acted on, never the author's own data
            hit = [f for f in decision.findings if f.subject != "self"]
            text = mask(msg.text, hit)
        client.chat_postMessage(
            channel=msg.channel_id,
            text=text,
            username=f"{author_name(client, msg.author_id)} (redacted by Blackline)",
            icon_emoji=":black_square:",
        )

    entities = ", ".join(sorted({f.entity for f in decision.findings}))
    across = f" across {len(decision.related_ids)} messages" if decision.related_ids else ""
    client.chat_postEphemeral(
        channel=msg.channel_id,
        user=msg.author_id,
        text=f"Blackline: {decision.action}{across}. {decision.message} ({entities})",
    )


@app.event("message")
def on_message(event: dict, client: WebClient) -> None:
    subtype = event.get("subtype")
    if subtype == "message_changed":
        inner = event["message"]
        if inner.get("bot_id"):
            return
        event = {**inner, "channel": event["channel"]}
    elif subtype or event.get("bot_id"):
        return

    msg = to_message(event, client)
    earlier = window.recent(msg)
    decision = pipeline.run(msg, window=earlier)
    log.info(
        "%s %s %s related=%d %s",
        msg.id,
        decision.action,
        decision.rule_id,
        len(decision.related_ids),
        decision.timing_ms,
    )
    if decision.action in ("allow", "log"):
        window.add(msg)
        return
    if decision.action == "warn":
        window.add(msg)
    apply(decision, msg, client)


if __name__ == "__main__":
    log.info("Blackline listening via Socket Mode")
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()
