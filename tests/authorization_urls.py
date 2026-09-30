"""Authorization probes have no domain data and are never mounted in production."""

from django.urls import include, path
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.admin_views import AdministrativeView
from accounts.policy import Permission


class PermissionProbe(AdministrativeView):
    def get(self, request: Request) -> Response:
        return Response({"authorized": True})

    post = get
    patch = get
    delete = get


class UndeclaredAdminProbe(APIView):
    def get(self, request: Request) -> Response:
        return Response({"should_never_be_visible": True})


def permission_method(permission: str) -> str:
    action = permission.split(".")[1]
    return {
        "view": "GET",
        "create": "POST",
        "update": "PATCH",
        "cancel": "POST",
        "delete": "DELETE",
        "manage": "PATCH",
    }[action]


patterns = [
    path(
        f"admin/probe/{permission}/",
        PermissionProbe.as_view(required_permissions={permission_method(permission): permission}),
    )
    for permission in Permission
]
patterns += [
    path("admin/undeclared/", UndeclaredAdminProbe.as_view()),
    path("admin/unmapped/", PermissionProbe.as_view()),
    path("admin/unknown/", PermissionProbe.as_view(required_permissions={"GET": "unknown.view"})),
]
urlpatterns = [path("api/v1/", include((patterns, "api"), namespace="v1"))]
