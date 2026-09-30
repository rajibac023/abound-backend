from uuid import UUID

from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.admin_views import AdministrativeView
from accounts.policy import Permission
from accounts.throttles import AuthIPThrottle
from availability import services
from availability.models import AvailabilityBlock
from availability.selectors import filter_blocks
from availability.serializers import (
    AvailabilityCheckInputSerializer,
    BlockQuerySerializer,
    BlockSerializer,
)


def check_response(request: Request, *, administrative: bool) -> Response:
    serializer = AvailabilityCheckInputSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    values = serializer.validated_data
    result = services.check_availability(
        vehicle_uuid=values["vehicle"],
        pickup_date=values["pickup_date"],
        return_date=values["return_date"],
        administrative=administrative,
    )
    return Response(result)


class PublicAvailabilityCheckView(APIView):
    authentication_classes = ()
    permission_classes = [AllowAny]
    throttle_classes = [AuthIPThrottle]
    throttle_scope = "availability_check"
    http_method_names = ["post", "options"]

    def post(self, request: Request) -> Response:
        return check_response(request, administrative=False)


class AdminAvailabilityCheckView(AdministrativeView):
    required_permissions = {
        "POST": Permission.AVAILABILITY_VIEW,
        "OPTIONS": Permission.AVAILABILITY_VIEW,
    }

    def post(self, request: Request) -> Response:
        return check_response(request, administrative=True)


class AdminBlockListView(AdministrativeView, ListAPIView):
    required_permissions = {
        "GET": Permission.AVAILABILITY_VIEW,
        "OPTIONS": Permission.AVAILABILITY_VIEW,
        "POST": Permission.AVAILABILITY_MANAGE,
    }
    serializer_class = BlockSerializer

    def get_queryset(self):
        filters = BlockQuerySerializer(data=self.request.query_params)
        filters.is_valid(raise_exception=True)
        return filter_blocks(
            AvailabilityBlock.objects.select_related("vehicle", "created_by"),
            filters.validated_data,
        )

    def post(self, request: Request) -> Response:
        block = services.save_block(actor=request.user, data=request.data)
        return Response(BlockSerializer(block).data, status=201)


class AdminBlockDetailView(AdministrativeView, RetrieveAPIView):
    required_permissions = {
        "GET": Permission.AVAILABILITY_VIEW,
        "OPTIONS": Permission.AVAILABILITY_VIEW,
        "PATCH": Permission.AVAILABILITY_MANAGE,
        "DELETE": Permission.AVAILABILITY_MANAGE,
    }
    queryset = AvailabilityBlock.objects.select_related("vehicle", "created_by")
    serializer_class = BlockSerializer
    lookup_field = "uuid"

    def patch(self, request: Request, uuid: UUID) -> Response:
        block = services.save_block(actor=request.user, block_uuid=uuid, data=request.data)
        return Response(BlockSerializer(block).data)

    def delete(self, request: Request, uuid: UUID) -> Response:
        block = services.save_block(actor=request.user, block_uuid=uuid, data={"is_active": False})
        return Response(BlockSerializer(block).data)
