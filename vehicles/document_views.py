from uuid import UUID

from django.http import FileResponse
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import NotFound
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.parsers import MultiPartParser
from rest_framework.request import Request
from rest_framework.response import Response

from accounts.admin_views import AdministrativeView
from accounts.policy import Permission
from vehicles import document_services
from vehicles.document_processing import BoundedDocumentUploadHandler
from vehicles.document_serializers import AdminVehicleDocumentSerializer
from vehicles.models import Vehicle, VehicleDocument

CONTENT_TYPES = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg"}


class AdminVehicleDocumentListView(AdministrativeView, ListAPIView):
    required_permissions = {
        "GET": Permission.VEHICLES_VIEW,
        "OPTIONS": Permission.VEHICLES_VIEW,
        "POST": Permission.VEHICLES_UPDATE,
    }
    serializer_class = AdminVehicleDocumentSerializer
    parser_classes = [MultiPartParser]

    def initialize_request(self, request, *args, **kwargs):
        request.upload_handlers.insert(0, BoundedDocumentUploadHandler(request))
        return super().initialize_request(request, *args, **kwargs)

    def get_queryset(self):
        vehicle = get_object_or_404(Vehicle, uuid=self.kwargs["uuid"])
        return vehicle.documents.all()

    def get_serializer_context(self):
        return {**super().get_serializer_context(), "vehicle_uuid": self.kwargs["uuid"]}

    def post(self, request: Request, uuid: UUID) -> Response:
        document = document_services.upload_document(
            actor=request.user, vehicle_uuid=uuid, data=request.data
        )
        return Response(self.get_serializer(document).data, status=201)


class AdminVehicleDocumentDetailView(AdministrativeView, RetrieveAPIView):
    required_permissions = {
        "GET": Permission.VEHICLES_VIEW,
        "OPTIONS": Permission.VEHICLES_VIEW,
        "PATCH": Permission.VEHICLES_UPDATE,
        "DELETE": Permission.VEHICLES_UPDATE,
    }
    serializer_class = AdminVehicleDocumentSerializer
    lookup_field = "uuid"
    lookup_url_kwarg = "document_uuid"

    def get_queryset(self):
        return VehicleDocument.objects.filter(vehicle__uuid=self.kwargs["uuid"])

    def get_serializer_context(self):
        return {**super().get_serializer_context(), "vehicle_uuid": self.kwargs["uuid"]}

    def patch(self, request: Request, uuid: UUID, document_uuid: UUID) -> Response:
        document = document_services.update_document(
            actor=request.user, vehicle_uuid=uuid, document_uuid=document_uuid, data=request.data
        )
        return Response(self.get_serializer(document).data)

    def delete(self, request: Request, uuid: UUID, document_uuid: UUID) -> Response:
        document_services.delete_document(
            actor=request.user, vehicle_uuid=uuid, document_uuid=document_uuid
        )
        return Response({"uuid": str(document_uuid), "deleted": True})


class AdminVehicleDocumentFileView(AdministrativeView):
    required_permissions = {"GET": Permission.VEHICLES_VIEW, "OPTIONS": Permission.VEHICLES_VIEW}

    def get(self, request: Request, uuid: UUID, document_uuid: UUID) -> FileResponse:
        document = get_object_or_404(
            VehicleDocument.objects.filter(vehicle__uuid=uuid), uuid=document_uuid
        )
        try:
            file = document.file.open("rb")
        except FileNotFoundError as exc:
            raise NotFound() from exc
        extension = document.file.name.rsplit(".", 1)[-1]
        content_type = CONTENT_TYPES.get(extension, "application/octet-stream")
        response = FileResponse(file, content_type=content_type)
        response["X-Content-Type-Options"] = "nosniff"
        response["Content-Security-Policy"] = "default-src 'none'; sandbox"
        response["Content-Disposition"] = "attachment"
        return response
