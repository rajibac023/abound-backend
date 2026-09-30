from typing import Any

from rest_framework import serializers

from accounts.serializers import StrictSerializer
from pricing.models import RentalDiscount
from vehicles.models import Vehicle

RATE_FIELDS = ("daily_rate", "weekly_rate", "monthly_rate", "currency")


class QuoteInputSerializer(StrictSerializer):
    vehicle = serializers.UUIDField()
    pickup_date = serializers.DateField(input_formats=["%Y-%m-%d"])
    return_date = serializers.DateField(input_formats=["%Y-%m-%d"])


class RateWriteSerializer(StrictSerializer, serializers.ModelSerializer):
    class Meta:
        model = Vehicle
        fields = RATE_FIELDS


class RateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Vehicle
        fields = ("uuid", *RATE_FIELDS, "updated_at")
        read_only_fields = fields


class DiscountWriteSerializer(StrictSerializer, serializers.ModelSerializer):
    vehicle = serializers.SlugRelatedField(
        slug_field="uuid", queryset=Vehicle.objects.all(), allow_null=True, required=False
    )

    class Meta:
        model = RentalDiscount
        fields = (
            "label",
            "vehicle",
            "kind",
            "value",
            "currency",
            "minimum_days",
            "starts_on",
            "ends_on",
            "is_active",
        )

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        def value(field: str, default=None):
            return attrs.get(field, getattr(self.instance, field, default))

        if value("kind") == RentalDiscount.Kind.PERCENTAGE and value("value") > 100:
            raise serializers.ValidationError({"value": ["Percentage cannot exceed 100."]})
        start, end = value("starts_on"), value("ends_on")
        if start and end and end <= start:
            raise serializers.ValidationError({"ends_on": ["End date must be after start date."]})
        return attrs


class DiscountSerializer(serializers.ModelSerializer):
    vehicle = serializers.SlugRelatedField(slug_field="uuid", read_only=True)

    class Meta:
        model = RentalDiscount
        fields = ("uuid", *DiscountWriteSerializer.Meta.fields, "created_at", "updated_at")
        read_only_fields = fields
