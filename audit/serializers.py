from rest_framework import serializers

from audit.models import AuditEvent


class AuditEventQuerySerializer(serializers.Serializer):
    action = serializers.CharField(max_length=48, required=False)
    entity_type = serializers.CharField(max_length=32, required=False)
    subject_uuid = serializers.UUIDField(required=False)
    actor = serializers.UUIDField(required=False)
    start_date = serializers.DateField(required=False)
    end_date = serializers.DateField(required=False)

    def validate(self, attrs: dict) -> dict:
        start, end = attrs.get("start_date"), attrs.get("end_date")
        if start and end and start > end:
            raise serializers.ValidationError({"end_date": ["Must be on or after start_date."]})
        return attrs


class AuditEventSerializer(serializers.ModelSerializer):
    actor_uuid = serializers.UUIDField(source="actor.uuid", read_only=True, allow_null=True)

    class Meta:
        model = AuditEvent
        fields = [
            "id",
            "action",
            "actor_uuid",
            "entity_type",
            "subject_uuid",
            "previous_value",
            "new_value",
            "changes",
            "ip_address",
            "user_agent",
            "request_id",
            "created_at",
        ]
        read_only_fields = fields
