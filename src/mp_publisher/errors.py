class PublisherError(RuntimeError):
    """Unsafe or invalid input that must stop publication."""


class SkipArticle(PublisherError):
    """Expected, safe absence or not-ready state."""


class StateConflict(PublisherError):
    """Local idempotency state conflicts with the current request."""


class UnknownDelivery(PublisherError):
    """A request may have reached a channel and must be reconciled manually."""
