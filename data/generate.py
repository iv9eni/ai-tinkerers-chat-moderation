"""Synthetic labeled Slack messages. Never use real data."""

from __future__ import annotations

import json
import random
import sys

from faker import Faker

random.seed(7)
fake = Faker("en_CA")
Faker.seed(7)


def luhn_complete(prefix: str) -> str:
    total = 0
    for i, ch in enumerate(reversed(prefix)):
        n = int(ch)
        if i % 2 == 0:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return prefix + str((10 - total % 10) % 10)


def card() -> str:
    bin_ = random.choice(["4532", "5412", "3714", "6011"])
    body_len = 10 if bin_ == "3714" else 11
    return luhn_complete(bin_ + "".join(str(random.randint(0, 9)) for _ in range(body_len)))


def sin() -> str:
    return luhn_complete(
        random.choice("1234567") + "".join(str(random.randint(0, 9)) for _ in range(7))
    )


def bad_luhn(n: str) -> str:
    return n[:-1] + str((int(n[-1]) + 1) % 10)


def fmt_sin(n: str) -> str:
    return random.choice([n, f"{n[:3]} {n[3:6]} {n[6:]}", f"{n[:3]}-{n[3:6]}-{n[6:]}"])


def fmt(n: str) -> str:
    return random.choice(
        [
            n,
            " ".join(n[i : i + 4] for i in range(0, len(n), 4)),
            "-".join(n[i : i + 3] for i in range(0, len(n), 3)),
        ]
    )


TEMPLATES = {
    "CREDIT_CARD": [
        "customer card is {v} exp 09/27",
        "charge {v} for the refund pls",
        "cc {v} declined again",
    ],
    "CA_SIN": ["her SIN is {v} for the T4", "new hire SIN {v}", "payroll needs {v} by friday"],
    "API_KEY": ["use sk-or-{v} for the router", "prod key AKIA{v2}", "token ghp_{v3}"],
    "PHONE_3P": ["call {name} at {v} about the invoice", "{name}'s cell is {v}"],
    "ADDRESS_3P": ["{name} lives at {v}, ship it there", "send the gift to {v} ({name})"],
    "EMAIL_SELF": ["ping me at {v} if it breaks", "my email is {v}"],
    "NEGATIVE": [
        "order #{v} shipped",
        "tracking {v} says delivered",
        "lunch?",
        "PR is up, can someone review",
        "call the office at 416-555-0199 if the door is locked",
        "Justin Trudeau was on the news again",
        "standup moved to 10:15",
        "the build is red, looking now",
    ],
}
WEIGHTS = {
    "CREDIT_CARD": 8,
    "CA_SIN": 6,
    "API_KEY": 5,
    "PHONE_3P": 6,
    "ADDRESS_3P": 5,
    "EMAIL_SELF": 5,
    "NEGATIVE": 65,
}
CHANNELS = ["support", "sales", "recruiting", "eng", "hr", "random"]


def make(i: int) -> dict:
    kind = random.choices(list(WEIGHTS), weights=list(WEIGHTS.values()))[0]
    name = fake.name()
    entity, subject = None, None
    if kind == "CREDIT_CARD":
        v, entity, subject = fmt(card()), "CREDIT_CARD", "third_party"
    elif kind == "CA_SIN":
        v, entity, subject = fmt_sin(sin()), "CA_SIN", "third_party"
    elif kind == "API_KEY":
        v, entity, subject = fake.pystr(min_chars=32, max_chars=40), "API_KEY", "third_party"
    elif kind == "PHONE_3P":
        v, entity, subject = fake.phone_number(), "PHONE", "third_party"
    elif kind == "ADDRESS_3P":
        v, entity, subject = fake.street_address() + ", " + fake.city(), "ADDRESS", "third_party"
    elif kind == "EMAIL_SELF":
        v, entity, subject = fake.email(), "EMAIL", "self"
    else:
        v = bad_luhn(card())
    tmpl = random.choice(TEMPLATES[kind])
    text = tmpl.format(
        v=v,
        v2=fake.pystr(min_chars=16, max_chars=16).upper(),
        v3=fake.pystr(min_chars=36, max_chars=36),
        name=name,
    )
    span = None
    if entity:
        j = text.find(v)
        span = [j, j + len(v)] if j >= 0 else None
    return {
        "ts": f"{1725000000 + i}.000100",
        "user": fake.user_name(),
        "channel": random.choice(CHANNELS),
        "text": text,
        "label": {"entity": entity, "span": span, "subject": subject},
    }


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
    rows = [make(i) for i in range(n)]
    with open("data/demo_messages.json", "w") as fh:
        json.dump(rows[:-100], fh, indent=1)
    with open("data/holdout.json", "w") as fh:
        json.dump(rows[-100:], fh, indent=1)
    print(f"wrote {len(rows) - 100} demo + 100 holdout")
