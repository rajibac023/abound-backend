from datetime import date
from typing import Any
from uuid import UUID

from django.db import transaction
from rest_framework.exceptions import NotFound

from accounts.authorization import locked_actor
from accounts.models import User
from accounts.policy import Permission
from audit.services import record_pricing_event
from pricing.duration import rental_days
from pricing.engine import PriceQuote, RateCard, calculate_quote
from pricing.models import RentalDiscount
from pricing.selectors import eligible_discounts
from pricing.serializers import DiscountWriteSerializer, RateWriteSerializer
from vehicles.choices import VehicleStatus
from vehicles.models import Vehicle


def price_vehicle(*, vehicle: Vehicle, pickup_date: date, return_date: date) -> PriceQuote:
    """Internal entry point; booking callers pass a freshly loaded, locked vehicle."""
    days = rental_days(pickup_date, return_date)
    return calculate_quote(
        vehicle_uuid=vehicle.uuid,
        rates=RateCard(
            vehicle.daily_rate, vehicle.weekly_rate, vehicle.monthly_rate, vehicle.currency
        ),
        pickup_date=pickup_date,
        return_date=return_date,
        discounts=eligible_discounts(
            vehicle=vehicle, pickup_date=pickup_date, return_date=return_date, days=days
        ),
    )


def quote_vehicle(
    *, vehicle_uuid: UUID, pickup_date: date, return_date: date, administrative: bool = False
) -> PriceQuote:
    vehicles = Vehicle.objects.all()
    if not administrative:
        vehicles = vehicles.filter(status=VehicleStatus.AVAILABLE)
    vehicle = (
        vehicles.only("uuid", "daily_rate", "weekly_rate", "monthly_rate", "currency")
        .filter(uuid=vehicle_uuid)
        .first()
    )
    if vehicle is None:
        raise NotFound()
    return price_vehicle(vehicle=vehicle, pickup_date=pickup_date, return_date=return_date)


@transaction.atomic
def update_rates(*, actor: User, vehicle_uuid: UUID, data: Any) -> Vehicle:
    current = locked_actor(actor, Permission.PRICING_UPDATE)
    vehicle = Vehicle.objects.select_for_update().filter(uuid=vehicle_uuid).first()
    if vehicle is None:
        raise NotFound()
    serializer = RateWriteSerializer(vehicle, data=data, partial=True)
    serializer.is_valid(raise_exception=True)
    previous = {key: getattr(vehicle, key) for key in serializer.validated_data}
    changed = [
        key for key, value in serializer.validated_data.items() if getattr(vehicle, key) != value
    ]
    if changed:
        for key, value in serializer.validated_data.items():
            setattr(vehicle, key, value)
        vehicle.save(update_fields=[*changed, "updated_at"])
        record_pricing_event(
            action="pricing.rates_updated",
            actor=current,
            subject_uuid=vehicle.uuid,
            fields=changed,
            # Rates are numeric and not sensitive, unlike the free-text fields other
            # audit events deliberately keep out of the log - safe to record verbatim.
            previous_value={key: str(previous[key]) for key in changed},
            new_value={key: str(getattr(vehicle, key)) for key in changed},
        )
    return vehicle


@transaction.atomic
def save_discount(*, actor: User, data: Any, discount_uuid: UUID | None = None) -> RentalDiscount:
    current = locked_actor(actor, Permission.PRICING_UPDATE)
    discount = None
    if discount_uuid is not None:
        discount = RentalDiscount.objects.select_for_update().filter(uuid=discount_uuid).first()
        if discount is None:
            raise NotFound()
    serializer = DiscountWriteSerializer(discount, data=data, partial=discount is not None)
    serializer.is_valid(raise_exception=True)
    creating = discount is None
    discount = serializer.save()
    record_pricing_event(
        action="pricing.discount_created" if creating else "pricing.discount_updated",
        actor=current,
        subject_uuid=discount.uuid,
        fields=list(serializer.validated_data),
    )
    return discount
