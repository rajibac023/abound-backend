from typing import Any

from django.db.models import Prefetch, Q, QuerySet

from vehicles.choices import VehicleStatus
from vehicles.models import Vehicle, VehicleFeature, VehicleImage
from vehicles.serializers import PUBLIC_VEHICLE_FIELDS
from vehicles.validators import normalize_identifier


def customer_vehicles(*, detail: bool = False) -> QuerySet[Vehicle]:
    images = VehicleImage.objects.all()
    if not detail:
        images = images.filter(is_primary=True)
    vehicles = (
        Vehicle.objects.filter(status=VehicleStatus.AVAILABLE)
        .only("id", *PUBLIC_VEHICLE_FIELDS)
        .prefetch_related(Prefetch("images", queryset=images, to_attr="presentation_images"))
    )
    if detail:
        vehicles = vehicles.prefetch_related(
            Prefetch(
                "features",
                queryset=VehicleFeature.objects.filter(is_active=True),
                to_attr="presentation_features",
            )
        )
    return vehicles


def filter_vehicles(
    queryset: QuerySet[Vehicle], filters: dict[str, Any], *, administrative: bool
) -> QuerySet[Vehicle]:
    for field in ("vehicle_type", "fuel_type", "transmission", "featured", "driver_available"):
        if field in filters:
            queryset = queryset.filter(**{field: filters[field]})
    if "brand" in filters:
        queryset = queryset.filter(brand__iexact=filters["brand"])
    for field, lookup in (
        ("min_seats", "seat_count__gte"),
        ("min_daily_rate", "daily_rate__gte"),
        ("max_daily_rate", "daily_rate__lte"),
    ):
        if field in filters:
            queryset = queryset.filter(**{lookup: filters[field]})
    if administrative:
        if "status" in filters:
            queryset = queryset.filter(status=filters["status"])
        for field in ("registration_number", "vin"):
            if field in filters:
                queryset = queryset.filter(**{field: normalize_identifier(filters[field])})
    if search := filters.get("search"):
        predicate = (
            Q(vehicle_name__icontains=search)
            | Q(brand__icontains=search)
            | Q(model__icontains=search)
            | Q(variant__icontains=search)
        )
        if administrative:
            identity = normalize_identifier(search)
            if identity:
                predicate |= Q(registration_number__icontains=identity) | Q(vin__icontains=identity)
        queryset = queryset.filter(predicate)
    # Always end in a unique key, avoiding unstable pages for equal prices/names.
    if ordering := filters.get("ordering"):
        return queryset.order_by(*ordering, "id")
    return queryset.order_by("-created_at", "-id")
