from uuid import UUID

from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.admin_views import AdministrativeView
from accounts.policy import Permission
from accounts.throttles import AuthIPThrottle
from pricing import services
from pricing.models import RentalDiscount
from pricing.serializers import DiscountSerializer, QuoteInputSerializer, RateSerializer
from vehicles.models import Vehicle


def quote_response(request: Request, *, administrative: bool) -> Response:
    serializer = QuoteInputSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    values = serializer.validated_data
    quote = services.quote_vehicle(
        vehicle_uuid=values["vehicle"],
        pickup_date=values["pickup_date"],
        return_date=values["return_date"],
        administrative=administrative,
    )
    return Response(quote.as_dict())


class PublicQuoteView(APIView):
    authentication_classes = ()
    permission_classes = [AllowAny]
    throttle_classes = [AuthIPThrottle]
    throttle_scope = "pricing_quote"
    http_method_names = ["post", "options"]

    def post(self, request: Request) -> Response:
        return quote_response(request, administrative=False)


class AdminQuoteView(AdministrativeView):
    required_permissions = {"POST": Permission.PRICING_VIEW, "OPTIONS": Permission.PRICING_VIEW}

    def post(self, request: Request) -> Response:
        return quote_response(request, administrative=True)


class AdminRatesView(AdministrativeView, RetrieveAPIView):
    required_permissions = {
        "GET": Permission.PRICING_VIEW,
        "OPTIONS": Permission.PRICING_VIEW,
        "PATCH": Permission.PRICING_UPDATE,
    }
    serializer_class = RateSerializer
    queryset = Vehicle.objects.all()
    lookup_field = "uuid"

    def patch(self, request: Request, uuid: UUID) -> Response:
        vehicle = services.update_rates(actor=request.user, vehicle_uuid=uuid, data=request.data)
        return Response(RateSerializer(vehicle).data)


class AdminDiscountListView(AdministrativeView, ListAPIView):
    required_permissions = {
        "GET": Permission.PRICING_VIEW,
        "OPTIONS": Permission.PRICING_VIEW,
        "POST": Permission.PRICING_UPDATE,
    }
    queryset = RentalDiscount.objects.select_related("vehicle")
    serializer_class = DiscountSerializer

    def post(self, request: Request) -> Response:
        discount = services.save_discount(actor=request.user, data=request.data)
        return Response(DiscountSerializer(discount).data, status=201)


class AdminDiscountDetailView(AdministrativeView, RetrieveAPIView):
    required_permissions = {
        "GET": Permission.PRICING_VIEW,
        "OPTIONS": Permission.PRICING_VIEW,
        "PATCH": Permission.PRICING_UPDATE,
        "DELETE": Permission.PRICING_UPDATE,
    }
    queryset = RentalDiscount.objects.select_related("vehicle")
    serializer_class = DiscountSerializer
    lookup_field = "uuid"

    def patch(self, request: Request, uuid: UUID) -> Response:
        discount = services.save_discount(actor=request.user, discount_uuid=uuid, data=request.data)
        return Response(DiscountSerializer(discount).data)

    def delete(self, request: Request, uuid: UUID) -> Response:
        discount = services.save_discount(
            actor=request.user, discount_uuid=uuid, data={"is_active": False}
        )
        return Response(DiscountSerializer(discount).data)
