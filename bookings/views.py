from uuid import UUID

from django.db.models import QuerySet
from django.shortcuts import get_object_or_404
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.admin_views import AdministrativeView
from accounts.policy import Permission
from accounts.throttles import AuthIPThrottle
from bookings import services
from bookings.choices import BookingStatus
from bookings.models import Booking
from bookings.selectors import filter_bookings, own_bookings
from bookings.serializers import (
    AdminBookingNoteSerializer,
    AdminBookingQuerySerializer,
    AdminBookingSerializer,
    AdminBookingTransitionSerializer,
    BookingCancelSerializer,
    BookingQuerySerializer,
    CustomerBookingSerializer,
)

IDEMPOTENCY_HEADER = "Idempotency-Key"


class BookingListView(ListAPIView):
    throttle_classes = [AuthIPThrottle]
    throttle_scope = "bookings"
    serializer_class = CustomerBookingSerializer

    def get_queryset(self) -> QuerySet[Booking]:
        filters = BookingQuerySerializer(data=self.request.query_params)
        filters.is_valid(raise_exception=True)
        return filter_bookings(own_bookings(self.request.user), filters.validated_data)

    def post(self, request: Request) -> Response:
        key = request.headers.get(IDEMPOTENCY_HEADER) or None
        booking = services.create_booking(
            actor=request.user, data=request.data, idempotency_key=key
        )
        return Response(self.get_serializer(booking).data, status=201)


class BookingDetailView(RetrieveAPIView):
    throttle_classes = [AuthIPThrottle]
    throttle_scope = "bookings"
    serializer_class = CustomerBookingSerializer
    lookup_field = "uuid"

    def get_queryset(self) -> QuerySet[Booking]:
        return own_bookings(self.request.user)


class BookingCancelView(APIView):
    throttle_classes = [AuthIPThrottle]
    throttle_scope = "bookings"

    def post(self, request: Request, uuid: UUID) -> Response:
        get_object_or_404(own_bookings(request.user), uuid=uuid)
        serializer = BookingCancelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        booking = services.transition_booking(
            actor=request.user,
            booking_uuid=uuid,
            target_status=BookingStatus.CANCELLED,
            customer_note=serializer.validated_data.get("note"),
        )
        return Response(CustomerBookingSerializer(booking, context={"request": request}).data)


class AdminBookingListView(AdministrativeView, ListAPIView):
    required_permissions = {"GET": Permission.BOOKINGS_VIEW, "OPTIONS": Permission.BOOKINGS_VIEW}
    serializer_class = AdminBookingSerializer

    def get_queryset(self) -> QuerySet[Booking]:
        filters = AdminBookingQuerySerializer(data=self.request.query_params)
        filters.is_valid(raise_exception=True)
        return filter_bookings(
            Booking.objects.select_related("vehicle", "customer", "created_by"),
            filters.validated_data,
        )


class AdminBookingDetailView(AdministrativeView, RetrieveAPIView):
    required_permissions = {
        "GET": Permission.BOOKINGS_VIEW,
        "OPTIONS": Permission.BOOKINGS_VIEW,
        "PATCH": Permission.BOOKINGS_UPDATE,
    }
    queryset = Booking.objects.select_related("vehicle", "customer", "created_by")
    serializer_class = AdminBookingSerializer
    lookup_field = "uuid"

    def patch(self, request: Request, uuid: UUID) -> Response:
        serializer = AdminBookingNoteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        booking = services.update_admin_note(
            actor=request.user,
            booking_uuid=uuid,
            admin_note=serializer.validated_data["admin_note"],
        )
        return Response(AdminBookingSerializer(booking).data)


class AdminBookingTransitionView(AdministrativeView):
    """Confirm, reject, activate, or complete a booking. Cancellation has its own route."""

    required_permissions = {
        "POST": Permission.BOOKINGS_UPDATE,
        "OPTIONS": Permission.BOOKINGS_UPDATE,
    }

    def post(self, request: Request, uuid: UUID) -> Response:
        serializer = AdminBookingTransitionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        booking = services.transition_booking(
            actor=request.user,
            booking_uuid=uuid,
            target_status=values["status"],
            admin_note=values.get("admin_note"),
        )
        return Response(AdminBookingSerializer(booking).data)


class AdminBookingCancelView(AdministrativeView):
    required_permissions = {
        "POST": Permission.BOOKINGS_CANCEL,
        "OPTIONS": Permission.BOOKINGS_CANCEL,
    }

    def post(self, request: Request, uuid: UUID) -> Response:
        serializer = BookingCancelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        booking = services.transition_booking(
            actor=request.user,
            booking_uuid=uuid,
            target_status=BookingStatus.CANCELLED,
            admin_note=serializer.validated_data.get("note"),
        )
        return Response(AdminBookingSerializer(booking).data)
