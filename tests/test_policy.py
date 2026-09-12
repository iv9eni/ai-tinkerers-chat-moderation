from blackline.actions import mask
from blackline.contract import Finding, Message
from blackline.policy import Policy

policy = Policy.load("policies/default.yaml")


def msg(channel="eng", guests=False):
    return Message(
        id="1",
        channel_id="C1",
        channel_name=channel,
        author_id="U1",
        text="x",
        channel_has_guests=guests,
    )


def test_card_blocks():
    f = Finding(entity="CREDIT_CARD", start=0, end=16, confidence=0.97, tier=0)
    assert policy.decide(msg(), [f]).action == "block"


def test_card_and_sin_blocks():
    fs = [
        Finding(entity="CREDIT_CARD", start=0, end=16, confidence=0.97, tier=0),
        Finding(entity="CA_SIN", start=20, end=29, confidence=0.9, tier=0),
    ]
    d = policy.decide(msg(), fs)
    assert d.action == "block"


def test_self_email_allowed():
    f = Finding(entity="EMAIL", start=0, end=5, confidence=0.95, subject="self", tier=1)
    assert policy.decide(msg(), [f]).action == "allow"


def test_third_party_phone_warns_but_logs_in_recruiting():
    f = Finding(entity="PHONE", start=0, end=5, confidence=0.95, subject="third_party", tier=1)
    assert policy.decide(msg("eng"), [f]).action == "warn"
    assert policy.decide(msg("recruiting"), [f]).action == "log"


def test_third_party_email_masks_but_logs_in_recruiting():
    f = Finding(entity="EMAIL", start=0, end=15, confidence=0.95, subject="third_party", tier=1)
    assert policy.decide(msg("eng"), [f]).action == "mask"
    assert policy.decide(msg("recruiting"), [f]).action == "log"


def test_low_confidence_ignored():
    f = Finding(entity="ADDRESS", start=0, end=5, confidence=0.5, subject="third_party", tier=1)
    assert policy.decide(msg(), [f]).action == "allow"


def test_mask_hides_every_card_digit():
    text = "card 4532015112830366 ok"
    f = Finding(entity="CREDIT_CARD", start=5, end=21, confidence=0.97, tier=0)
    assert mask(text, [f]) == "card " + "\u2588" * 16 + " ok"
