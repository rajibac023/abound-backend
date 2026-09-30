from uuid import UUID

from django.db.models import QuerySet
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.request import Request
from rest_framework.response import Response

from accounts.admin_views import AdministrativeView
from accounts.policy import Permission
from maintenance import services
from maintenance.models import MaintenanceRecord
from maintenance.selectors import filter_maintenance_records
from maintenance.serializers import MaintenanceQuerySerializer, MaintenanceSerializer


class AdminMaintenanceListView(AdministrativeView, ListAPIView):
    required_permissions = {
        "GET": Permission.MAINTENANCE_VIEW,
        "OPTIONS": Permission.MAINTENANCE_VIEW,
        "POST": Permission.MAINTENANCE_CREATE,
    }
    serializer_class = MaintenanceSerializer

    def get_queryset(self) -> QuerySet[MaintenanceRecord]:
        filters = MaintenanceQuerySerializer(data=self.request.query_params)
        filters.is_valid(raise_exception=True)
        return filter_maintenance_records(
            MaintenanceRecord.objects.select_related("vehicle", "created_by"),
            filters.validated_data,
        )

    def post(self, request: Request) -> Response:
        record = services.create_maintenance_record(actor=request.user, data=request.data)
        return Response(MaintenanceSerializer(record).data, status=201)


class AdminMaintenanceDetailView(AdministrativeView, RetrieveAPIView):
    required_permissions = {
        "GET": Permission.MAINTENANCE_VIEW,
        "OPTIONS": Permission.MAINTENANCE_VIEW,
        "PATCH": Permission.MAINTENANCE_UPDATE,
    }
    queryset = MaintenanceRecord.objects.select_related("vehicle", "created_by")
    serializer_class = MaintenanceSerializer
    lookup_field = "uuid"

    def patch(self, request: Request, uuid: UUID) -> Response:
        record = services.update_maintenance_record(
            actor=request.user, record_uuid=uuid, data=request.data
        )
        return Response(MaintenanceSerializer(record).data)
