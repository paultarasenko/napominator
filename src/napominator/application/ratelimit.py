"""Token buckets driven by the injected Clock (so tests never sleep)."""

from __future__ import annotations

from napominator.application.ports import Clock


class TokenBucket:
    """``rate`` tokens per second with a burst of ``capacity``.

    ``reserve()`` never blocks: it returns how long the caller must wait, and books the token
    so concurrent callers queue up behind each other.
    """

    def __init__(self, clock: Clock, rate: float, capacity: float) -> None:
        if rate <= 0 or capacity < 1:
            msg = "rate must be positive and capacity >= 1"
            raise ValueError(msg)
        self._clock = clock
        self._rate = rate
        self._capacity = capacity
        self._tokens = capacity
        self._stamp = clock.now()

    def reserve(self) -> float:
        now = self._clock.now()
        elapsed = max((now - self._stamp).total_seconds(), 0.0)
        self._stamp = now
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
        self._tokens -= 1
        return 0.0 if self._tokens >= 0 else -self._tokens / self._rate

    @property
    def idle(self) -> bool:
        """Full again: safe to forget (used to prune per-chat buckets)."""
        elapsed = max((self._clock.now() - self._stamp).total_seconds(), 0.0)
        return self._tokens + elapsed * self._rate >= self._capacity


class RateLimiter:
    """A global bucket plus one bucket per chat."""

    _PRUNE_AT = 2048

    def __init__(self, clock: Clock, global_rate: float = 25.0, chat_rate: float = 1.0) -> None:
        self._clock = clock
        self._chat_rate = chat_rate
        self._global = TokenBucket(clock, global_rate, global_rate)
        self._chats: dict[int, TokenBucket] = {}

    def reserve(self, chat_id: int) -> float:
        if len(self._chats) > self._PRUNE_AT:
            self._chats = {k: b for k, b in self._chats.items() if not b.idle}
        bucket = self._chats.get(chat_id)
        if bucket is None:
            bucket = self._chats[chat_id] = TokenBucket(self._clock, self._chat_rate, 1)
        return max(bucket.reserve(), self._global.reserve())
