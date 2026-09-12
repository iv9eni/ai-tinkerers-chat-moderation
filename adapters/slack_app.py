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
from blackline.actions import mask
from blackline.contract import Message

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("blackline")

app = App(token=os.environ["SLACK_BOT_TOKEN"])
user_client = WebClient(token=os.environ["SLACK_USER_TOKEN"])

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
    decision = pipeline.run(msg)
    log.info("%s %s %s %s", msg.id, decision.action, decision.rule_id, decision.timing_ms)
    if decision.action in ("allow", "log"):
        return

    if decision.action in ("mask", "block", "quarantine"):
        try:
            user_client.chat_delete(channel=msg.channel_id, ts=event["ts"])
        except SlackApiError as e:
            log.warning("delete failed: %s (falling back to warn)", e.response.get("error"))
            decision.action = "warn"

    if decision.action == "mask":
        name = client.users_info(user=msg.author_id)["user"].get("real_name", "someone")
        client.chat_postMessage(
            channel=msg.channel_id,
            text=mask(msg.text, decision.findings),
            username=f"{name} (redacted by Blackline)",
            icon_emoji=":black_square:",
        )

    entities = ", ".join(sorted({f.entity for f in decision.findings}))
    client.chat_postEphemeral(
        channel=msg.channel_id,
        user=msg.author_id,
        text=f"Blackline: {decision.action}. {decision.message} ({entities})",
    )


if __name__ == "__main__":
    log.info("Blackline listening via Socket Mode")
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()
