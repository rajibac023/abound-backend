import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import StringIO
from threading import Barrier
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import jwt
from django.conf import settings
from django.contrib.auth.hashers import check_password, identify_hasher
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.core.management import call_command
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed, Throttled
from rest_framework.test import APIClient, APITestCase
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import AuthRateLimit, AuthSession, User
from accounts.roles import Role
from accounts.services import login, refresh_session
from accounts.throttles import consume_limit
from accounts.tokens import SessionRefreshToken
from audit.models import AuditEvent
from common.logging import JSONFormatter, RequestIDFilter
from notifications.models import PasswordResetDelivery
from notifications.services import deliver_one_password_reset

PASSWORD = "Violet-River!Lantern-284"
NEW_PASSWORD = "Silver-Valley!Compass-937"
AUTH = "/api/v1/auth/"


def claims(token: str) -> dict:
    # Only inspect tokens in tests; request authentication always verifies signatures.
    return jwt.decode(token, options={"verify_signature": False})


class AuthTests(APITestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(
            email="renter@example.com", password=PASSWORD, first_name="Renter", last_name="Example"
        )

    def token_pair(self, **changes):
        payload = {"email": self.user.email, "password": PASSWORD, **changes}
        response = self.client.post(AUTH + "login/", payload)
        self.assertEqual(response.status_code, 200, response.json())
        return response.json()["data"]

    def authorize(self, access: str) -> None:
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    def registration(self, **changes):
        return {
            "email": "new@example.com",
            "password": PASSWORD,
            "password_confirm": PASSWORD,
            "first_name": "New",
            "last_name": "Customer",
            "phone": "+9779800000000",
            **changes,
        }

    def verified_registration(self, **changes):
        payload = self.registration(**changes)
        response = self.client.post(AUTH + "register/code/", payload)
        self.assertEqual(response.status_code, 202, response.json())
        return {
            **payload,
            "verification_id": response.json()["data"]["verification_id"],
            "code": re.search(r"\b[0-9]{6}\b", mail.outbox[-1].body).group(),
        }

    def test_registration_persists_core_fields_without_credentials(self) -> None:
        response = self.client.post(
            AUTH + "register/", self.verified_registration(email=" NEW@Example.COM ")
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()["data"]
        self.assertEqual(
            set(data),
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
        user = User.objects.get(email="new@example.com")
        self.assertTrue(user.check_password(PASSWORD))
        self.assertNotEqual(user.password, PASSWORD)
        self.assertEqual(user.role, User.Role.CUSTOMER)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertIsNotNone(user.email_verified_at)
        self.assertEqual(str(user.uuid), data["uuid"])
        UUID(data["uuid"])
        self.assertNotIn(PASSWORD, response.content.decode())
        self.assertNotIn(user.password, response.content.decode())
        self.assertTrue(AuditEvent.objects.filter(action="auth.registered", actor=user).exists())

    def test_duplicate_email_is_case_insensitive(self) -> None:
        response = self.client.post(
            AUTH + "register/", self.registration(email="RENTER@example.com")
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("email", response.json()["errors"])
        self.assertEqual(User.objects.count(), 1)

    def test_database_rejects_duplicate_and_noncanonical_email(self) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.create_user(email=" RENTER@EXAMPLE.COM ", password=PASSWORD)
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.filter(pk=self.user.pk).update(email="RENTER@EXAMPLE.COM")

    def test_registration_rejects_privilege_injection(self) -> None:
        for field, value in [
            ("role", "admin"),
            ("is_staff", True),
            ("is_superuser", True),
            ("is_active", False),
        ]:
            with self.subTest(field=field):
                response = self.client.post(AUTH + "register/", self.registration(**{field: value}))
                self.assertEqual(response.status_code, 400)
        self.assertEqual(User.objects.count(), 1)

    def test_registration_password_validation(self) -> None:
        for password in ["short", "123456789012", "password1234", "new@example.com"]:
            with self.subTest(password_kind=len(password)):
                response = self.client.post(
                    AUTH + "register/code/",
                    self.registration(
                        password=password,
                        password_confirm=password,
                    ),
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn("password", response.json()["errors"])

    def test_registration_rejects_mismatch_and_excessive_length(self) -> None:
        for payload in [
            self.registration(password_confirm="different"),
            self.registration(password="a" * 129),
        ]:
            response = self.client.post(AUTH + "register/", payload)
            self.assertEqual(response.status_code, 400)

    def test_registration_validates_email(self) -> None:
        response = self.client.post(AUTH + "register/", self.registration(email="invalid"))
        self.assertEqual(response.status_code, 400)

    @override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.PBKDF2PasswordHasher"])
    def test_registration_uses_production_password_hashing(self) -> None:
        response = self.client.post(AUTH + "register/", self.verified_registration())
        self.assertEqual(response.status_code, 201)
        encoded = User.objects.get(email="new@example.com").password
        self.assertEqual(identify_hasher(encoded).algorithm, "pbkdf2_sha256")
        self.assertTrue(check_password(PASSWORD, encoded))

    def test_login_normalizes_email_and_returns_expiring_session_tokens(self) -> None:
        data = self.token_pair(email=" RENTER@EXAMPLE.COM ")
        access, refresh = claims(data["access"]), claims(data["refresh"])
        self.assertEqual(access["user_id"], str(self.user.uuid))
        self.assertEqual(access["sid"], refresh["sid"])
        self.assertEqual(access["token_type"], "access")
        self.assertEqual(refresh["token_type"], "refresh")
        self.assertGreater(refresh["exp"], access["exp"])
        self.assertEqual(access["exp"], data["access_expires_at"])
        self.assertNotIn("email", access)
        self.assertNotIn("password", access)
        session = AuthSession.objects.get(pk=access["sid"])
        self.assertNotEqual(session.refresh_digest, data["refresh"])
        self.assertNotEqual(session.password_fingerprint, self.user.password)

    def test_invalid_password_unknown_user_and_inactive_user_have_same_error(self) -> None:
        responses = []
        for email, password in [(self.user.email, "wrong"), ("missing@example.com", PASSWORD)]:
            responses.append(
                self.client.post(AUTH + "login/", {"email": email, "password": password})
            )
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        responses.append(
            self.client.post(AUTH + "login/", {"email": self.user.email, "password": PASSWORD})
        )
        for response in responses:
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json(), responses[0].json())
            self.assertNotIn("access", response.json().get("errors", {}))
        self.assertEqual(AuthSession.objects.count(), 0)

    def test_me_requires_a_valid_token(self) -> None:
        self.assertEqual(self.client.get(AUTH + "me/").status_code, 401)
        data = self.token_pair()
        self.authorize(data["access"])
        response = self.client.get(AUTH + "me/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["email"], self.user.email)
        self.assertNotIn("password", response.json()["data"])
        self.assertNotIn("refresh", response.json()["data"])

    def test_me_cannot_update_role_or_email(self) -> None:
        self.authorize(self.token_pair()["access"])
        for payload in ({"role": "ADMIN"}, {"email": "new@example.com"}):
            self.assertEqual(self.client.patch(AUTH + "me/", payload).status_code, 400)
        self.user.refresh_from_db()
        self.assertEqual(self.user.role, Role.CUSTOMER)

    def test_refresh_rotates_and_replay_revokes_the_session(self) -> None:
        initial = self.token_pair()
        response = self.client.post(AUTH + "refresh/", {"refresh": initial["refresh"]})
        self.assertEqual(response.status_code, 200)
        rotated = response.json()["data"]
        self.assertNotEqual(initial["refresh"], rotated["refresh"])
        self.authorize(rotated["access"])
        self.assertEqual(self.client.get(AUTH + "me/").status_code, 200)
        replay = self.client.post(AUTH + "refresh/", {"refresh": initial["refresh"]})
        self.assertEqual(replay.status_code, 401)
        self.assertEqual(self.client.get(AUTH + "me/").status_code, 401)
        self.assertEqual(
            self.client.post(AUTH + "refresh/", {"refresh": rotated["refresh"]}).status_code, 401
        )
        self.assertTrue(AuditEvent.objects.filter(action="auth.refresh_replay").exists())

    def test_logout_revokes_access_and_refresh_for_only_current_session(self) -> None:
        first, other = self.token_pair(), self.token_pair()
        self.authorize(first["access"])
        self.assertEqual(self.client.post(AUTH + "logout/", {}).status_code, 200)
        self.assertEqual(self.client.get(AUTH + "me/").status_code, 401)
        self.assertEqual(
            self.client.post(AUTH + "refresh/", {"refresh": first["refresh"]}).status_code, 401
        )
        self.authorize(other["access"])
        self.assertEqual(self.client.get(AUTH + "me/").status_code, 200)

    def test_inactive_user_cannot_use_access_or_refresh(self) -> None:
        tokens = self.token_pair()
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        self.authorize(tokens["access"])
        self.assertEqual(self.client.get(AUTH + "me/").status_code, 401)
        self.assertEqual(
            self.client.post(AUTH + "refresh/", {"refresh": tokens["refresh"]}).status_code, 401
        )

    def test_unknown_expired_and_revoked_sessions_are_rejected(self) -> None:
        for change in ["expired", "revoked", "missing"]:
            with self.subTest(change=change):
                tokens = self.token_pair()
                session = AuthSession.objects.get(pk=claims(tokens["access"])["sid"])
                if change == "missing":
                    session.delete()
                elif change == "expired":
                    session.expires_at = timezone.now() - timedelta(seconds=1)
                    session.save(update_fields=["expires_at"])
                else:
                    session.revoked_at = timezone.now()
                    session.save(update_fields=["revoked_at"])
                self.authorize(tokens["access"])
                self.assertEqual(self.client.get(AUTH + "me/").status_code, 401)
                self.assertEqual(
                    self.client.post(AUTH + "refresh/", {"refresh": tokens["refresh"]}).status_code,
                    401,
                )

    def test_refresh_rejects_access_tokens_malformed_tokens_and_bad_signatures(self) -> None:
        tokens = self.token_pair()
        forged = jwt.encode(
            claims(tokens["refresh"]), "incorrect-test-signing-key" * 3, algorithm="HS256"
        )
        for value in [tokens["access"], "invalid-token", forged]:
            response = self.client.post(AUTH + "refresh/", {"refresh": value})
            self.assertEqual(response.status_code, 401)
            self.assertNotIn(value, response.content.decode())

    def test_access_rejects_expiration_missing_sid_and_wrong_type(self) -> None:
        data = self.token_pair()
        bad = []
        for variant in ["expired", "missing_sid", "malformed_sid"]:
            token = AccessToken()
            token["user_id"] = str(self.user.uuid)
            if variant == "expired":
                token["sid"] = claims(data["access"])["sid"]
                token.set_exp(lifetime=timedelta(seconds=-1))
            if variant == "malformed_sid":
                token["sid"] = {"invalid": "claim"}
            bad.append(str(token))
        for value in [*bad, data["refresh"]]:
            self.authorize(value)
            self.assertEqual(self.client.get(AUTH + "me/").status_code, 401)

    def test_expired_refresh_is_rejected(self) -> None:
        token = SessionRefreshToken()
        token["user_id"] = str(self.user.uuid)
        token["sid"] = str(uuid4())
        token.set_exp(lifetime=timedelta(seconds=-1))
        self.assertEqual(
            self.client.post(AUTH + "refresh/", {"refresh": str(token)}).status_code, 401
        )

    def test_password_change_revokes_all_sessions_and_updates_hash(self) -> None:
        first, second = self.token_pair(), self.token_pair()
        self.authorize(first["access"])
        response = self.client.post(
            AUTH + "password/change/",
            {
                "current_password": PASSWORD,
                "password": NEW_PASSWORD,
                "password_confirm": NEW_PASSWORD,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(NEW_PASSWORD))
        for tokens in [first, second]:
            self.authorize(tokens["access"])
            self.assertEqual(self.client.get(AUTH + "me/").status_code, 401)
            self.assertEqual(
                self.client.post(AUTH + "refresh/", {"refresh": tokens["refresh"]}).status_code, 401
            )

    def test_password_change_requires_current_password_and_valid_new_password(self) -> None:
        self.authorize(self.token_pair()["access"])
        for current, new in [("wrong", NEW_PASSWORD), (PASSWORD, "weak")]:
            response = self.client.post(
                AUTH + "password/change/",
                {
                    "current_password": current,
                    "password": new,
                    "password_confirm": new,
                },
            )
            self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(PASSWORD))

    def test_password_updated_outside_auth_services_still_invalidates_tokens(self) -> None:
        tokens = self.token_pair()
        self.user.set_password(NEW_PASSWORD)
        self.user.save(update_fields=["password"])
        self.authorize(tokens["access"])
        self.assertEqual(self.client.get(AUTH + "me/").status_code, 401)
        self.assertEqual(
            self.client.post(AUTH + "refresh/", {"refresh": tokens["refresh"]}).status_code, 401
        )

    def test_auth_responses_are_not_cacheable_including_errors(self) -> None:
        for response in [self.client.get(AUTH + "me/"), self.client.post(AUTH + "login/", {})]:
            self.assertEqual(response["Cache-Control"], "no-store")
            self.assertEqual(response["Pragma"], "no-cache")

    def test_unexpected_auth_errors_do_not_log_or_return_credentials(self) -> None:
        marker = "private-auth-password-token-marker"
        with (
            patch("accounts.services.login", side_effect=RuntimeError(marker)),
            self.assertLogs("common.exceptions", level="ERROR") as captured,
        ):
            response = self.client.post(
                AUTH + "login/", {"email": self.user.email, "password": PASSWORD}
            )
        self.assertEqual(response.status_code, 500)
        self.assertNotIn(marker, response.content.decode())
        self.assertNotIn(marker, " ".join(captured.output))
        self.assertNotIn(PASSWORD, " ".join(captured.output))

    def test_dev_access_log_redacts_auth_query_credentials_and_handles_socket_request(self) -> None:
        record = logging.makeLogRecord(
            {
                "msg": '"GET /api/v1/auth/me/?token=private-marker HTTP/1.1" 401',
                "request": object(),
            }
        )
        RequestIDFilter().filter(record)
        self.assertNotIn("private-marker", JSONFormatter().format(record))

    def test_ip_limit_cannot_be_bypassed_with_forwarded_headers(self) -> None:
        limits = {**settings.AUTH_RATE_LIMITS, "login_ip": (1, 60)}
        with override_settings(AUTH_RATE_LIMITS=limits):
            first = self.client.post(
                AUTH + "login/",
                {"email": "one@example.com", "password": "wrong"},
                HTTP_X_FORWARDED_FOR="1.2.3.4",
            )
            second = self.client.post(
                AUTH + "login/",
                {"email": "two@example.com", "password": "wrong"},
                HTTP_X_FORWARDED_FOR="5.6.7.8",
            )
        self.assertEqual(first.status_code, 401)
        self.assertEqual(second.status_code, 429)
        self.assertIn("Retry-After", second)

    def test_identity_limit_applies_across_client_addresses(self) -> None:
        limits = {**settings.AUTH_RATE_LIMITS, "login_identity": (1, 60)}
        with override_settings(AUTH_RATE_LIMITS=limits):
            first = self.client.post(
                AUTH + "login/",
                {"email": self.user.email, "password": "wrong"},
                REMOTE_ADDR="10.0.0.1",
            )
            second = self.client.post(
                AUTH + "login/",
                {"email": self.user.email.upper(), "password": "wrong"},
                REMOTE_ADDR="10.0.0.2",
            )
        self.assertEqual(first.status_code, 401)
        self.assertEqual(second.status_code, 429)
        self.assertNotIn(self.user.email, str(list(AuthRateLimit.objects.values())))

    def test_superuser_manager_uses_email_identity(self) -> None:
        admin = User.objects.create_superuser(email="ADMIN@example.com", password=PASSWORD)
        self.assertEqual(admin.email, "admin@example.com")
        self.assertEqual(admin.role, User.Role.SUPER_ADMIN)
        self.assertTrue(admin.is_staff and admin.is_superuser)
        with self.assertRaises(ValueError):
            User.objects.create_superuser(
                email="other@example.com", password=PASSWORD, is_staff=False
            )


class BrowserAuthTests(APITestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(email="browser@example.com", password=PASSWORD)
        self.client = APIClient(enforce_csrf_checks=True)

    def browser_login(self):
        csrf = self.client.get(AUTH + "csrf/", secure=True).json()["data"]["csrf_token"]
        self.client.credentials(HTTP_X_CSRFTOKEN=csrf, HTTP_REFERER="https://testserver/")
        response = self.client.post(
            AUTH + "login/",
            {
                "email": self.user.email,
                "password": PASSWORD,
                "client": "web",
            },
            secure=True,
        )
        self.assertEqual(response.status_code, 200, response.json())
        return csrf, response

    def test_web_login_requires_csrf(self) -> None:
        response = self.client.post(
            AUTH + "login/",
            {
                "email": self.user.email,
                "password": PASSWORD,
                "client": "web",
            },
            secure=True,
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(AuthSession.objects.count(), 0)

    def test_refresh_cookie_is_secure_httponly_and_absent_from_body(self) -> None:
        _, response = self.browser_login()
        cookie = response.cookies[settings.AUTH_REFRESH_COOKIE_NAME]
        self.assertTrue(cookie["httponly"])
        self.assertTrue(cookie["secure"])
        self.assertEqual(cookie["samesite"], "Strict")
        self.assertEqual(cookie["path"], "/api/v1/auth/")
        self.assertNotIn("refresh", response.json()["data"])

    def test_cookie_refresh_requires_csrf_and_rotates_cookie(self) -> None:
        csrf, response = self.browser_login()
        old = response.cookies[settings.AUTH_REFRESH_COOKIE_NAME].value
        self.client.credentials()
        self.assertEqual(self.client.post(AUTH + "refresh/", {}, secure=True).status_code, 403)
        self.client.credentials(HTTP_X_CSRFTOKEN=csrf, HTTP_REFERER="https://testserver/")
        refreshed = self.client.post(AUTH + "refresh/", {}, secure=True)
        self.assertEqual(refreshed.status_code, 200)
        self.assertNotEqual(refreshed.cookies[settings.AUTH_REFRESH_COOKIE_NAME].value, old)
        self.assertNotIn("refresh", refreshed.json()["data"])

    def test_web_refresh_token_cannot_switch_to_body_transport(self) -> None:
        _, response = self.browser_login()
        raw = response.cookies[settings.AUTH_REFRESH_COOKIE_NAME].value
        outsider = APIClient()
        self.assertEqual(outsider.post(AUTH + "refresh/", {"refresh": raw}).status_code, 401)

    def test_browser_logout_clears_cookie_and_revokes_access(self) -> None:
        csrf, logged_in = self.browser_login()
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {logged_in.json()['data']['access']}",
            HTTP_X_CSRFTOKEN=csrf,
            HTTP_REFERER="https://testserver/",
        )
        response = self.client.post(AUTH + "logout/", {}, secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.cookies[settings.AUTH_REFRESH_COOKIE_NAME]["max-age"], 0)
        self.assertEqual(self.client.get(AUTH + "me/", secure=True).status_code, 401)


class PasswordResetTests(APITestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(email="reset@example.com", password=PASSWORD)

    def request_reset(self, email=None):
        return self.client.post(AUTH + "password/reset/", {"email": email or self.user.email})

    def confirm(self, token, **changes):
        return self.client.post(
            AUTH + "password/reset/confirm/",
            {
                "uuid": str(self.user.uuid),
                "token": token,
                "password": NEW_PASSWORD,
                "password_confirm": NEW_PASSWORD,
                **changes,
            },
        )

    def test_known_unknown_and_inactive_accounts_get_same_response(self) -> None:
        known = self.request_reset()
        unknown = self.request_reset("unknown@example.com")
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        inactive = self.request_reset()
        self.assertEqual(known.status_code, 202)
        self.assertEqual(known.json(), unknown.json())
        self.assertEqual(known.json(), inactive.json())
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(PasswordResetDelivery.objects.count(), 3)
        self.assertEqual(PasswordResetDelivery.objects.filter(user__isnull=True).count(), 2)

    def test_delivery_uses_configured_url_fragment_and_prints_no_token(self) -> None:
        self.request_reset()
        output = StringIO()
        call_command("deliver_password_resets", stdout=output)
        self.assertEqual(len(mail.outbox), 1)
        link = next(
            line for line in mail.outbox[0].body.splitlines() if line.startswith("https://")
        )
        parsed = urlsplit(link)
        self.assertEqual(parsed.hostname, "app.example.com")
        self.assertEqual(parsed.query, "")
        fragment = parse_qs(parsed.fragment)
        token = fragment["token"][0]
        self.assertEqual(fragment["uuid"][0], str(self.user.uuid))
        self.assertNotIn(token, output.getvalue())
        self.assertNotIn(token, str(list(PasswordResetDelivery.objects.values())))
        self.assertEqual(self.confirm(token).status_code, 200)

    def test_reset_is_single_use_and_revokes_all_sessions(self) -> None:
        _, tokens = login(email=self.user.email, password=PASSWORD, client="mobile")
        self.user.refresh_from_db()
        token = default_token_generator.make_token(self.user)
        self.assertEqual(self.confirm(token).status_code, 200)
        self.assertEqual(self.confirm(token).status_code, 400)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        self.assertEqual(self.client.get(AUTH + "me/").status_code, 401)
        self.assertEqual(
            self.client.post(AUTH + "refresh/", {"refresh": tokens["refresh"]}).status_code, 401
        )
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(NEW_PASSWORD))

    def test_reset_rejects_invalid_expired_and_wrong_user_tokens(self) -> None:
        token = default_token_generator.make_token(self.user)
        self.assertEqual(self.confirm("invalid").status_code, 400)
        self.assertEqual(self.confirm(token, uuid=str(uuid4())).status_code, 400)
        future = default_token_generator._now() + timedelta(
            seconds=settings.PASSWORD_RESET_TIMEOUT + 1
        )
        with patch.object(default_token_generator, "_now", return_value=future):
            self.assertEqual(self.confirm(token).status_code, 400)

    def test_inactive_account_cannot_reset_password(self) -> None:
        token = default_token_generator.make_token(self.user)
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        self.assertEqual(self.confirm(token).status_code, 400)

    def test_reset_validates_new_password_without_consuming_valid_link(self) -> None:
        token = default_token_generator.make_token(self.user)
        self.assertEqual(
            self.confirm(token, password="weak", password_confirm="weak").status_code, 400
        )
        self.assertEqual(self.confirm(token).status_code, 200)

    def test_unknown_and_stale_delivery_jobs_send_nothing(self) -> None:
        self.request_reset("unknown@example.com")
        self.request_reset()
        self.user.set_password(NEW_PASSWORD)
        self.user.save(update_fields=["password"])
        call_command("deliver_password_resets", stdout=StringIO())
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(PasswordResetDelivery.objects.filter(status="skipped").count(), 2)

    def test_delivery_failures_are_bounded_and_do_not_log_credentials(self) -> None:
        self.request_reset()
        with (
            override_settings(AUTH_RESET_MAX_ATTEMPTS=1),
            patch(
                "notifications.services.send_mail", side_effect=RuntimeError("secret-smtp-password")
            ),
            self.assertLogs("notifications.services", level="WARNING") as captured,
        ):
            self.assertTrue(deliver_one_password_reset())
        self.assertNotIn("secret-smtp-password", " ".join(captured.output))
        self.assertEqual(PasswordResetDelivery.objects.get().status, "failed")

    def test_cleanup_preserves_unexpired_sessions_and_audit(self) -> None:
        login(email=self.user.email, password=PASSWORD, client="mobile")
        AuthRateLimit.objects.create(key="old", expires_at=timezone.now() - timedelta(seconds=1))
        self.request_reset()
        PasswordResetDelivery.objects.update(expires_at=timezone.now() - timedelta(days=40))
        call_command("cleanup_auth", stdout=StringIO())
        self.assertFalse(AuthRateLimit.objects.filter(key="old").exists())
        self.assertEqual(AuthSession.objects.count(), 1)
        self.assertEqual(PasswordResetDelivery.objects.count(), 0)
        self.assertTrue(AuditEvent.objects.exists())


class AuthenticationConcurrencyTests(TransactionTestCase):
    def test_concurrent_refresh_cannot_issue_two_surviving_token_pairs(self) -> None:
        user = User.objects.create_user(email="concurrent@example.com", password=PASSWORD)
        _, tokens = login(email=user.email, password=PASSWORD, client="mobile")
        barrier = Barrier(2)

        def attempt() -> int:
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                refresh_session(raw_token=tokens["refresh"], client="mobile")
                return 200
            except AuthenticationFailed:
                return 401
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(lambda _: attempt(), range(2)))
        self.assertEqual(sorted(outcomes), [200, 401])
        self.assertIsNotNone(AuthSession.objects.get().revoked_at)

    def test_concurrent_rate_limit_attempts_use_atomic_counter(self) -> None:
        barrier = Barrier(2)

        def attempt() -> bool:
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                consume_limit("login_identity", "same-identity")
                return True
            except Throttled:
                return False
            finally:
                connection.close()

        with (
            override_settings(AUTH_RATE_LIMITS={"login_identity": (1, 60)}),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            outcomes = list(executor.map(lambda _: attempt(), range(2)))
        self.assertEqual(sorted(outcomes), [False, True])
        self.assertEqual(AuthRateLimit.objects.get().count, 2)
