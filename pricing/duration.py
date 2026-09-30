from datetime import date, datetime

from django.conf import settings
from rest_framework.exceptions import ValidationError

WEEK_DAYS = 7
MONTH_DAYS = 30
POLICY_VERSION = "calendar-exact-bundles-v1"


def rental_days(pickup_date: date, return_date: date) -> int:
    """Calendar dates: pickup inclusive, return exclusive. No implicit time conversion."""
    for field, value in (("pickup_date", pickup_date), ("return_date", return_date)):
        if not isinstance(value, date) or isinstance(value, datetime):
            raise ValidationError({field: ["Supply a calendar date, not a timestamp."]})
    days = (return_date - pickup_date).days
    if days <= 0:
        raise ValidationError({"return_date": ["Return date must be after pickup date."]})
    if days > settings.PRICING_MAX_RENTAL_DAYS:
        raise ValidationError(
            {"return_date": [f"Rentals cannot exceed {settings.PRICING_MAX_RENTAL_DAYS} days."]}
        )
    return days
