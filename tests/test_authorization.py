from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import Group
from django.contrib.auth.models import Permission as DjangoPermission
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.test import TransactionTestCase, override_settings
from django.urls import URLPattern, URLResolver, get_resolver
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import AccessToken

from accounts.access_services import update_user_access
from accounts.models import AuthSession, User
from accounts.permissions import HasDomainPermission
from accounts.policy import Permission, has_permission
from accounts.roles import Role
from accounts.services import login
from audit.models import AuditEvent
from tests.authorization_urls import permission_method

PASSWORD = "Granite-Peak!Orchid-294"
ADMIN = "/api/v1/admin/"
AUTH = "/api/v1/auth/"
STAFF_GRANTS = {
    "vehicles.view",
    "bookings.view",
    "bookings.create",
    "bookings.update",
    "bookings.cancel",
    "customers.view",
    "pricing.view",
    "maintenance.view",
    "maintenance.create",
    "maintenance.update",
    "availability.view",
}


class AuthorizationTests(APITestCase):
    def setUp(self) -> None:
        self.users = {
            role: User.objects.create_user(
                email=f"{role.lower()}@example.com", password=PASSWORD, role=role
            )
            for role in Role.values
        }
        self.tokens = {
            role: login(email=user.email, password=PASSWORD, client="mobile")[1]
            for role, user in self.users.items()
        }

    def authorize(self, role: str) -> None:
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.tokens[role]['access']}")

    def access_url(self, user: User) -> str:
        return ADMIN + f"users/{user.uuid}/access/"

    @override_settings(ROOT_URLCONF="tests.authorization_urls")
    def test_every_permission_is_enforced_for_each_role_over_http(self) -> None:
        self.assertEqual(len(Permission), 21)
        for role in Role.values:
            self.authorize(role)
            for permission in Permission:
                with self.subTest(role=role, permission=permission):
                    allowed = role in {Role.SUPER_ADMIN, Role.ADMIN} or (
                        role == Role.STAFF and permission in STAFF_GRANTS
                    )
                    response = getattr(self.client, permission_method(permission).lower())(
                        ADMIN + f"probe/{permission}/"
                    )
                    self.assertEqual(response.status_code, 200 if allowed else 403)
                    self.assertEqual(response.json()["success"], allowed)

    @override_settings(ROOT_URLCONF="tests.authorization_urls")
    def test_unauthenticated_permission_requests_are_401(self) -> None:
        for permission in Permission:
            response = getattr(self.client, permission_method(permission).lower())(
                ADMIN + f"probe/{permission}/"
            )
            self.assertEqual(response.status_code, 401)
            self.assertIn("Bearer", response["WWW-Authenticate"])

    @override_settings(ROOT_URLCONF="tests.authorization_urls")
    def test_missing_unknown_and_unmapped_permissions_deny_even_super_admin(self) -> None:
        for role in Role.values:
            self.authorize(role)
            for suffix in ("undeclared/", "unmapped/", "unknown/"):
                self.assertEqual(self.client.get(ADMIN + suffix).status_code, 403)
            self.assertEqual(self.client.post(ADMIN + "probe/vehicles.view/").status_code, 403)

    def test_admin_routes_reject_anonymous_staff_and_customer(self) -> None:
        paths = ["roles/", "users/", f"users/{self.users[Role.CUSTOMER].uuid}/", "audit/"]
        for role in (None, Role.STAFF, Role.CUSTOMER):
            if role is not None:
                self.authorize(role)
            for method in ("get", "head", "options"):
                for path in paths:
                    response = getattr(self.client, method)(ADMIN + path)
                    self.assertEqual(response.status_code, 401 if role is None else 403)
                    self.assertEqual(response["Cache-Control"], "no-store")
            response = self.client.patch(
                self.access_url(self.users[Role.CUSTOMER]), {"role": Role.STAFF}
            )
            self.assertEqual(response.status_code, 401 if role is None else 403)
        self.users[Role.CUSTOMER].refresh_from_db()
        self.assertEqual(self.users[Role.CUSTOMER].role, Role.CUSTOMER)

    def test_admin_reads_are_paginated_and_customer_data_is_not_public(self) -> None:
        for role in (Role.ADMIN, Role.SUPER_ADMIN):
            self.authorize(role)
            for path in ("users/", "audit/"):
                response = self.client.get(ADMIN + path + "?page_size=1")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(len(response.json()["data"]["results"]), 1)
                self.assertIsNotNone(response.json()["data"]["next"])
                self.assertEqual(response["Cache-Control"], "no-store")
            self.assertEqual(self.client.get(ADMIN + "roles/").status_code, 200)

    def test_admin_user_visibility_is_scoped_for_lists_and_details(self) -> None:
        peer = User.objects.create_user(
            email="peer@example.com", password=PASSWORD, role=Role.ADMIN
        )
        self.authorize(Role.ADMIN)
        data = self.client.get(ADMIN + "users/").json()["data"]
        self.assertEqual(
            {item["uuid"] for item in data["results"]},
            {str(self.users[role].uuid) for role in (Role.ADMIN, Role.STAFF, Role.CUSTOMER)},
        )
        for hidden_uuid in (peer.uuid, self.users[Role.SUPER_ADMIN].uuid, uuid4()):
            self.assertEqual(self.client.get(ADMIN + f"users/{hidden_uuid}/").status_code, 404)
            self.assertEqual(
                self.client.patch(
                    ADMIN + f"users/{hidden_uuid}/access/", {"is_active": False}
                ).status_code,
                404,
            )
        self.authorize(Role.SUPER_ADMIN)
        self.assertEqual(self.client.get(ADMIN + "users/").json()["data"]["count"], 5)

    def test_user_responses_exclude_internal_credentials_and_django_grants(self) -> None:
        self.authorize(Role.SUPER_ADMIN)
        response = self.client.get(ADMIN + f"users/{self.users[Role.CUSTOMER].uuid}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(response.json()["data"]),
            {
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
            },
        )
        self.assertNotIn(self.users[Role.CUSTOMER].password, response.content.decode())

    def test_current_permissions_and_identity_are_own_account_only(self) -> None:
        for role in Role.values:
            self.authorize(role)
            response = self.client.get(AUTH + "permissions/")
            self.assertEqual(response.status_code, 200)
            data = response.json()["data"]
            self.assertEqual(data["role"], role)
            expected = (
                set(Permission)
                if role in {Role.SUPER_ADMIN, Role.ADMIN}
                else STAFF_GRANTS
                if role == Role.STAFF
                else set()
            )
            self.assertEqual(set(data["permissions"]), expected)
            own = self.client.get(AUTH + f"me/?uuid={self.users[Role.SUPER_ADMIN].uuid}")
            self.assertEqual(own.json()["data"]["uuid"], str(self.users[role].uuid))

    def test_admin_can_manage_staff_and_customers_but_cannot_assign_admin_roles(self) -> None:
        self.authorize(Role.ADMIN)
        subject = self.users[Role.CUSTOMER]
        for role in (Role.STAFF, Role.CUSTOMER):
            response = self.client.patch(self.access_url(subject), {"role": role})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["data"]["role"], role)
        for role in (Role.ADMIN, Role.SUPER_ADMIN):
            self.assertEqual(
                self.client.patch(self.access_url(subject), {"role": role}).status_code, 403
            )
        for active in (False, True):
            response = self.client.patch(
                self.access_url(self.users[Role.STAFF]), {"is_active": active}
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["data"]["is_active"], active)

    def test_super_admin_can_assign_all_roles_and_flags_follow_roles(self) -> None:
        self.authorize(Role.SUPER_ADMIN)
        subject = self.users[Role.CUSTOMER]
        for role in Role.values:
            response = self.client.patch(self.access_url(subject), {"role": role})
            self.assertEqual(response.status_code, 200)
            subject.refresh_from_db()
            self.assertEqual(subject.role, role)
            self.assertEqual(subject.is_superuser, role == Role.SUPER_ADMIN)
            self.assertEqual(subject.is_staff, role in {Role.SUPER_ADMIN, Role.ADMIN})

    def test_self_access_changes_are_forbidden_including_last_super_admin(self) -> None:
        for role in (Role.SUPER_ADMIN, Role.ADMIN):
            self.authorize(role)
            for payload in (
                {"is_active": False},
                {"role": Role.CUSTOMER},
                {"role": Role.SUPER_ADMIN},
            ):
                self.assertEqual(
                    self.client.patch(self.access_url(self.users[role]), payload).status_code, 403
                )
        self.assertEqual(User.objects.filter(role=Role.SUPER_ADMIN, is_active=True).count(), 1)

    def test_access_update_validates_fields_and_rejects_mass_assignment(self) -> None:
        self.authorize(Role.SUPER_ADMIN)
        payloads: tuple[dict[str, object], ...] = (
            {},
            {"role": "owner"},
            {"role": "admin"},
            {"role": None},
            {"is_active": None},
            {"is_staff": True},
            {"is_superuser": True},
            {"groups": []},
            {"user_permissions": []},
            {"password": "sensitive"},
            {"email": "new@example.com"},
        )
        for payload in payloads:
            with self.subTest(fields=list(payload)):
                response = self.client.patch(self.access_url(self.users[Role.CUSTOMER]), payload)
                self.assertEqual(response.status_code, 400)
        self.assertFalse(AuditEvent.objects.filter(action="accounts.access_changed").exists())

    def test_access_changes_revoke_all_subject_tokens_and_audit_actor_and_changes(self) -> None:
        subject = self.users[Role.CUSTOMER]
        _, second_pair = login(email=subject.email, password=PASSWORD, client="mobile")
        self.authorize(Role.ADMIN)
        response = self.client.patch(self.access_url(subject), {"role": Role.STAFF})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(AuthSession.objects.filter(user=subject, revoked_at__isnull=True).exists())
        event = AuditEvent.objects.get(action="accounts.access_changed")
        self.assertEqual(event.actor, self.users[Role.ADMIN])
        self.assertEqual(event.subject_uuid, subject.uuid)
        self.assertEqual(str(event.request_id), response["X-Request-ID"])
        self.assertEqual(
            event.changes,
            {
                "role": {"before": Role.CUSTOMER, "after": Role.STAFF},
                "is_active": {"before": True, "after": True},
            },
        )
        for pair in (self.tokens[Role.CUSTOMER], second_pair):
            self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {pair['access']}")
            self.assertEqual(self.client.get(AUTH + "me/").status_code, 401)
            self.assertEqual(
                self.client.post(AUTH + "refresh/", {"refresh": pair["refresh"]}).status_code, 401
            )

    def test_deactivate_reactivate_does_not_restore_old_sessions(self) -> None:
        subject = self.users[Role.STAFF]
        self.authorize(Role.ADMIN)
        for active in (False, True):
            self.assertEqual(
                self.client.patch(self.access_url(subject), {"is_active": active}).status_code, 200
            )
            self.client.credentials(
                HTTP_AUTHORIZATION=f"Bearer {self.tokens[Role.STAFF]['access']}"
            )
            self.assertEqual(self.client.get(AUTH + "me/").status_code, 401)
            self.authorize(Role.ADMIN)
        self.assertEqual(
            self.client.post(
                AUTH + "login/", {"email": subject.email, "password": PASSWORD}
            ).status_code,
            200,
        )

    def test_noop_access_patch_does_not_revoke_or_create_audit_event(self) -> None:
        self.authorize(Role.ADMIN)
        response = self.client.patch(
            self.access_url(self.users[Role.CUSTOMER]), {"role": Role.CUSTOMER, "is_active": True}
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            AuthSession.objects.filter(
                user=self.users[Role.CUSTOMER], revoked_at__isnull=True
            ).exists()
        )
        self.assertFalse(AuditEvent.objects.filter(action="accounts.access_changed").exists())

    def test_service_rechecks_stale_actor_and_hierarchy_without_a_view(self) -> None:
        actor = self.users[Role.ADMIN]
        User.objects.filter(pk=actor.pk).update(role=Role.STAFF, is_staff=False)
        with self.assertRaises(PermissionDenied):
            update_user_access(
                actor=actor, subject_uuid=self.users[Role.CUSTOMER].uuid, role=Role.STAFF
            )
        with self.assertRaises(PermissionDenied):
            update_user_access(
                actor=self.users[Role.CUSTOMER], subject_uuid=actor.uuid, role=Role.SUPER_ADMIN
            )

    def test_failed_audit_rolls_back_access_and_revocation(self) -> None:
        subject = self.users[Role.CUSTOMER]
        with (
            patch(
                "accounts.access_services.record_access_change",
                side_effect=RuntimeError("audit unavailable"),
            ),
            self.assertRaises(RuntimeError),
        ):
            update_user_access(
                actor=self.users[Role.ADMIN], subject_uuid=subject.uuid, role=Role.STAFF
            )
        subject.refresh_from_db()
        self.assertEqual(subject.role, Role.CUSTOMER)
        self.assertTrue(AuthSession.objects.filter(user=subject, revoked_at__isnull=True).exists())

    def test_inactive_roles_cannot_access_admin_even_with_existing_tokens(self) -> None:
        for role in Role.values:
            User.objects.filter(pk=self.users[role].pk).update(is_active=False)
            self.authorize(role)
            self.assertEqual(self.client.get(ADMIN + "users/").status_code, 401)
            self.assertEqual(self.client.get(AUTH + "permissions/").status_code, 401)

    def test_jwt_role_claim_and_headers_cannot_escalate_customer(self) -> None:
        token = AccessToken()
        original = AccessToken(self.tokens[Role.CUSTOMER]["access"])
        token["user_id"], token["sid"] = original["user_id"], original["sid"]
        token["role"] = Role.SUPER_ADMIN
        token["permissions"] = list(Permission)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}", HTTP_X_ROLE=Role.SUPER_ADMIN)
        self.assertEqual(self.client.get(ADMIN + "users/").status_code, 403)

    def test_each_request_reads_current_database_role_instead_of_cached_claims(self) -> None:
        self.authorize(Role.ADMIN)
        self.assertEqual(self.client.get(ADMIN + "users/").status_code, 200)
        User.objects.filter(pk=self.users[Role.ADMIN].pk).update(role=Role.STAFF, is_staff=False)
        self.assertEqual(self.client.get(ADMIN + "users/").status_code, 403)
        self.assertEqual(self.client.get(AUTH + "permissions/").json()["data"]["role"], Role.STAFF)

    def test_django_groups_and_permissions_do_not_bypass_platform_policy(self) -> None:
        subject = self.users[Role.CUSTOMER]
        grant = DjangoPermission.objects.get(
            content_type__app_label="accounts", codename="change_user"
        )
        group = Group.objects.create(name="legacy administrators")
        group.permissions.add(grant)
        subject.groups.add(group)
        subject.user_permissions.add(grant)
        self.authorize(Role.CUSTOMER)
        self.assertEqual(self.client.get(ADMIN + "users/").status_code, 403)
        self.assertEqual(
            self.client.patch(
                self.access_url(self.users[Role.STAFF]), {"role": Role.ADMIN}
            ).status_code,
            403,
        )

    def test_database_rejects_unknown_roles_and_conflicting_django_flags(self) -> None:
        for fields in (
            {"role": "owner"},
            {"is_staff": True},
            {"is_superuser": True},
            {"role": Role.SUPER_ADMIN},
        ):
            with self.assertRaises(IntegrityError), transaction.atomic():
                User.objects.filter(pk=self.users[Role.CUSTOMER].pk).update(**fields)

    def test_unknown_inactive_or_forged_flag_objects_never_get_grants(self) -> None:
        for role, active in (("owner", True), (Role.ADMIN, False), (Role.CUSTOMER, True)):
            user = SimpleNamespace(
                is_authenticated=True, is_active=active, role=role, is_staff=True, is_superuser=True
            )
            for permission in Permission:
                self.assertFalse(has_permission(user, permission))
        self.assertFalse(has_permission(self.users[Role.SUPER_ADMIN], "unknown.view"))

    def test_audit_is_read_only_and_contains_only_reviewed_fields(self) -> None:
        self.authorize(Role.SUPER_ADMIN)
        response = self.client.get(ADMIN + "audit/")
        self.assertEqual(response.status_code, 200)
        for event in response.json()["data"]["results"]:
            self.assertEqual(
                set(event),
                {
                    "id",
                    "action",
                    "actor_uuid",
                    "entity_type",
                    "subject_uuid",
                    "previous_value",
                    "new_value",
                    "changes",
                    "ip_address",
                    "user_agent",
                    "request_id",
                    "created_at",
                },
            )
        for method in ("post", "patch", "delete"):
            self.assertEqual(getattr(self.client, method)(ADMIN + "audit/").status_code, 403)

    def test_production_admin_routes_all_use_explicit_domain_permissions(self) -> None:
        paths = []

        def inspect(patterns, prefix=""):
            for pattern in patterns:
                full_path = prefix + str(pattern.pattern)
                if isinstance(pattern, URLResolver):
                    inspect(pattern.url_patterns, full_path)
                elif isinstance(pattern, URLPattern) and full_path.startswith("api/v1/admin/"):
                    view = pattern.callback.cls
                    self.assertIn(HasDomainPermission, view.permission_classes)
                    self.assertTrue(view.required_permissions)
                    for permission in view.required_permissions.values():
                        self.assertIn(permission, Permission)
                    paths.append(full_path)

        inspect(get_resolver().url_patterns)
        self.assertEqual(len(paths), 38)


class ConcurrentAccessChangesTests(TransactionTestCase):
    def test_concurrent_super_admin_deactivations_leave_an_active_administrator(self) -> None:
        first = User.objects.create_superuser(email="first@example.com", password=PASSWORD)
        second = User.objects.create_superuser(email="second@example.com", password=PASSWORD)
        barrier = Barrier(2)

        def deactivate(actor_id: int, subject_uuid) -> str:
            close_old_connections()
            try:
                actor = User.objects.get(pk=actor_id)
                with connection.cursor() as cursor:
                    cursor.execute("SET statement_timeout = '10s'")
                barrier.wait(timeout=10)
                try:
                    update_user_access(actor=actor, subject_uuid=subject_uuid, is_active=False)
                    return "changed"
                except PermissionDenied:
                    return "denied"
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            tasks = [
                executor.submit(deactivate, first.pk, second.uuid),
                executor.submit(deactivate, second.pk, first.uuid),
            ]
            results = sorted(task.result(timeout=15) for task in tasks)
        self.assertEqual(results, ["changed", "denied"])
        self.assertEqual(User.objects.filter(role=Role.SUPER_ADMIN, is_active=True).count(), 1)
        self.assertEqual(AuditEvent.objects.filter(action="accounts.access_changed").count(), 1)
