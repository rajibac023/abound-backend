from rest_framework import serializers

from accounts.serializers import StrictSerializer
from bookings.choices import BookingStatus
from bookings.models import Booking
from bookings.policy import customer_can_cancel
from pricing.serializers import QuoteInputSerializer
from vehicles.choices import VehicleStatus
from vehicles.media_serializers import VehicleImageSerializer
from vehicles.models import Vehicle

CUSTOMER_FIELDS: tuple[str, ...] = (
    "uuid",
    "booking_reference",
    "customer",
    "vehicle",
    "pickup_date",
    "return_date",
    "rental_days",
    "subtotal",
    "discount",
    "total_amount",
    "currency",
    "status",
    "customer_note",
    "pricing_breakdown",
    "discount_breakdown",
    "pricing_version",
    "created_at",
    "updated_at",
)
ADMIN_FIELDS = CUSTOMER_FIELDS + (
    "created_by",
    "admin_note",
    "daily_rate_snapshot",
    "weekly_rate_snapshot",
    "monthly_rate_snapshot",
    "priced_at",
)


class BookingInputSerializer(QuoteInputSerializer):
    customer = serializers.UUIDField(required=False)
    customer_note = serializers.CharField(max_length=2000, required=False, allow_blank=True)


class BookingCancelSerializer(StrictSerializer):
    note = serializers.CharField(max_length=2000, required=False, allow_blank=True)


# Cancellation has its own permission (BOOKINGS_CANCEL) and route; excluded here so a single
# status value can never straddle two different required permissions on one endpoint.
ADMIN_TRANSITION_CHOICES = [
    (value, label) for value, label in BookingStatus.choices if value != BookingStatus.CANCELLED
]


class AdminBookingTransitionSerializer(StrictSerializer):
    status = serializers.ChoiceField(choices=ADMIN_TRANSITION_CHOICES)
    admin_note = serializers.CharField(max_length=2000, required=False, allow_blank=True)


class AdminBookingNoteSerializer(StrictSerializer):
    admin_note = serializers.CharField(max_length=2000, allow_blank=True)


class BookingQuerySerializer(StrictSerializer):
    status = serializers.ChoiceField(choices=BookingStatus.choices, required=False)
    vehicle = serializers.UUIDField(required=False)
    ordering = serializers.CharField(max_length=32, required=False)
    # Pagination clamps requested sizes to 100, consistently with the rest of the API.
    page = serializers.IntegerField(min_value=1, required=False)
    page_size = serializers.IntegerField(min_value=1, required=False)

    def validate_ordering(self, value: str) -> str:
        allowed = {"created_at", "-created_at", "pickup_date", "-pickup_date"}
        if value not in allowed:
            raise serializers.ValidationError("Choose a supported ordering field.")
        return value


class AdminBookingQuerySerializer(BookingQuerySerializer):
    # A customer's own booking list is always self-scoped; only admin routes may
    # cross customers, which is how admins view a customer's booking history.
    customer = serializers.UUIDField(required=False)
    search = serializers.CharField(max_length=100, required=False, allow_blank=True)


class BookingSerializer(serializers.ModelSerializer):
    customer = serializers.SlugRelatedField(slug_field="uuid", read_only=True)
    vehicle = serializers.SlugRelatedField(slug_field="uuid", read_only=True)

    class Meta:
        model = Booking
        fields = CUSTOMER_FIELDS
        read_only_fields = fields


class BookingVehicleSerializer(serializers.ModelSerializer):
    primary_image = serializers.SerializerMethodField()

    class Meta:
        model = Vehicle
        fields = ("uuid", "vehicle_name", "brand", "model", "primary_image")
        read_only_fields = fields

    def get_primary_image(self, vehicle: Vehicle):
        # A customer's historical booking remains visible when a vehicle is retired,
        # but its private media must not acquire a new public delivery route.
        if vehicle.status != VehicleStatus.AVAILABLE:
            return None
        images = getattr(vehicle, "booking_primary_images", None)
        image = images[0] if images else None
        if images is None:
            image = vehicle.images.filter(is_primary=True).first()
        return (
            VehicleImageSerializer(image, context={"vehicle_uuid": vehicle.uuid}).data
            if image is not None
            else None
        )


class CustomerBookingSerializer(BookingSerializer):
    # Keep the established vehicle UUID field compatible with existing consumers.
    vehicle_details = BookingVehicleSerializer(source="vehicle", read_only=True)
    can_cancel = serializers.SerializerMethodField()

    class Meta(BookingSerializer.Meta):
        fields = (*CUSTOMER_FIELDS, "vehicle_details", "can_cancel")
        read_only_fields = fields

    def get_can_cancel(self, booking: Booking) -> bool:
        request = self.context.get("request")
        return customer_can_cancel(booking=booking, actor=request.user if request else None)


class AdminBookingSerializer(BookingSerializer):
    created_by = serializers.SlugRelatedField(slug_field="uuid", read_only=True)

    class Meta(BookingSerializer.Meta):
        fields = ADMIN_FIELDS
        read_only_fields = fields
