from django.db.models import QuerySet
from django.shortcuts import get_object_or_404
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.access_services import update_user_access
from accounts.models import User
from accounts.permissions import HasDomainPermission, IsPlatformAdmin
from accounts.policy import ROLE_PERMISSIONS, Permission, assignable_roles
from accounts.roles import Role
from accounts.selectors import visible_users
from accounts.serializers import UserAccessSerializer, UserSerializer
from accounts.throttles import AuthIPThrottle


class AdministrativeView(APIView):
    permission_classes = [HasDomainPermission]
    required_permissions: dict[str, str] = {}
    throttle_classes = [AuthIPThrottle]
    throttle_scope = "account"


class RoleListView(AdministrativeView):
    permission_classes = [HasDomainPermission, IsPlatformAdmin]
    required_permissions = {"GET": Permission.USERS_VIEW, "OPTIONS": Permission.USERS_VIEW}

    def get(self, request: Request) -> Response:
        return Response(
            {
                "roles": [
                    {"role": role, "permissions": sorted(ROLE_PERMISSIONS[role])}
                    for role in Role.values
                ],
                "assignable_roles": assignable_roles(request.user),
            }
        )


class UserListView(AdministrativeView, ListAPIView):
    required_permissions = {"GET": Permission.USERS_VIEW, "OPTIONS": Permission.USERS_VIEW}
    serializer_class = UserSerializer

    def get_queryset(self) -> QuerySet[User]:
        return visible_users(self.request.user)


class UserDetailView(AdministrativeView, RetrieveAPIView):
    required_permissions = {"GET": Permission.USERS_VIEW, "OPTIONS": Permission.USERS_VIEW}
    serializer_class = UserSerializer
    lookup_field = "uuid"

    def get_queryset(self) -> QuerySet[User]:
        return visible_users(self.request.user)


class UserAccessView(AdministrativeView):
    required_permissions = {"PATCH": Permission.USERS_MANAGE, "OPTIONS": Permission.USERS_MANAGE}

    def patch(self, request: Request, uuid: str) -> Response:
        subject = get_object_or_404(visible_users(request.user), uuid=uuid)
        serializer = UserAccessSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = update_user_access(
            actor=request.user, subject_uuid=subject.uuid, **serializer.validated_data
        )
        return Response(UserSerializer(user).data)
