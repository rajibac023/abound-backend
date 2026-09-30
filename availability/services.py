from datetime import date
from typing import Any
from uuid import UUID

from django.db import connection, transaction
from rest_framework.exceptions import NotFound, ValidationError

from accounts.authorization import locked_actor
from accounts.models import User
from accounts.policy import Permission
from audit.services import record_availability_event
from availability.models import AvailabilityBlock
from availability.selectors import overlapping_blocks, overlapping_bookings, overlapping_maintenance
from availability.serializers import BlockWriteSerializer
from bookings.choices import BLOCKING_STATUSES
from bookings.models import Booking
from pricing.duration import rental_days
from vehicles.choices import VehicleStatus
from vehicles.models import Vehicle


def lock_vehicle_schedules(*vehicle_ids: int) -> None:
    """Every writer changing availability must share the booking vehicle lock."""
    if not connection.in_atomic_block:
        raise RuntimeError("Schedule changes require a transaction.")
    # Ordered locks also cover records reassigned from one vehicle to another.
    list(Vehicle.objects.select_for_update().filter(pk__in=vehicle_ids).order_by("pk"))


def assert_no_reserved_booking(*, vehicle: Vehicle, start_date: date, end_date: date) -> None:
    """An inclusive operational closure may not invalidate a reserved booking."""
    if Booking.objects.filter(
        vehicle=vehicle,
        status__in=BLOCKING_STATUSES,
        pickup_date__lte=end_date,
        return_date__gt=start_date,
    ).exists():
        raise ValidationError(
            {"start_date": ["These dates overlap a reserved booking. Resolve that booking first."]}
        )


def availability_reasons(
    *,
    vehicle: Vehicle,
    pickup_date: date,
    return_date: date,
    exclude_booking_uuid: UUID | None = None,
    include_status: bool = True,
) -> list[str]:
    """Pure read of every independent unavailability rule; never mutates state."""
    rental_days(pickup_date, return_date)
    reasons = []
    if include_status and vehicle.status != VehicleStatus.AVAILABLE:
        reasons.append("STATUS")
    if overlapping_bookings(
        vehicle, pickup_date, return_date, exclude_uuid=exclude_booking_uuid
    ).exists():
        reasons.append("BOOKING")
    if overlapping_maintenance(vehicle, pickup_date, return_date).exists():
        reasons.append("MAINTENANCE")
    if overlapping_blocks(vehicle, pickup_date, return_date).exists():
        reasons.append("BLOCKED")
    return reasons


def assert_period_available(
    *,
    vehicle: Vehicle,
    pickup_date: date,
    return_date: date,
    exclude_booking_uuid: UUID | None = None,
) -> None:
    """Booking-creation entry point. Vehicle status is already enforced by the caller's lock."""
    reasons = availability_reasons(
        vehicle=vehicle,
        pickup_date=pickup_date,
        return_date=return_date,
        exclude_booking_uuid=exclude_booking_uuid,
        include_status=False,
    )
    if reasons:
        raise ValidationError(
            {"pickup_date": ["The vehicle is not available for the requested dates."]}
        )


def check_availability(
    *, vehicle_uuid: UUID, pickup_date: date, return_date: date, administrative: bool = False
) -> dict[str, Any]:
    vehicles = Vehicle.objects.all()
    if not administrative:
        vehicles = vehicles.filter(status=VehicleStatus.AVAILABLE)
    vehicle = vehicles.filter(uuid=vehicle_uuid).first()
    if vehicle is None:
        raise NotFound()
    reasons = availability_reasons(
        vehicle=vehicle,
        pickup_date=pickup_date,
        return_date=return_date,
        include_status=administrative,
    )
    return {
        "vehicle": str(vehicle.uuid),
        "pickup_date": pickup_date.isoformat(),
        "return_date": return_date.isoformat(),
        "rental_days": rental_days(pickup_date, return_date),
        "available": not reasons,
        "reasons": reasons,
    }


@transaction.atomic
def save_block(*, actor: User, data: Any, block_uuid: UUID | None = None) -> AvailabilityBlock:
    current = locked_actor(actor, Permission.AVAILABILITY_MANAGE)
    block = None
    if block_uuid is not None:
        block = AvailabilityBlock.objects.select_for_update().filter(uuid=block_uuid).first()
        if block is None:
            raise NotFound()
    serializer = BlockWriteSerializer(block, data=data, partial=block is not None)
    serializer.is_valid(raise_exception=True)
    creating = block is None
    values = serializer.validated_data
    vehicle = values["vehicle"] if block is None else values.get("vehicle", block.vehicle)
    lock_vehicle_schedules(vehicle.pk, block.vehicle_id if block else vehicle.pk)
    if values.get("is_active", block.is_active if block else True):
        assert_no_reserved_booking(
            vehicle=vehicle,
            start_date=values.get("start_date", block.start_date if block else None),
            end_date=values.get("end_date", block.end_date if block else None),
        )
    block = serializer.save(created_by=current) if creating else serializer.save()
    record_availability_event(
        action="availability.block_created" if creating else "availability.block_updated",
        actor=current,
        subject_uuid=block.uuid,
        vehicle_uuid=block.vehicle.uuid,
        fields=list(serializer.validated_data),
    )
    return block
