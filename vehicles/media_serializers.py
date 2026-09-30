from typing import Any

from django.urls import reverse
from rest_framework import serializers

from accounts.serializers import StrictSerializer
from vehicles.choices import VehicleImageType
from vehicles.image_processing import normalize_image
from vehicles.models import VehicleFeature, VehicleImage


class ImageMetadataSerializer(StrictSerializer):
    image_type = serializers.ChoiceField(choices=VehicleImageType.choices, required=False)
    alt_text = serializers.CharField(max_length=255, required=False, allow_blank=True)
    is_primary = serializers.BooleanField(required=False)
    display_order = serializers.IntegerField(min_value=0, max_value=2147483647, required=False)

    def to_internal_value(self, data: Any) -> dict[str, Any]:
        if hasattr(data, "getlist"):
            if any(len(data.getlist(key)) != 1 for key in data):
                raise serializers.ValidationError("Specify each image field only once.")
            data = dict(data.items())
        return super().to_internal_value(data)


class ImageUploadSerializer(ImageMetadataSerializer):
    image = serializers.FileField()

    def validate_image(self, value):
        return normalize_image(value)


class VehicleImageSerializer(serializers.ModelSerializer):
    image = serializers.SerializerMethodField()

    class Meta:
        model = VehicleImage
        fields = ["uuid", "image", "image_type", "alt_text", "is_primary", "display_order"]
        read_only_fields = fields

    def get_image(self, image: VehicleImage) -> str:
        route = (
            "v1:admin-vehicle-image-file"
            if self.context.get("administrative")
            else "v1:vehicle-image-file"
        )
        vehicle_uuid = self.context.get("vehicle_uuid") or image.vehicle.uuid
        return reverse(route, kwargs={"uuid": vehicle_uuid, "image_uuid": image.uuid})


class AdminVehicleImageSerializer(VehicleImageSerializer):
    class Meta(VehicleImageSerializer.Meta):
        fields = [*VehicleImageSerializer.Meta.fields, "created_at", "updated_at"]
        read_only_fields = fields


class FeatureWriteSerializer(StrictSerializer, serializers.ModelSerializer):
    class Meta:
        model = VehicleFeature
        fields = ["name", "description", "is_active"]
        validators: list = []  # The service handles the database uniqueness race.

    def validate_name(self, value: str) -> str:
        value = " ".join(value.split())
        existing = VehicleFeature.objects.filter(name__iexact=value)
        if self.instance is not None:
            existing = existing.exclude(pk=self.instance.pk)
        if existing.exists():
            raise serializers.ValidationError("A feature with this name already exists.")
        return value


class FeatureSerializer(serializers.ModelSerializer):
    class Meta:
        model = VehicleFeature
        fields = ["uuid", "name", "description"]
        read_only_fields = fields


class AdminFeatureSerializer(FeatureSerializer):
    class Meta(FeatureSerializer.Meta):
        fields = [*FeatureSerializer.Meta.fields, "is_active", "created_at", "updated_at"]
        read_only_fields = fields


class FeatureAssignmentSerializer(StrictSerializer):
    feature_uuids = serializers.ListField(child=serializers.UUIDField(), allow_empty=True)

    def validate_feature_uuids(self, values):
        if len(values) != len(set(values)):
            raise serializers.ValidationError("Do not repeat feature UUIDs.")
        return values
