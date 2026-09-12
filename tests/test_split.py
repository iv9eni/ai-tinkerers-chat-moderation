from blackline.contract import Message
from blackline.detectors import split
from blackline.detectors.tier1 import _verify
from blackline.normalize import normalize
from blackline.window import ConversationWindow


def test_normalize_tricks():
    assert normalize("４５３２") == "4532"
    assert normalize("45\u200b32") == "4532"
    assert normalize("four five three two") == "4532"
    assert normalize("4️⃣5️⃣") == "45"
    assert normalize("4532 0151.1283-0366") == "4532015112830366"
    assert normalize("standup at 10:15") == "standup at 10:15"


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
