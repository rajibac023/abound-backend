from typing import Any

from django.contrib.auth.base_user import BaseUserManager
from django.core.validators import validate_email

from accounts.roles import Role, django_privilege_flags


def normalize_email_identity(email: str) -> str:
    """Abound treats the entire email address as a case-insensitive identity."""
    return email.strip().lower()


class UserManager(BaseUserManager):
    use_in_migrations = True

    def get_by_natural_key(self, username: str) -> Any:
        return self.get(email=normalize_email_identity(username))

    def create_user(self, email: str, password: str | None = None, **extra_fields: Any) -> Any:
        email = normalize_email_identity(email)
        if not email:
            raise ValueError("An email address is required.")
        validate_email(email)
        role = extra_fields.setdefault("role", Role.CUSTOMER)
        if role not in Role.values:
            raise ValueError("A supported platform role is required.")
        is_staff, is_superuser = django_privilege_flags(role)
        for name, expected in (("is_staff", is_staff), ("is_superuser", is_superuser)):
            if extra_fields.setdefault(name, expected) != expected:
                raise ValueError("Django privilege flags must match the platform role.")
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email: str, password: str, **extra_fields: Any) -> Any:
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        extra_fields.setdefault("role", Role.SUPER_ADMIN)
        if not all(extra_fields[field] for field in ("is_staff", "is_superuser", "is_active")):
            raise ValueError("A superuser must be active and have staff and superuser privileges.")
        if extra_fields["role"] != Role.SUPER_ADMIN:
            raise ValueError("A superuser must have the SUPER_ADMIN role.")
        return self.create_user(email, password, **extra_fields)
