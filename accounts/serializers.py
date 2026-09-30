import re
from collections.abc import Mapping
from typing import Any

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from accounts.managers import normalize_email_identity
from accounts.models import AuthSession, User


class StrictSerializer(serializers.Serializer):
    def to_internal_value(self, data: Any) -> dict[str, Any]:
        if isinstance(data, Mapping) and set(data) - set(self.fields):
            raise serializers.ValidationError(
                {"non_field_errors": ["Unsupported fields supplied."]}
            )
        return super().to_internal_value(data)


class EmailSerializer(StrictSerializer):
    email = serializers.EmailField(max_length=254)

    def validate_email(self, value: str) -> str:
        return normalize_email_identity(value)


def validate_new_password(password: str, confirmation: str, user: User) -> None:
    if password != confirmation:
        raise serializers.ValidationError({"password_confirm": ["Passwords do not match."]})
    try:
        validate_password(password, user=user)
    except DjangoValidationError as exc:
        raise serializers.ValidationError({"password": exc.messages}) from exc


class RegistrationSerializer(EmailSerializer):
    password = serializers.CharField(write_only=True, trim_whitespace=False, max_length=128)
    password_confirm = serializers.CharField(write_only=True, trim_whitespace=False, max_length=128)
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150)
    phone = serializers.CharField(max_length=32, required=False, allow_blank=True)

    def validate_email(self, value: str) -> str:
        email = super().validate_email(value)
        if User.objects.filter(email=email).exists():
            raise serializers.ValidationError("Unable to register with this email address.")
        return email

    def validate_phone(self, value: str) -> str:
        if not value:
            return value
        if not re.fullmatch(r"\+[1-9][0-9]{6,14}", value):
            raise serializers.ValidationError(
                "Use an international phone number including its country code."
            )
        return value

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        candidate = User(
            email=attrs["email"], first_name=attrs["first_name"], last_name=attrs["last_name"]
        )
        validate_new_password(attrs["password"], attrs["password_confirm"], candidate)
        return attrs


class VerifiedRegistrationSerializer(RegistrationSerializer):
    verification_id = serializers.UUIDField(write_only=True)
    code = serializers.RegexField(r"\A[0-9]{6}\Z", write_only=True, min_length=6, max_length=6)


class PasswordResetRequestSerializer(EmailSerializer):
    """Extends email-only reset request with an optional client hint for URL routing."""
    client = serializers.ChoiceField(choices=AuthSession.Client.choices, default="web")


class LoginSerializer(EmailSerializer):
    password = serializers.CharField(write_only=True, trim_whitespace=False, max_length=128)
    client = serializers.ChoiceField(choices=AuthSession.Client.choices, default="mobile")


class RefreshSerializer(StrictSerializer):
    refresh = serializers.CharField(write_only=True, max_length=2048, required=False)


class ChangePasswordSerializer(StrictSerializer):
    current_password = serializers.CharField(write_only=True, trim_whitespace=False, max_length=128)
    password = serializers.CharField(write_only=True, trim_whitespace=False, max_length=128)
    password_confirm = serializers.CharField(write_only=True, trim_whitespace=False, max_length=128)


class ResetConfirmSerializer(StrictSerializer):
    uuid = serializers.UUIDField()
    token = serializers.CharField(write_only=True, max_length=128)
    password = serializers.CharField(write_only=True, trim_whitespace=False, max_length=128)
    password_confirm = serializers.CharField(write_only=True, trim_whitespace=False, max_length=128)


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = [
            "id",
            "uuid",
            "email",
            "first_name",
            "last_name",
            "phone",
            "role",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class ProfileUpdateSerializer(StrictSerializer):
    first_name = serializers.CharField(max_length=150, required=False)
    last_name = serializers.CharField(max_length=150, required=False)
    phone = serializers.CharField(max_length=32, required=False, allow_blank=True)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if not attrs:
            raise serializers.ValidationError("Supply at least one field to update.")
        return attrs


class UserAccessSerializer(StrictSerializer):
    role = serializers.ChoiceField(choices=User.Role.choices, required=False)
    is_active = serializers.BooleanField(required=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if not attrs:
            raise serializers.ValidationError("Supply role or is_active.")
        return attrs
