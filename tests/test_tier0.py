from blackline.detectors import tier0


def ents(text):
    return [f.entity for f in tier0.detect(text)]


def test_card_luhn_valid():
    assert ents("card is 4532 0151 1283 0366 exp 09/27") == ["CREDIT_CARD"]


def test_card_luhn_invalid_is_not_flagged():
    assert ents("order #4532015112830367 shipped") == []


def test_sin():
    assert ents("her SIN is 130 692 544") == ["CA_SIN"]


def test_sin_unissued_prefix_rejected():
    assert "CA_SIN" not in ents("ref 830692547")


def test_api_key():
    assert ents("use sk-or-v1-abcdefghijklmnopqrstuvwxyz0123456789") == ["API_KEY"]


def test_clean():
    assert ents("standup moved to 10:15, PR is up") == []


def test_tier0_is_fast():
    import time

    t = time.perf_counter()
    for _ in range(200):
        tier0.detect("call the office at 416-555-0199, card 4532 0151 1283 0366, SIN 130 692 544")
    assert (time.perf_counter() - t) / 200 < 0.005
