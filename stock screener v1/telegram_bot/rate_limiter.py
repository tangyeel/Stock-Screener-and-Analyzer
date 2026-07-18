"""
Per-chat rate limiting — max N queries per rolling 60-second window.
"""

import time
from collections import defaultdict
from config import BOT_MAX_QUERIES_PER_MIN


class RateLimiter:
    def __init__(self, max_queries: int = BOT_MAX_QUERIES_PER_MIN, window_sec: int = 60):
        self.max_queries = max_queries
        self.window_sec = window_sec
        self._buckets: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, chat_id: str) -> bool:
        now = time.time()
        cutoff = now - self.window_sec
        timestamps = self._buckets[chat_id]
        timestamps[:] = [t for t in timestamps if t > cutoff]
        if len(timestamps) >= self.max_queries:
            return False
        timestamps.append(now)
        return True

    def remaining(self, chat_id: str) -> int:
        now = time.time()
        cutoff = now - self.window_sec
        timestamps = self._buckets[chat_id]
        active = sum(1 for t in timestamps if t > cutoff)
        return max(0, self.max_queries - active)
