from typing import Any

from rest_framework import serializers

from accounts.serializers import StrictSerializer
from vehicles.choices import FuelType, Transmission, VehicleStatus, VehicleType
from vehicles.models import MAX_RATE

PUBLIC_SORT_FIELDS = {
    "vehicle_name",
    "brand",
    "manufacturing_year",
    "daily_rate",
    "seat_count",
    "featured",
}
ADMIN_SORT_FIELDS = PUBLIC_SORT_FIELDS | {
    "created_at",
    "updated_at",
    "status",
    "registration_number",
}


class VehicleQuerySerializer(StrictSerializer):
    search = serializers.CharField(max_length=100, required=False, allow_blank=True)
    vehicle_type = serializers.ChoiceField(choices=VehicleType.choices, required=False)
    brand = serializers.CharField(max_length=100, required=False)
    fuel_type = serializers.ChoiceField(choices=FuelType.choices, required=False)
    transmission = serializers.ChoiceField(choices=Transmission.choices, required=False)
    featured = serializers.BooleanField(required=False)
    driver_available = serializers.BooleanField(required=False)
    min_seats = serializers.IntegerField(min_value=1, max_value=32767, required=False)
    min_daily_rate = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=0, max_value=MAX_RATE, required=False
    )
    max_daily_rate = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=0, max_value=MAX_RATE, required=False
    )
    ordering = serializers.CharField(max_length=160, required=False)
    page = serializers.IntegerField(min_value=1, required=False)
    # Pagination clamps requested sizes to 100, consistently with the rest of the API.
    page_size = serializers.IntegerField(min_value=1, required=False)

    def to_internal_value(self, data: Any) -> dict[str, Any]:
        if hasattr(data, "getlist") and any(len(data.getlist(key)) != 1 for key in data):
            raise serializers.ValidationError(
                {"non_field_errors": ["Specify each filter only once."]}
            )
        # Query strings are not HTML checkbox forms: absent booleans mean no filter.
        return super().to_internal_value(dict(data.items()))

    def validate_ordering(self, value: str) -> list[str]:
        allowed = ADMIN_SORT_FIELDS if self.context.get("administrative") else PUBLIC_SORT_FIELDS
        fields = value.split(",")
        if not 1 <= len(fields) <= 3 or any(
            field.removeprefix("-") not in allowed for field in fields
        ):
            raise serializers.ValidationError("Choose up to three supported ordering fields.")
        return fields

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if attrs.get("min_daily_rate", 0) > attrs.get("max_daily_rate", MAX_RATE):
            raise serializers.ValidationError(
                {"max_daily_rate": ["Must be at least min_daily_rate."]}
            )
        return attrs


class AdminVehicleQuerySerializer(VehicleQuerySerializer):
    status = serializers.ChoiceField(choices=VehicleStatus.choices, required=False)
    registration_number = serializers.CharField(max_length=100, required=False)
    vin = serializers.CharField(max_length=100, required=False)
