from django.db.models import Q, QuerySet

from accounts.models import User
from accounts.policy import Permission, has_permission
from accounts.roles import Role


def visible_users(actor: User) -> QuerySet[User]:
    """Scope lists and detail lookups together; hidden accounts also return 404."""
    users = User.objects.order_by("id")
    if not has_permission(actor, Permission.USERS_VIEW):
        return users.none()
    if actor.role == Role.SUPER_ADMIN:
        return users
    return users.filter(Q(role__in=[Role.STAFF, Role.CUSTOMER]) | Q(pk=actor.pk))
