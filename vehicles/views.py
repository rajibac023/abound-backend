from uuid import UUID

from django.db.models import QuerySet
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.admin_views import AdministrativeView
from accounts.policy import Permission
from accounts.throttles import AuthIPThrottle
from vehicles import services
from vehicles.filters import AdminVehicleQuerySerializer, VehicleQuerySerializer
from vehicles.models import Vehicle
from vehicles.selectors import customer_vehicles, filter_vehicles
from vehicles.serializers import (
    AdminVehicleSerializer,
    CustomerVehicleDetailSerializer,
    CustomerVehicleSerializer,
)


class AdminVehicleListView(AdministrativeView, ListAPIView):
    required_permissions = {
        "GET": Permission.VEHICLES_VIEW,
        "OPTIONS": Permission.VEHICLES_VIEW,
        "POST": Permission.VEHICLES_CREATE,
    }
    serializer_class = AdminVehicleSerializer

    def get_queryset(self) -> QuerySet[Vehicle]:
        filters = AdminVehicleQuerySerializer(
            data=self.request.query_params, context={"administrative": True}
        )
        filters.is_valid(raise_exception=True)
        return filter_vehicles(Vehicle.objects.all(), filters.validated_data, administrative=True)

    def post(self, request: Request) -> Response:
        vehicle = services.create_vehicle(actor=request.user, data=request.data)
        return Response(AdminVehicleSerializer(vehicle).data, status=201)


class AdminVehicleDetailView(AdministrativeView, RetrieveAPIView):
    required_permissions = {
        "GET": Permission.VEHICLES_VIEW,
        "OPTIONS": Permission.VEHICLES_VIEW,
        "PUT": Permission.VEHICLES_UPDATE,
        "PATCH": Permission.VEHICLES_UPDATE,
        "DELETE": Permission.VEHICLES_DELETE,
    }
    serializer_class = AdminVehicleSerializer
    queryset = Vehicle.objects.all()
    lookup_field = "uuid"

    def put(self, request: Request, uuid: UUID) -> Response:
        vehicle = services.update_vehicle(
            actor=request.user, vehicle_uuid=uuid, data=request.data, partial=False
        )
        return Response(AdminVehicleSerializer(vehicle).data)

    def patch(self, request: Request, uuid: UUID) -> Response:
        vehicle = services.update_vehicle(
            actor=request.user, vehicle_uuid=uuid, data=request.data, partial=True
        )
        return Response(AdminVehicleSerializer(vehicle).data)

    def delete(self, request: Request, uuid: UUID) -> Response:
        vehicle = services.deactivate_vehicle(actor=request.user, vehicle_uuid=uuid)
        return Response({"uuid": str(vehicle.uuid), "status": vehicle.status, "deactivated": True})


class CustomerVehicleView(APIView):
    authentication_classes = ()
    permission_classes = [AllowAny]
    throttle_classes = [AuthIPThrottle]
    throttle_scope = "vehicle_catalog"
    http_method_names = ["get", "head", "options"]


class CustomerVehicleListView(CustomerVehicleView, ListAPIView):
    serializer_class = CustomerVehicleSerializer

    def get_queryset(self) -> QuerySet[Vehicle]:
        filters = VehicleQuerySerializer(data=self.request.query_params)
        filters.is_valid(raise_exception=True)
        return filter_vehicles(customer_vehicles(), filters.validated_data, administrative=False)


class CustomerVehicleDetailView(CustomerVehicleView, RetrieveAPIView):
    serializer_class = CustomerVehicleDetailSerializer
    lookup_field = "uuid"

    def get_queryset(self) -> QuerySet[Vehicle]:
        return customer_vehicles(detail=True)
