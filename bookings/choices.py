from django.db import models


class BookingStatus(models.TextChoices):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


# Statuses that still reserve the vehicle for its dates.
BLOCKING_STATUSES = (BookingStatus.PENDING, BookingStatus.CONFIRMED, BookingStatus.ACTIVE)

# Every valid status transition. Terminal states have no outgoing edges.
TRANSITIONS: dict[str, frozenset[str]] = {
    BookingStatus.PENDING: frozenset(
        {BookingStatus.CONFIRMED, BookingStatus.REJECTED, BookingStatus.CANCELLED}
    ),
    BookingStatus.CONFIRMED: frozenset({BookingStatus.ACTIVE, BookingStatus.CANCELLED}),
    BookingStatus.ACTIVE: frozenset({BookingStatus.COMPLETED}),
    BookingStatus.COMPLETED: frozenset(),
    BookingStatus.CANCELLED: frozenset(),
    BookingStatus.REJECTED: frozenset(),
}

# Transitions a customer may trigger on their own booking, without a domain permission.
CUSTOMER_ALLOWED_TARGETS = frozenset({BookingStatus.CANCELLED})
