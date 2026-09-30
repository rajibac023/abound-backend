from datetime import date

from django.db.models import Q

from pricing.engine import Discount
from pricing.models import RentalDiscount
from vehicles.models import Vehicle


def eligible_discounts(
    *, vehicle: Vehicle, pickup_date: date, return_date: date, days: int
) -> tuple[Discount, ...]:
    # A single query provides coherent rule values. Rule edits affect future quotes only.
    rules = RentalDiscount.objects.filter(
        is_active=True, currency=vehicle.currency, minimum_days__lte=days
    ).filter(
        Q(vehicle__isnull=True) | Q(vehicle=vehicle),
        Q(starts_on__isnull=True) | Q(starts_on__lte=pickup_date),
        Q(ends_on__isnull=True) | Q(ends_on__gte=return_date),
    )
    return tuple(
        Discount(rule.uuid, rule.label, rule.kind, rule.value, rule.currency) for rule in rules
    )
