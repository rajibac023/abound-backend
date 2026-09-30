from typing import Any

from django.db.models import QuerySet

from maintenance.models import MaintenanceRecord


def filter_maintenance_records(
    queryset: QuerySet[MaintenanceRecord], filters: dict[str, Any]
) -> QuerySet[MaintenanceRecord]:
    if vehicle_uuid := filters.get("vehicle"):
        queryset = queryset.filter(vehicle__uuid=vehicle_uuid)
    if status := filters.get("status"):
        queryset = queryset.filter(status=status)
    return queryset
