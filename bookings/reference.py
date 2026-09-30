from datetime import date

from django.db import models
from django.utils import timezone

from bookings.models import BookingSequence


def next_booking_reference(*, as_of: date | None = None) -> str:
    """Caller must already hold a transaction; the row lock serializes the counter."""
    year = (as_of or timezone.localdate()).year
    sequence, _ = BookingSequence.objects.select_for_update().get_or_create(year=year)
    sequence.last_value = models.F("last_value") + 1
    sequence.save(update_fields=["last_value"])
    sequence.refresh_from_db(fields=["last_value"])
    return f"ABH-{year}-{sequence.last_value:06d}"
