from django.db import models


class Role(models.TextChoices):
    SUPER_ADMIN = "SUPER_ADMIN"
    ADMIN = "ADMIN"
    STAFF = "STAFF"
    CUSTOMER = "CUSTOMER"


def django_privilege_flags(role: str) -> tuple[bool, bool]:
    """Django admin-site flags follow the role; they never grant API permissions."""
    return role in {Role.SUPER_ADMIN, Role.ADMIN}, role == Role.SUPER_ADMIN
