from typing import Any

from rest_framework import serializers

from accounts.serializers import StrictSerializer

SORT_FIELDS = {"created_at", "email", "first_name", "last_name"}


class CustomerQuerySerializer(StrictSerializer):
    search = serializers.CharField(max_length=100, required=False, allow_blank=True)
    status = serializers.ChoiceField(choices=["active", "inactive"], required=False)
    ordering = serializers.CharField(max_length=32, required=False)
    page = serializers.IntegerField(min_value=1, required=False)
    page_size = serializers.IntegerField(min_value=1, required=False)

    def validate_ordering(self, value: str) -> str:
        if value.removeprefix("-") not in SORT_FIELDS:
            raise serializers.ValidationError("Choose a supported ordering field.")
        return value


class CustomerUpdateSerializer(StrictSerializer):
    first_name = serializers.CharField(max_length=150, required=False)
    last_name = serializers.CharField(max_length=150, required=False)
    phone = serializers.CharField(max_length=32, required=False, allow_blank=True)
    is_active = serializers.BooleanField(required=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if not attrs:
            raise serializers.ValidationError("Supply at least one field to update.")
        return attrs
