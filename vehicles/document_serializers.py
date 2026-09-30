from typing import Any

from django.urls import reverse
from rest_framework import serializers

from accounts.serializers import StrictSerializer
from vehicles.choices import VehicleDocumentType
from vehicles.document_processing import normalize_document
from vehicles.models import VehicleDocument


class DocumentMetadataSerializer(StrictSerializer):
    document_type = serializers.ChoiceField(choices=VehicleDocumentType.choices, required=False)
    document_number = serializers.CharField(max_length=100, required=False, allow_blank=True)
    issue_date = serializers.DateField(required=False, allow_null=True)
    expiry_date = serializers.DateField(required=False, allow_null=True)
    notes = serializers.CharField(max_length=10000, required=False, allow_blank=True)

    def to_internal_value(self, data: Any) -> dict[str, Any]:
        if hasattr(data, "getlist"):
            if any(len(data.getlist(key)) != 1 for key in data):
                raise serializers.ValidationError("Specify each document field only once.")
            data = dict(data.items())
        return super().to_internal_value(data)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        def value(field: str) -> Any:
            return attrs.get(field, getattr(self.instance, field, None))

        issue, expiry = value("issue_date"), value("expiry_date")
        if issue and expiry and expiry < issue:
            raise serializers.ValidationError(
                {"expiry_date": ["Must be on or after issue_date."]}
            )
        return attrs


class DocumentUploadSerializer(DocumentMetadataSerializer):
    file = serializers.FileField()

    def validate_file(self, value):
        return normalize_document(value)


class AdminVehicleDocumentSerializer(serializers.ModelSerializer):
    file = serializers.SerializerMethodField()

    class Meta:
        model = VehicleDocument
        fields = [
            "uuid",
            "file",
            "document_type",
            "document_number",
            "issue_date",
            "expiry_date",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_file(self, document: VehicleDocument) -> str:
        vehicle_uuid = self.context.get("vehicle_uuid") or document.vehicle.uuid
        return reverse(
            "v1:admin-vehicle-document-file",
            kwargs={"uuid": vehicle_uuid, "document_uuid": document.uuid},
        )
