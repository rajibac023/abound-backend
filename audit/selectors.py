from typing import Any

from django.db.models import QuerySet

from audit.models import AuditEvent


def filter_audit_events(
    queryset: QuerySet[AuditEvent], filters: dict[str, Any]
) -> QuerySet[AuditEvent]:
    if action := filters.get("action"):
        queryset = queryset.filter(action=action)
    if entity_type := filters.get("entity_type"):
        queryset = queryset.filter(entity_type=entity_type)
    if subject_uuid := filters.get("subject_uuid"):
        queryset = queryset.filter(subject_uuid=subject_uuid)
    if actor_uuid := filters.get("actor"):
        queryset = queryset.filter(actor__uuid=actor_uuid)
    if start := filters.get("start_date"):
        queryset = queryset.filter(created_at__date__gte=start)
    if end := filters.get("end_date"):
        queryset = queryset.filter(created_at__date__lte=end)
    return queryset
