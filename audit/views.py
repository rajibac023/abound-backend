from django.db.models import QuerySet
from rest_framework.generics import ListAPIView

from accounts.admin_views import AdministrativeView
from accounts.policy import Permission
from audit.models import AuditEvent
from audit.selectors import filter_audit_events
from audit.serializers import AuditEventQuerySerializer, AuditEventSerializer


class AuditEventListView(AdministrativeView, ListAPIView):
    required_permissions = {"GET": Permission.AUDIT_VIEW, "OPTIONS": Permission.AUDIT_VIEW}
    serializer_class = AuditEventSerializer

    def get_queryset(self) -> QuerySet[AuditEvent]:
        filters = AuditEventQuerySerializer(data=self.request.query_params)
        filters.is_valid(raise_exception=True)
        queryset = AuditEvent.objects.select_related("actor").order_by("-created_at", "-id")
        return filter_audit_events(queryset, filters.validated_data)
