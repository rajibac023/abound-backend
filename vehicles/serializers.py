from typing import Any

from django.core.validators import RegexValidator
from rest_framework import serializers
from rest_framework.validators import UniqueValidator

from accounts.serializers import StrictSerializer
from vehicles.models import Vehicle
from vehicles.validators import normalize_identifier

# Explicit allowlists: adding an administrative model field cannot publish it accidentally.
PUBLIC_VEHICLE_FIELDS = (
    "uuid",
    "vehicle_name",
    "brand",
    "model",
    "variant",
    "vehicle_type",
    "manufacturing_year",
    "color",
    "short_description",
    "description",
    "featured",
    "status",
    "engine_type",
    "engine_capacity_cc",
    "engine_power",
    "torque",
    "fuel_type",
    "transmission",
    "drive_type",
    "number_of_gears",
    "mileage",
    "mileage_unit",
    "seat_count",
    "passenger_capacity",
    "door_count",
    "luggage_capacity",
    "boot_capacity",
    "currency",
    "daily_rate",
    "weekly_rate",
    "monthly_rate",
    "security_deposit",
    "included_kilometers",
    "extra_kilometer_rate",
    "driver_available",
    "driver_daily_rate",
    "fuel_included",
    "insurance_included",
)
VEHICLE_WRITE_FIELDS = tuple(field for field in PUBLIC_VEHICLE_FIELDS if field != "uuid") + (
    "registration_number",
    "vin",
    "internal_notes",
)


class CanonicalIdentifierField(serializers.CharField):
    def to_internal_value(self, data: Any) -> str:
        return normalize_identifier(super().to_internal_value(data))


class VehicleWriteSerializer(StrictSerializer, serializers.ModelSerializer):
    registration_number = CanonicalIdentifierField(
        max_length=64,
        min_length=1,
        validators=[
            UniqueValidator(
                queryset=Vehicle.objects.all(), message="Registration number already exists."
            )
        ],
    )
    vin = CanonicalIdentifierField(
        max_length=64,
        required=False,
        allow_null=True,
        allow_blank=True,
        validators=[
            RegexValidator(r"^[A-Z0-9]+$", "Use letters and digits for VIN/chassis number."),
            UniqueValidator(
                queryset=Vehicle.objects.all(), message="VIN/chassis number already exists."
            ),
        ],
    )

    class Meta:
        model = Vehicle
        fields = VEHICLE_WRITE_FIELDS

    def validate_vin(self, value: str | None) -> str | None:
        return value or None

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        def value(field: str) -> Any:
            return attrs.get(field, getattr(self.instance, field, None))

        if value("passenger_capacity") > value("seat_count"):
            raise serializers.ValidationError({"passenger_capacity": ["Cannot exceed seat_count."]})
        if value("mileage") is not None and not value("mileage_unit"):
            raise serializers.ValidationError(
                {"mileage_unit": ["Specify the unit when mileage is provided."]}
            )
        return attrs


class AdminVehicleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Vehicle
        fields = ("id", "uuid", *VEHICLE_WRITE_FIELDS, "created_at", "updated_at")
        read_only_fields = fields


class CustomerVehicleSerializer(serializers.ModelSerializer):
    primary_image = serializers.SerializerMethodField()

    class Meta:
        model = Vehicle
        fields: tuple[str, ...] = (*PUBLIC_VEHICLE_FIELDS, "primary_image")
        read_only_fields: tuple[str, ...] = fields

    def get_primary_image(self, vehicle):
        from vehicles.media_serializers import VehicleImageSerializer

        primary = next((image for image in vehicle.presentation_images if image.is_primary), None)
        return (
            VehicleImageSerializer(primary, context={"vehicle_uuid": vehicle.uuid}).data
            if primary
            else None
        )


class CustomerVehicleDetailSerializer(CustomerVehicleSerializer):
    gallery = serializers.SerializerMethodField()
    features = serializers.SerializerMethodField()

    class Meta(CustomerVehicleSerializer.Meta):
        fields = (*CustomerVehicleSerializer.Meta.fields, "gallery", "features")
        read_only_fields = fields

    def get_gallery(self, vehicle):
        from vehicles.media_serializers import VehicleImageSerializer

        return VehicleImageSerializer(
            vehicle.presentation_images, many=True, context={"vehicle_uuid": vehicle.uuid}
        ).data

    def get_features(self, vehicle):
        from vehicles.media_serializers import FeatureSerializer

        return FeatureSerializer(vehicle.presentation_features, many=True).data
