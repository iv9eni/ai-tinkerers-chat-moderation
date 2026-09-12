"""Fold the many ways people write digits back into plain ASCII digits.

Used for detection only. The original text is what gets masked or removed.

Layers, applied in this order:
  1. emoji shortcodes         :four: :keycap_ten:          -> 4 10
  2. Unicode compatibility    ４ 𝟒 ⁴ ₄                     -> 4
  3. invisible characters     zero-width, bidi marks, keycap combiners, variation selectors
  4. digits from any script   ٤ ४ ৪ ๔ ④ ➍                  -> 4
  5. number words             four, quatre, cuatro, forty five, double one
  6. look-alike letters       4lll O5S1                    -> 4111 0551 (only in digit-like runs)
  7. separators               4*1_1~1 `1111` 4.1.1.1       -> joined
"""

from __future__ import annotations

import re
import unicodedata

# 1. Slack, GitHub, and Discord all use these names for the keycap emoji
SHORTCODES = {
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "keycap_ten": "10",
    "100": "100",
    "1234": "1234",
}
_SHORTCODE_RX = re.compile(r":(" + "|".join(SHORTCODES) + r")(?:::skin-tone-\d)?:")

# 5. number words. English, French, Spanish (Canada plus the most common second language)
UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "zéro": 0, "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4,
    "cinq": 5, "sept": 7, "huit": 8, "neuf": 9,
    "cero": 0, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4,
    "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9,
}  # fmt: skip
TEENS = {
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "dix": 10, "onze": 11, "douze": 12, "diez": 10, "once": 11, "doce": 12,
}  # fmt: skip
TENS = {
    "twenty": 2, "thirty": 3, "forty": 4, "fourty": 4, "fifty": 5,
    "sixty": 6, "seventy": 7, "eighty": 8, "ninety": 9,
}  # fmt: skip
REPEAT = {"double": 2, "triple": 3}

_U = "|".join(sorted(UNITS, key=len, reverse=True))
_TENS_RX = re.compile(rf"\b({'|'.join(TENS)})(?:[\s-]+({_U}))?\b", re.IGNORECASE)
_TEENS_RX = re.compile(rf"\b({'|'.join(TEENS)})\b", re.IGNORECASE)
_UNITS_RX = re.compile(rf"\b({_U})\b", re.IGNORECASE)
_REPEAT_RX = re.compile(rf"\b({'|'.join(REPEAT)})[\s-]+(\d)", re.IGNORECASE)

# 6. letters people swap in for digits. Lowercase s, i, z are left out: too common in words.
LEET = {"O": "0", "o": "0", "I": "1", "l": "1", "|": "1", "Z": "2", "S": "5", "B": "8"}

# 7. up to 3 non-alphanumeric characters between digits are separators
_SEPARATORS = re.compile(r"(?<=\d)[\W_]{1,3}(?=\d)")


def _fold_char(ch: str) -> str:
    if ch.isascii():
        return ch
    cat = unicodedata.category(ch)
    if cat in ("Cf", "Mn", "Me"):  # zero-width, bidi, variation selectors, keycap combiner
        return ""
    d = unicodedata.digit(ch, None)
    if d is not None:
        return str(d)
    n = unicodedata.numeric(ch, None)
    if n is not None and float(n).is_integer() and 0 <= n <= 20:  # ⑩ .. ⑳
        return str(int(n))
    return ch


def _words(text: str) -> str:
    def tens(m: re.Match) -> str:
        unit = UNITS[m.group(2).lower()] if m.group(2) else 0
        return f"{TENS[m.group(1).lower()]}{unit}"

    text = _TENS_RX.sub(tens, text)
    text = _TEENS_RX.sub(lambda m: str(TEENS[m.group(1).lower()]), text)
    text = _UNITS_RX.sub(lambda m: str(UNITS[m.group(1).lower()]), text)
    return _REPEAT_RX.sub(lambda m: m.group(2) * REPEAT[m.group(1).lower()], text)


def _leet(text: str) -> str:
    """Swap look-alike letters only in tokens made entirely of digits and look-alikes,
    and only when that token or its neighbour has a real digit."""
    parts = re.split(r"(\s+)", text)
    tokens = parts[::2]
    shape = [bool(t) and all(c.isdigit() or c in LEET for c in t) for t in tokens]
    has_digit = [s and any(c.isdigit() for c in t) for s, t in zip(shape, tokens, strict=True)]
    anchored = list(has_digit)
    for order in (range(len(tokens)), reversed(range(len(tokens)))):
        prev = False
        for i in order:
            anchored[i] = shape[i] and (anchored[i] or prev)
            prev = anchored[i]
    for i, t in enumerate(tokens):
        if anchored[i]:
            parts[2 * i] = "".join(LEET.get(c, c) for c in t)
    return "".join(parts)


def normalize(text: str) -> str:
    text = _SHORTCODE_RX.sub(lambda m: SHORTCODES[m.group(1)], text)
    text = unicodedata.normalize("NFKC", text)
    text = "".join(_fold_char(c) for c in text)
    text = _words(text)
    text = _leet(text)
    return _SEPARATORS.sub("", text)


def digit_groups(text: str) -> list[str]:
    return re.findall(r"\d+", normalize(text))


def disguised_digit_count(text: str) -> int:
    """How many digits only appear after folding. High means someone is hiding numbers."""
    return sum(c.isdigit() for c in normalize(text)) - sum(c in "0123456789" for c in text)
