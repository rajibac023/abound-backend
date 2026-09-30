from typing import Any

from rest_framework import serializers

from accounts.serializers import StrictSerializer
from availability.models import AvailabilityBlock
from pricing.serializers import QuoteInputSerializer
from vehicles.models import Vehicle


class AvailabilityCheckInputSerializer(QuoteInputSerializer):
    """Same shape as a pricing quote; one authoritative date rule for both domains."""


class BlockWriteSerializer(StrictSerializer, serializers.ModelSerializer):
    vehicle = serializers.SlugRelatedField(slug_field="uuid", queryset=Vehicle.objects.all())

    class Meta:
        model = AvailabilityBlock
        fields = ("vehicle", "reason", "start_date", "end_date", "is_active")

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        def value(field: str) -> Any:
            return attrs.get(field, getattr(self.instance, field, None))

        start, end = value("start_date"), value("end_date")
        if start and end and end < start:
            raise serializers.ValidationError({"end_date": ["Must be on or after start_date."]})
        return attrs


class BlockSerializer(serializers.ModelSerializer):
    vehicle = serializers.SlugRelatedField(slug_field="uuid", read_only=True)
    created_by = serializers.SlugRelatedField(slug_field="uuid", read_only=True)

    class Meta:
        model = AvailabilityBlock
        fields = (
            "uuid",
            "vehicle",
            "reason",
            "start_date",
            "end_date",
            "is_active",
            "created_by",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class BlockQuerySerializer(StrictSerializer):
    vehicle = serializers.UUIDField(required=False)
    is_active = serializers.BooleanField(required=False)
    page = serializers.IntegerField(min_value=1, required=False)
    page_size = serializers.IntegerField(min_value=1, required=False)
