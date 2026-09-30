from datetime import date
from typing import Any
from uuid import UUID

from django.db.models import QuerySet

from availability.models import AvailabilityBlock
from bookings.choices import BLOCKING_STATUSES as BOOKING_BLOCKING_STATUSES
from bookings.models import Booking
from maintenance.choices import BLOCKING_STATUSES as MAINTENANCE_BLOCKING_STATUSES
from maintenance.models import MaintenanceRecord
from vehicles.models import Vehicle


def overlapping_bookings(
    vehicle: Vehicle, pickup_date: date, return_date: date, *, exclude_uuid: UUID | None = None
) -> QuerySet[Booking]:
    # Booking ranges are half-open: pickup inclusive, return exclusive. A cancelled,
    # rejected, or completed booking no longer reserves the vehicle.
    queryset = Booking.objects.filter(
        vehicle=vehicle,
        status__in=BOOKING_BLOCKING_STATUSES,
        pickup_date__lt=return_date,
        return_date__gt=pickup_date,
    )
    if exclude_uuid is not None:
        queryset = queryset.exclude(uuid=exclude_uuid)
    return queryset


def overlapping_maintenance(
    vehicle: Vehicle, pickup_date: date, return_date: date
) -> QuerySet[MaintenanceRecord]:
    # Maintenance ranges are inclusive on end_date; only active statuses block availability.
    return MaintenanceRecord.objects.filter(
        vehicle=vehicle,
        status__in=MAINTENANCE_BLOCKING_STATUSES,
        start_date__lt=return_date,
        end_date__gte=pickup_date,
    )


def overlapping_blocks(
    vehicle: Vehicle, pickup_date: date, return_date: date
) -> QuerySet[AvailabilityBlock]:
    return AvailabilityBlock.objects.filter(
        vehicle=vehicle, is_active=True, start_date__lt=return_date, end_date__gte=pickup_date
    )


def filter_blocks(
    queryset: QuerySet[AvailabilityBlock], filters: dict[str, Any]
) -> QuerySet[AvailabilityBlock]:
    if vehicle_uuid := filters.get("vehicle"):
        queryset = queryset.filter(vehicle__uuid=vehicle_uuid)
    if "is_active" in filters:
        queryset = queryset.filter(is_active=filters["is_active"])
    return queryset
