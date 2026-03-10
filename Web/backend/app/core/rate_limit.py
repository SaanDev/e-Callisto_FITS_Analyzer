from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
import math
import time


@dataclass(frozen=True)
class RateLimitExceeded(Exception):
    retry_after_seconds: int


class RateLimiter:
    def __init__(self) -> None:
        self._buckets: dict[str, deque[float]] = defaultdict(deque)

    def hit(self, key: str, *, limit: int, window_seconds: int) -> None:
        now = time.monotonic()
        bucket = self._buckets[key]
        cutoff = now - float(window_seconds)
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= int(limit):
            retry_after = max(1, int(math.ceil(window_seconds - (now - bucket[0]))))
            raise RateLimitExceeded(retry_after_seconds=retry_after)
        bucket.append(now)

