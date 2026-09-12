"""Undo common tricks people use to slip numbers past a filter.

Used for detection only. The original text is what gets masked or removed.
"""

from __future__ import annotations

import re
import unicodedata

# zero-width and joiner characters, variation selector, keycap combiner (4️⃣ -> 4)
_INVISIBLE = re.compile(r"[\u200b-\u200f\u2060\ufeff\ufe0f\u20e3]")
_WORDS = {
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
}
_WORD_RX = re.compile(r"\b(" + "|".join(_WORDS) + r")\b", re.IGNORECASE)
# digits separated only by spaces, dots, dashes, slashes, or underscores belong together
_SEP_BETWEEN_DIGITS = re.compile(r"(?<=\d)[\s.\-_/]+(?=\d)")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)  # fullwidth ４５ -> 45
    text = _INVISIBLE.sub("", text)
    text = _WORD_RX.sub(lambda m: _WORDS[m.group(1).lower()], text)
    return _SEP_BETWEEN_DIGITS.sub("", text)


def digit_groups(text: str) -> list[str]:
    return re.findall(r"\d+", normalize(text))
