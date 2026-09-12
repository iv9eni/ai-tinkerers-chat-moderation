import pytest

from blackline.detectors import tier0
from blackline.normalize import normalize

CARD = "4111111111111111"  # Visa test number, Luhn valid

DISGUISES = {
    "slack shortcodes": ":four::one::one::one: :one::one::one::one: :one::one::one::one: :one::one::one::one:",
    "keycap unicode": "4️⃣1️⃣1️⃣1️⃣ " + "1️⃣" * 12,
    "fullwidth": "４１１１ １１１１ １１１１ １１１１",
    "arabic-indic": "٤١١١ ١١١١ ١١١١ ١١١١",
    "devanagari": "४१११ ११११ ११११ ११११",
    "math bold": "𝟒𝟏𝟏𝟏 𝟏𝟏𝟏𝟏 𝟏𝟏𝟏𝟏 𝟏𝟏𝟏𝟏",
    "circled": "④①①① ①①①① ①①①① ①①①①",
    "dingbat": "➍➊➊➊ ➊➊➊➊ ➊➊➊➊ ➊➊➊➊",
    "superscript": "⁴¹¹¹ ¹¹¹¹ ¹¹¹¹ ¹¹¹¹",
    "zero-width": "4\u200b111 1111‍ 1111 ⁠" + "1111",
    "markdown": "*4111* _1111_ ~1111~ `1111`",
    "punctuation": "4.1.1.1-1|1|1|1 1/1/1/1 1,1,1,1",
    "english words": "four one one one one one one one one one one one one one one one",
    "double triple": "four triple one double one" + " one" * 10,
    "french words": "quatre un un un un un un un un un un un un un un un",
    "spanish words": "cuatro uno uno uno uno uno uno uno uno uno uno uno uno uno uno uno",
    "leet": "4lll llll llll llll",
    "mixed": ":four: one 1 ① ➊ ١ l 𝟏 ¹ one one :one: 1 1 1 1",
}


@pytest.mark.parametrize("name", DISGUISES)
def test_every_disguise_folds_to_the_card(name):
    assert CARD in normalize(DISGUISES[name]).replace(" ", "")


@pytest.mark.parametrize("name", DISGUISES)
def test_every_disguise_is_caught_by_rules(name):
    found = tier0.detect_folded("card " + DISGUISES[name])
    assert [f.entity for f in found] == ["CREDIT_CARD"], name


def test_tens_words():
    assert normalize("forty-five thirty two") == "4532"


PROSE = [
    "I have one question about the two tickets from last week",
    "Oslo office opens at nine, bring the SOS kit",
    "une question sur les deux projets",
    "Is Bob OK? lol",
    "standup at 10:15 in room 4",
    "ship 4111 units by Friday",  # short digit run, no card
]


@pytest.mark.parametrize("text", PROSE)
def test_ordinary_prose_is_not_a_card(text):
    assert tier0.detect_folded(text) == []


def test_leet_needs_a_real_digit_nearby():
    assert normalize("SOS lol") == "SOS lol"
    assert normalize("4lll") == "4111"


def test_long_run_needs_card_context():
    run = ":four:" + ":one:" * 15 + ":two::three::four::five:"  # 20 digits
    assert tier0.detect_folded(run) == []
    assert [f.entity for f in tier0.detect_folded("my visa " + run)] == ["CREDIT_CARD"]


def test_codebook_substitution_then_rules():
    from blackline.detectors.tier1 import decode_with

    custom = ":num_four:" + ":num_one:" * 15
    decoded = normalize(decode_with("card " + custom, [(":num_four:", "4"), (":num_one:", "1")]))
    assert CARD in decoded
    german = "karte vier" + " eins" * 15
    assert CARD in normalize(decode_with(german, [("vier", "4"), ("eins", "1")]))


def test_encoded_gate():
    from blackline.detectors.split import looks_encoded

    assert looks_encoded("karte vier" + " eins" * 15)
    assert looks_encoded(":num_four:" + ":num_one:" * 15)
    assert not looks_encoded("we shipped four features and one fix in two sprints")
    assert not looks_encoded("can someone review the PR before the standup tomorrow morning please")
