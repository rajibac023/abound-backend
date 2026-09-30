from typing import Any

from rest_framework import serializers

from accounts.serializers import StrictSerializer
from maintenance.choices import MaintenanceStatus
from maintenance.models import MaintenanceRecord
from vehicles.models import Vehicle

WRITE_FIELDS = (
    "vehicle",
    "maintenance_type",
    "description",
    "start_date",
    "end_date",
    "odometer",
    "cost",
    "service_provider",
    "status",
    "notes",
)


class MaintenanceWriteSerializer(StrictSerializer, serializers.ModelSerializer):
    vehicle = serializers.SlugRelatedField(slug_field="uuid", queryset=Vehicle.objects.all())

    class Meta:
        model = MaintenanceRecord
        fields = WRITE_FIELDS

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        def value(field: str) -> Any:
            return attrs.get(field, getattr(self.instance, field, None))

        start, end = value("start_date"), value("end_date")
        if start and end and end < start:
            raise serializers.ValidationError({"end_date": ["Must be on or after start_date."]})
        return attrs


class MaintenanceQuerySerializer(StrictSerializer):
    vehicle = serializers.UUIDField(required=False)
    status = serializers.ChoiceField(choices=MaintenanceStatus.choices, required=False)
    page = serializers.IntegerField(min_value=1, required=False)
    page_size = serializers.IntegerField(min_value=1, required=False)


class MaintenanceSerializer(serializers.ModelSerializer):
    vehicle = serializers.SlugRelatedField(slug_field="uuid", read_only=True)
    created_by = serializers.SlugRelatedField(slug_field="uuid", read_only=True)

    class Meta:
        model = MaintenanceRecord
        fields = (
            "uuid",
            *WRITE_FIELDS,
            "created_by",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields
