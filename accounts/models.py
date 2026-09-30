import uuid
from typing import Any

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.functions import Lower, Trim
from django.utils import timezone

from accounts.managers import UserManager, normalize_email_identity
from accounts.roles import Role


class User(AbstractUser):
    Role = Role

    username = None
    date_joined = None
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=32, blank=True)
    email_verified_at = models.DateTimeField(null=True, blank=True, editable=False)
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.CUSTOMER)
    created_at = models.DateTimeField(default=timezone.now, editable=False, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []
    objects = UserManager()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(email=Lower(Trim("email"))) & ~models.Q(email=""),
                name="accounts_user_email_canonical",
            ),
            models.CheckConstraint(
                condition=models.Q(role__in=Role.values),
                name="accounts_user_role_valid",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(role=Role.SUPER_ADMIN, is_superuser=True, is_staff=True)
                    | models.Q(role=Role.ADMIN, is_superuser=False, is_staff=True)
                    | models.Q(
                        role__in=[Role.STAFF, Role.CUSTOMER], is_superuser=False, is_staff=False
                    )
                ),
                name="accounts_user_role_flags_consistent",
            ),
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.email = normalize_email_identity(self.email)
        super().save(*args, **kwargs)


class RegistrationVerification(models.Model):
    """A short-lived email challenge, with no password or plaintext code stored."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True)
    code_digest = models.CharField(max_length=64)
    failed_attempts = models.PositiveSmallIntegerField(default=0)
    sent_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(db_index=True)
    consumed_at = models.DateTimeField(null=True, blank=True)


class AuthSession(models.Model):
    class Client(models.TextChoices):
        MOBILE = "mobile", "Mobile"
        WEB = "web", "Web"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    client = models.CharField(max_length=8, choices=Client.choices)
    refresh_digest = models.CharField(max_length=64)
    password_fingerprint = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)
    revoked_at = models.DateTimeField(null=True, blank=True)


class AuthRateLimit(models.Model):
    """Atomic shared counters; identities are keyed HMACs, never raw IPs/emails."""

    key = models.CharField(primary_key=True, max_length=64)
    count = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField(db_index=True)
