"""Reviewed platform grants. Unknown roles and permission codes always fail closed."""

from enum import StrEnum
from typing import Any

from accounts.roles import Role


class Permission(StrEnum):
    VEHICLES_VIEW = "vehicles.view"
    VEHICLES_CREATE = "vehicles.create"
    VEHICLES_UPDATE = "vehicles.update"
    VEHICLES_DELETE = "vehicles.delete"
    BOOKINGS_VIEW = "bookings.view"
    BOOKINGS_CREATE = "bookings.create"
    BOOKINGS_UPDATE = "bookings.update"
    BOOKINGS_CANCEL = "bookings.cancel"
    CUSTOMERS_VIEW = "customers.view"
    CUSTOMERS_UPDATE = "customers.update"
    PRICING_VIEW = "pricing.view"
    PRICING_UPDATE = "pricing.update"
    MAINTENANCE_VIEW = "maintenance.view"
    MAINTENANCE_CREATE = "maintenance.create"
    MAINTENANCE_UPDATE = "maintenance.update"
    AVAILABILITY_VIEW = "availability.view"
    AVAILABILITY_MANAGE = "availability.manage"
    REPORTS_VIEW = "reports.view"
    AUDIT_VIEW = "audit.view"
    USERS_VIEW = "users.view"
    USERS_MANAGE = "users.manage"


ROLE_PERMISSIONS: dict[str, frozenset[Permission]] = {
    Role.SUPER_ADMIN: frozenset(Permission),
    Role.ADMIN: frozenset(Permission),
    Role.STAFF: frozenset(
        {
            Permission.VEHICLES_VIEW,
            Permission.BOOKINGS_VIEW,
            Permission.BOOKINGS_CREATE,
            Permission.BOOKINGS_UPDATE,
            Permission.BOOKINGS_CANCEL,
            Permission.CUSTOMERS_VIEW,
            Permission.PRICING_VIEW,
            Permission.MAINTENANCE_VIEW,
            Permission.MAINTENANCE_CREATE,
            Permission.MAINTENANCE_UPDATE,
            Permission.AVAILABILITY_VIEW,
        }
    ),
    Role.CUSTOMER: frozenset(),
}


def is_active_user(user: Any) -> bool:
    return bool(
        user
        and user.is_authenticated
        and user.is_active
        and getattr(user, "role", None) in ROLE_PERMISSIONS
    )


def permissions_for(user: Any) -> frozenset[Permission]:
    return ROLE_PERMISSIONS[user.role] if is_active_user(user) else frozenset()


def has_permission(user: Any, permission: str) -> bool:
    return permission in permissions_for(user)


def assignable_roles(user: Any) -> tuple[str, ...]:
    if not has_permission(user, Permission.USERS_MANAGE):
        return ()
    if user.role == Role.SUPER_ADMIN:
        return tuple(Role.values)
    return (Role.STAFF, Role.CUSTOMER)


def can_manage_user(actor: Any, subject: Any) -> bool:
    return bool(
        has_permission(actor, Permission.USERS_MANAGE)
        and actor.pk != subject.pk
        and subject.role in assignable_roles(actor)
    )
