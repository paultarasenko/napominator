"""Typed errors that cross the application boundary."""

from __future__ import annotations


class NapominatorError(Exception):
    """Base class for expected, user-presentable failures."""


class NotFoundError(NapominatorError):
    """The entity does not exist or does not belong to the caller (never reveal which)."""


class LimitExceededError(NapominatorError):
    def __init__(self, what: str, limit: int) -> None:
        super().__init__(f"{what} limit exceeded ({limit})")
        self.what = what
        self.limit = limit


class InvalidScheduleError(NapominatorError):
    """The requested schedule has no future occurrence or violates a domain rule."""


# --- delivery failures raised by a Notifier implementation ---


class DeliveryError(Exception):
    """Base class for failures while sending a notification."""


class RetryAfterError(DeliveryError):
    """Telegram asked us to slow down (HTTP 429)."""

    def __init__(self, seconds: float) -> None:
        super().__init__(f"retry after {seconds}s")
        self.seconds = seconds


class TransientDeliveryError(DeliveryError):
    """Network trouble or a 5xx: retry with backoff."""


class RecipientBlockedError(DeliveryError):
    """The user blocked the bot (or deleted the chat)."""


class PermanentDeliveryError(DeliveryError):
    """The message can never be delivered (bad request); do not retry."""


class DuplicateCategoryError(NapominatorError):
    """A category with this name already exists."""


class ActionExpiredError(NapominatorError):
    """The button was pressed too late or the state has already changed (idempotent no-op)."""
