from blackline.contract import Message
from blackline.detectors import split
from blackline.detectors.tier1 import _verify
from blackline.window import ConversationWindow


def test_card_split_across_two_messages():
    # the exact messages from the test channel
    texts = ["card first digits 4532 second digits 0151", "card second digits  1283 0366"]
    hit = split.detect(texts)
    assert hit and hit.entity == "CREDIT_CARD" and hit.message_indexes == [0, 1]


def test_card_split_across_four_messages():
    texts = ["ok the card", "4532", "0151", "1283 0366"]
    hit = split.detect(texts)
    assert hit and hit.message_indexes == [1, 2, 3]


def test_sin_split():
    texts = ["new hire SIN starts 130", "then 692 544"]
    hit = split.detect(texts)
    assert hit and hit.entity == "CA_SIN"


def test_unrelated_numbers_do_not_combine():
    texts = ["order 4532 shipped", "room 0151", "ticket 1283 0366 closed"]
    assert split.detect(texts) is None


def test_older_hit_without_current_message_is_ignored():
    texts = ["card 4532 0151", "1283 0366", "lunch?"]
    assert split.detect(texts) is None


def test_model_reconstruction_is_verified():
    texts = ["last four are 0366", "first twelve 4532 0151 1283"]
    assert _verify("CREDIT_CARD", "4532 0151 1283 0366", texts, [0, 1])
    # a digit the author never typed
    assert not _verify("CREDIT_CARD", "4532 0151 1283 0367", texts, [0, 1])
    # right digits, wrong checksum order
    assert not _verify("CREDIT_CARD", "0366 4532 0151 1283", texts, [0, 1])


def _m(i, author="U1", channel="C1"):
    return Message(id=f"{channel}.{i}", channel_id=channel, author_id=author, text=str(i))


def test_window_per_author_and_expiry():
    w = ConversationWindow(max_messages=3, max_age_s=60)
    w.add(_m(1), now=0)
    w.add(_m(2, author="U2"), now=1)
    w.add(_m(3), now=2)
    assert [m.id for m in w.recent(_m(9), now=10)] == ["C1.1", "C1.3"]
    assert [m.id for m in w.recent(_m(9), now=61)] == ["C1.3"]
    w.forget(["C1.3"])
    assert w.recent(_m(9), now=62) == []


def test_emoji_card_split_without_context_word():
    # the exact messages from the test channel: keycap emoji, no word like "card"
    texts = [
        ":four::one::one::one:",
        ":one::one::one::one:",
        ":one::one::one::one: :one::one::one::one:",
    ]
    hit = split.detect(texts)
    assert hit and hit.entity == "CREDIT_CARD" and hit.message_indexes == [0, 1, 2]


def test_bare_digit_messages_form_card():
    assert split.detect(["4532", "0151", "1283 0366"])


def test_bare_digits_alone_do_not_make_a_sin():
    assert split.detect(["130", "692 544"]) is None


def test_bare_digits_that_fail_luhn_do_not_combine():
    assert split.detect(["4532", "0151", "1283 0367"]) is None


def test_context_word_after_bare_digits():
    # the exact messages from the test channel: digits first, the card word last
    texts = ["4111 1111", "1111 1111", "credit card number above ^"]
    hit = split.detect(texts)
    assert hit and hit.entity == "CREDIT_CARD" and hit.message_indexes == [0, 1]


def test_context_word_alone_does_not_combine_unrelated_numbers():
    assert split.detect(["order 4532", "room 0151", "card?"]) is None
