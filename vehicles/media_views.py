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
from vehicles import media_services
from vehicles.choices import VehicleStatus
from vehicles.image_processing import BoundedImageUploadHandler
from vehicles.media_serializers import AdminFeatureSerializer, AdminVehicleImageSerializer
from vehicles.models import Vehicle, VehicleFeature, VehicleImage
from vehicles.views import CustomerVehicleView


class FeatureListView(AdministrativeView, ListAPIView):
    required_permissions = {
        "GET": Permission.VEHICLES_VIEW,
        "OPTIONS": Permission.VEHICLES_VIEW,
        "POST": Permission.VEHICLES_UPDATE,
    }
    queryset = VehicleFeature.objects.all()
    serializer_class = AdminFeatureSerializer

    def post(self, request: Request) -> Response:
        feature = media_services.save_feature(actor=request.user, data=request.data)
        return Response(AdminFeatureSerializer(feature).data, status=201)


class FeatureDetailView(AdministrativeView, RetrieveAPIView):
    required_permissions = {
        "GET": Permission.VEHICLES_VIEW,
        "OPTIONS": Permission.VEHICLES_VIEW,
        "PATCH": Permission.VEHICLES_UPDATE,
        "DELETE": Permission.VEHICLES_UPDATE,
    }
    queryset = VehicleFeature.objects.all()
    serializer_class = AdminFeatureSerializer
    lookup_field = "uuid"

    def patch(self, request: Request, uuid: UUID) -> Response:
        feature = media_services.save_feature(
            actor=request.user, feature_uuid=uuid, data=request.data
        )
        return Response(AdminFeatureSerializer(feature).data)

    def delete(self, request: Request, uuid: UUID) -> Response:
        feature = media_services.save_feature(
            actor=request.user, feature_uuid=uuid, data={"is_active": False}
        )
        return Response(AdminFeatureSerializer(feature).data)


class VehicleFeaturesView(AdministrativeView):
    required_permissions = {
        "GET": Permission.VEHICLES_VIEW,
        "OPTIONS": Permission.VEHICLES_VIEW,
        "PUT": Permission.VEHICLES_UPDATE,
    }

    def get(self, request: Request, uuid: UUID) -> Response:
        vehicle = get_object_or_404(Vehicle, uuid=uuid)
        return Response(AdminFeatureSerializer(vehicle.features.all(), many=True).data)

    def put(self, request: Request, uuid: UUID) -> Response:
        vehicle = media_services.assign_features(
            actor=request.user, vehicle_uuid=uuid, data=request.data
        )
        return Response(AdminFeatureSerializer(vehicle.features.all(), many=True).data)


class VehicleImageListView(AdministrativeView, ListAPIView):
    required_permissions = {
        "GET": Permission.VEHICLES_VIEW,
        "OPTIONS": Permission.VEHICLES_VIEW,
        "POST": Permission.VEHICLES_UPDATE,
    }
    serializer_class = AdminVehicleImageSerializer
    parser_classes = [MultiPartParser]

    def initialize_request(self, request, *args, **kwargs):
        request.upload_handlers.insert(0, BoundedImageUploadHandler(request))
        return super().initialize_request(request, *args, **kwargs)

    def get_queryset(self):
        vehicle = get_object_or_404(Vehicle, uuid=self.kwargs["uuid"])
        return vehicle.images.all()

    def get_serializer_context(self):
        return {
            **super().get_serializer_context(),
            "administrative": True,
            "vehicle_uuid": self.kwargs["uuid"],
        }

    def post(self, request: Request, uuid: UUID) -> Response:
        image = media_services.upload_image(
            actor=request.user, vehicle_uuid=uuid, data=request.data
        )
        return Response(self.get_serializer(image).data, status=201)


class VehicleImageDetailView(AdministrativeView, RetrieveAPIView):
    required_permissions = {
        "GET": Permission.VEHICLES_VIEW,
        "OPTIONS": Permission.VEHICLES_VIEW,
        "PATCH": Permission.VEHICLES_UPDATE,
        "DELETE": Permission.VEHICLES_UPDATE,
    }
    serializer_class = AdminVehicleImageSerializer
    lookup_field = "uuid"
    lookup_url_kwarg = "image_uuid"

    def get_queryset(self):
        return VehicleImage.objects.filter(vehicle__uuid=self.kwargs["uuid"])

    def get_serializer_context(self):
        return {
            **super().get_serializer_context(),
            "administrative": True,
            "vehicle_uuid": self.kwargs["uuid"],
        }

    def patch(self, request: Request, uuid: UUID, image_uuid: UUID) -> Response:
        image = media_services.update_image(
            actor=request.user, vehicle_uuid=uuid, image_uuid=image_uuid, data=request.data
        )
        return Response(self.get_serializer(image).data)

    def delete(self, request: Request, uuid: UUID, image_uuid: UUID) -> Response:
        media_services.delete_image(actor=request.user, vehicle_uuid=uuid, image_uuid=image_uuid)
        return Response({"uuid": str(image_uuid), "deleted": True})


def image_file(*, vehicle_uuid: UUID, image_uuid: UUID, administrative: bool) -> FileResponse:
    images = VehicleImage.objects.filter(vehicle__uuid=vehicle_uuid)
    if not administrative:
        images = images.filter(vehicle__status=VehicleStatus.AVAILABLE)
    image = get_object_or_404(images, uuid=image_uuid)
    try:
        file = image.image.open("rb")
    except FileNotFoundError as exc:
        raise NotFound() from exc
    response = FileResponse(
        file, content_type="image/png" if image.image.name.endswith(".png") else "image/jpeg"
    )
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return response


class AdminVehicleImageFileView(AdministrativeView):
    required_permissions = {"GET": Permission.VEHICLES_VIEW, "OPTIONS": Permission.VEHICLES_VIEW}

    def get(self, request: Request, uuid: UUID, image_uuid: UUID) -> FileResponse:
        return image_file(vehicle_uuid=uuid, image_uuid=image_uuid, administrative=True)


class CustomerVehicleImageFileView(CustomerVehicleView):
    def get(self, request: Request, uuid: UUID, image_uuid: UUID) -> FileResponse:
        return image_file(vehicle_uuid=uuid, image_uuid=image_uuid, administrative=False)
