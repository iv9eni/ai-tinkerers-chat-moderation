"""Short memory of each author's recent messages in each channel.

In process memory is enough for one bot instance. Swap for Redis or Firestore when
there is more than one worker.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from blackline.contract import Message

MAX_MESSAGES = 6
MAX_AGE_S = 180


class ConversationWindow:
    def __init__(self, max_messages: int = MAX_MESSAGES, max_age_s: float = MAX_AGE_S):
        self.max_messages = max_messages
        self.max_age_s = max_age_s
        self._items: dict[tuple[str, str], deque[tuple[float, Message]]] = defaultdict(deque)
        self._lock = threading.Lock()

    @staticmethod
    def _key(msg: Message) -> tuple[str, str]:
        return (msg.channel_id, msg.author_id)

    def recent(self, msg: Message, now: float | None = None) -> list[Message]:
        """Earlier messages from the same author in the same channel, oldest first."""
        now = now or time.time()
        with self._lock:
            q = self._items[self._key(msg)]
            while q and now - q[0][0] > self.max_age_s:
                q.popleft()
            return [m for _, m in q if m.id != msg.id]

    def add(self, msg: Message, now: float | None = None) -> None:
        now = now or time.time()
        with self._lock:
            q = self._items[self._key(msg)]
            for i, (t, m) in enumerate(q):
                if m.id == msg.id:  # an edit replaces the earlier text
                    q[i] = (t, msg)
                    return
            q.append((now, msg))
            while len(q) > self.max_messages:
                q.popleft()

    def forget(self, ids: list[str]) -> None:
        drop = set(ids)
        with self._lock:
            for key, q in self._items.items():
                self._items[key] = deque((t, m) for t, m in q if m.id not in drop)
