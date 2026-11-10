from __future__ import annotations

import pytest

from napominator.application.ratelimit import RateLimiter, TokenBucket
from tests.support import FakeClock


def test_bucket_allows_a_burst_then_spaces_requests(clock: FakeClock) -> None:
    bucket = TokenBucket(clock, rate=2.0, capacity=3)
    assert [bucket.reserve() for _ in range(3)] == [0.0, 0.0, 0.0]
    assert bucket.reserve() == pytest.approx(0.5)
    assert bucket.reserve() == pytest.approx(1.0)  # queued behind the previous booking


def test_bucket_refills_over_time_but_never_beyond_capacity(clock: FakeClock) -> None:
    bucket = TokenBucket(clock, rate=1.0, capacity=2)
    bucket.reserve()
    bucket.reserve()
    clock.advance(seconds=100)
    assert [bucket.reserve(), bucket.reserve(), bucket.reserve()][:2] == [0.0, 0.0]
    assert bucket.reserve() > 0


def test_bucket_rejects_nonsense_parameters(clock: FakeClock) -> None:
    with pytest.raises(ValueError, match="positive"):
        TokenBucket(clock, rate=0, capacity=1)
    with pytest.raises(ValueError, match="capacity"):
        TokenBucket(clock, rate=1, capacity=0.5)


def test_limiter_combines_chat_and_global_buckets(clock: FakeClock) -> None:
    limiter = RateLimiter(clock, global_rate=2.0, chat_rate=1.0)
    assert limiter.reserve(1) == 0.0
    assert limiter.reserve(1) == pytest.approx(1.0)  # same chat: 1/s
    assert limiter.reserve(2) == pytest.approx(0.0, abs=0.5)  # other chat: only the global cap
    waits = [limiter.reserve(100 + i) for i in range(4)]
    assert waits == sorted(waits)
    assert waits[-1] > 0  # the global bucket eventually pushes back


def test_idle_chat_buckets_are_pruned(clock: FakeClock) -> None:
    limiter = RateLimiter(clock, global_rate=1_000_000.0, chat_rate=1.0)
    for chat in range(RateLimiter._PRUNE_AT + 5):
        limiter.reserve(chat)
    clock.advance(seconds=10)  # every per-chat bucket has refilled
    limiter.reserve(-1)
    assert len(limiter._chats) < 10
