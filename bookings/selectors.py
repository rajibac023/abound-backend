from typing import Any

from django.db.models import Prefetch, Q, QuerySet

from accounts.models import User
from bookings.models import Booking
from vehicles.models import VehicleImage


def own_bookings(user: User) -> QuerySet[Booking]:
    """Every role sees only its own bookings here; cross-customer visibility is admin-only."""
    return (
        Booking.objects.select_related("vehicle", "customer", "created_by")
        .prefetch_related(
            Prefetch(
                "vehicle__images",
                queryset=VehicleImage.objects.filter(is_primary=True),
                to_attr="booking_primary_images",
            )
        )
        .filter(customer=user)
    )


def filter_bookings(queryset: QuerySet[Booking], filters: dict[str, Any]) -> QuerySet[Booking]:
    if status := filters.get("status"):
        queryset = queryset.filter(status=status)
    if vehicle_uuid := filters.get("vehicle"):
        queryset = queryset.filter(vehicle__uuid=vehicle_uuid)
    if customer_uuid := filters.get("customer"):
        queryset = queryset.filter(customer__uuid=customer_uuid)
    if search := filters.get("search"):
        queryset = queryset.filter(
            Q(booking_reference__icontains=search)
            | Q(customer__email__icontains=search)
            | Q(customer__first_name__icontains=search)
            | Q(customer__last_name__icontains=search)
        )
    if ordering := filters.get("ordering"):
        return queryset.order_by(ordering, "-id")
    return queryset
