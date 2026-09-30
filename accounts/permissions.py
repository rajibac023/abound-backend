from __future__ import annotations

from typing import TYPE_CHECKING

from rest_framework.permissions import BasePermission
from rest_framework.request import Request

if TYPE_CHECKING:
    from rest_framework.views import APIView

from accounts.policy import has_permission, is_active_user
from accounts.roles import Role


class HasDomainPermission(BasePermission):
    """Map every allowed HTTP method to a registered permission; missing means deny."""

    def has_permission(self, request: Request, view: APIView) -> bool:
        if not is_active_user(request.user) or request.user.role == Role.CUSTOMER:
            return False
        method = "GET" if request.method == "HEAD" else request.method
        required = getattr(view, "required_permissions", {}).get(method)
        return isinstance(required, str) and has_permission(request.user, required)


class ApplicationPermission(BasePermission):
    """Authenticated by default; admin routes additionally require declared grants."""

    def has_permission(self, request: Request, view: APIView) -> bool:
        if not is_active_user(request.user):
            return False
        if request.path.startswith("/api/v1/admin/"):
            return HasDomainPermission().has_permission(request, view)
        return True


class IsPlatformAdmin(BasePermission):
    def has_permission(self, request: Request, view: APIView) -> bool:
        return is_active_user(request.user) and request.user.role in {Role.SUPER_ADMIN, Role.ADMIN}
