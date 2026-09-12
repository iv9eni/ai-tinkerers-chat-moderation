import pytest

from blackline.actions import mask
from blackline.contract import Message
from blackline.detectors import tier0
from blackline.policy import Policy

policy = Policy.load("policies/default.yaml")


def ents(text):
    return [f.entity for f in tier0.detect_all(text)]


def test_the_missed_message_is_card_like():
    assert ents("1234 xxx 2345 vv 6787 nn 5678") == ["CARD_LIKE"]


def test_valid_card_with_filler_is_a_card():
    assert ents("4111 xxx 1111 vv 1111 nn 1111") == ["CREDIT_CARD"]


def test_filler_with_emoji_digits():
    assert ents(":four::one::one::one: xx :one::one::one::one: yy 1111 zz 1111") == ["CREDIT_CARD"]


def test_card_shaped_with_card_word():
    assert ents("my card is 1234 5678 9012 3456") == ["CARD_LIKE"]


def test_bare_card_shaped_message_is_card_like():
    assert ents("1234 5235 2547 5235") == ["CARD_LIKE"]


@pytest.mark.parametrize(
    "text",
    [
        "invoices 1001 1002 1003 1004 are paid",  # card shape, no card word, no filler
        "meeting 1030 room 4412 floor 2",
        "call 519 555 0142 about the claim",
        "we moved from 2019 to 2024 and then to 2025",
    ],
)
def test_ordinary_numbers_are_not_cards(text):
    assert ents(text) == []


def test_mask_covers_the_whole_run_including_filler():
    text = "ok 1234 xxx 2345 vv 6787 nn 5678 thanks"
    [f] = tier0.detect_all(text)
    run = "1234 xxx 2345 vv 6787 nn 5678"
    assert mask(text, [f]) == "ok " + "\u2588" * len(run) + " thanks"


def test_policy_masks_card_like():
    msg = Message(id="1", channel_id="C1", author_id="U1", text="x")
    d = policy.decide(msg, tier0.detect_all("1234 xxx 2345 vv 6787 nn 5678"))
    assert d.action == "mask" and d.rule_id == "pci.card.unverified"
