from django.db import connection
from rest_framework.exceptions import PermissionDenied

from accounts.models import User
from accounts.policy import has_permission, is_active_user


def locked_actor(actor: User, permission: str | None = None) -> User:
    """Reload and lock the actor first in a domain transaction, before resource locks."""
    if not connection.in_atomic_block:
        raise RuntimeError("Actor authorization requires a transaction.")
    current = User.objects.select_for_update().filter(pk=actor.pk).first()
    if not is_active_user(current) or (
        permission is not None and not has_permission(current, permission)
    ):
        raise PermissionDenied()
    return current
