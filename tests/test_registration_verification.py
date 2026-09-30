import re
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.core import mail
from django.core.management import call_command
from django.db import close_old_connections
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from accounts.models import AuthRateLimit, AuthSession, RegistrationVerification, User
from audit.models import AuditEvent

AUTH = "/api/v1/auth/"
PASSWORD = "Violet-River!Lantern-284"
DETAILS = {
    "email": "new@example.com",
    "first_name": "New",
    "last_name": "Customer",
    "phone": "+9779800000000",
    "password": PASSWORD,
    "password_confirm": PASSWORD,
}


def emailed_code() -> str:
    match = re.search(r"\b[0-9]{6}\b", mail.outbox[-1].body)
    assert match
    return match.group()


class RegistrationVerificationTests(APITestCase):
    def request_code(self, **changes):
        response = self.client.post(AUTH + "register/code/", {**DETAILS, **changes})
        self.assertEqual(response.status_code, 202, response.json())
        return response.json()["data"]

    def final_payload(self, challenge, **changes):
        return {
            **DETAILS,
            "verification_id": challenge["verification_id"],
            "code": emailed_code(),
            **changes,
        }

    def allow_resend(self):
        RegistrationVerification.objects.update(sent_at=timezone.now() - timedelta(minutes=2))

    def test_sends_branded_email_without_creating_account_or_exposing_code(self):
        challenge = self.request_code(email=" NEW@Example.COM ")
        self.assertEqual(challenge["email"], "new@example.com")
        self.assertEqual(challenge["expires_in"], settings.AUTH_REGISTRATION_CODE_SECONDS)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["new@example.com"])
        self.assertIn("Abound Holidays", message.subject)
        self.assertIn(emailed_code(), message.alternatives[0].content)
        self.assertFalse(User.objects.exists())
        self.assertFalse(AuthSession.objects.exists())
        row = RegistrationVerification.objects.get()
        self.assertEqual(len(row.code_digest), 64)
        self.assertNotEqual(row.code_digest, emailed_code())
        self.assertNotIn("code", challenge)
        self.assertNotIn(PASSWORD, str(row.__dict__))

    def test_registration_requires_verification_even_when_called_directly(self):
        response = self.client.post(AUTH + "register/", DETAILS)
        self.assertEqual(response.status_code, 400)
        self.assertIn("verification_id", response.json()["errors"])
        self.assertIn("code", response.json()["errors"])
        self.assertFalse(User.objects.exists())

    def test_valid_code_creates_verified_customer_and_cannot_be_reused(self):
        challenge = self.request_code()
        payload = self.final_payload(challenge)
        response = self.client.post(AUTH + "register/", payload)
        self.assertEqual(response.status_code, 201, response.json())
        user = User.objects.get()
        self.assertIsNotNone(user.email_verified_at)
        self.assertTrue(user.check_password(PASSWORD))
        self.assertEqual(user.phone, DETAILS["phone"])
        self.assertFalse(AuthSession.objects.exists())
        self.assertTrue(
            AuditEvent.objects.filter(action="auth.email_verified", actor=user).exists()
        )
        row = RegistrationVerification.objects.get()
        self.assertIsNotNone(row.consumed_at)
        self.assertEqual(row.code_digest, "")
        self.assertEqual(self.client.post(AUTH + "register/", payload).status_code, 400)
        self.assertEqual(User.objects.count(), 1)

    @patch("accounts.registration.secrets.randbelow", return_value=123)
    def test_leading_zero_code_is_preserved(self, _random):
        challenge = self.request_code()
        self.assertEqual(emailed_code(), "000123")
        response = self.client.post(AUTH + "register/", self.final_payload(challenge))
        self.assertEqual(response.status_code, 201)

    def test_code_is_bound_to_email_and_challenge_id(self):
        challenge = self.request_code()
        for change in [{"email": "different@example.com"}, {"verification_id": str(uuid4())}]:
            response = self.client.post(AUTH + "register/", self.final_payload(challenge, **change))
            self.assertEqual(response.status_code, 400)
            self.assertIn("code", response.json()["errors"])
        self.assertFalse(User.objects.exists())

    def test_incorrect_attempts_persist_and_eventually_block_even_correct_code(self):
        challenge = self.request_code()
        wrong = "000000" if emailed_code() != "000000" else "000001"
        for attempt in range(1, settings.AUTH_REGISTRATION_MAX_ATTEMPTS + 1):
            response = self.client.post(
                AUTH + "register/", self.final_payload(challenge, code=wrong)
            )
            self.assertEqual(response.status_code, 400)
            self.assertEqual(RegistrationVerification.objects.get().failed_attempts, attempt)
        response = self.client.post(AUTH + "register/", self.final_payload(challenge))
        self.assertIn("Too many", response.json()["errors"]["code"][0])
        self.assertFalse(User.objects.exists())

    def test_expired_code_cannot_create_account(self):
        challenge = self.request_code()
        RegistrationVerification.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        response = self.client.post(AUTH + "register/", self.final_payload(challenge))
        self.assertEqual(response.status_code, 400)
        self.assertIn("expired", response.json()["errors"]["code"][0])
        self.assertFalse(User.objects.exists())

    def test_resend_cooldown_and_replacement_of_old_code(self):
        with patch("accounts.registration.secrets.randbelow", return_value=123456):
            challenge = self.request_code()
        old_payload = self.final_payload(challenge)
        response = self.client.post(AUTH + "register/code/", DETAILS)
        self.assertEqual(response.status_code, 429)
        self.assertGreater(int(response["Retry-After"]), 0)
        self.assertEqual(len(mail.outbox), 1)
        self.allow_resend()
        with patch("accounts.registration.secrets.randbelow", return_value=654321):
            replacement = self.request_code()
        self.assertEqual(RegistrationVerification.objects.count(), 1)
        self.assertEqual(self.client.post(AUTH + "register/", old_payload).status_code, 400)
        self.assertEqual(
            self.client.post(AUTH + "register/", self.final_payload(replacement)).status_code, 201
        )

    def test_delivery_failure_is_recoverable_and_never_leaks_provider_error(self):
        with patch(
            "accounts.registration.EmailMultiAlternatives.send",
            side_effect=RuntimeError("secret-smtp-password"),
        ):
            response = self.client.post(AUTH + "register/code/", DETAILS)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("secret-smtp-password", response.content.decode())
        self.assertFalse(RegistrationVerification.objects.exists())
        self.assertFalse(User.objects.exists())
        self.request_code()

    def test_failed_resend_keeps_previous_code_valid(self):
        challenge = self.request_code()
        payload = self.final_payload(challenge)
        self.allow_resend()
        with patch("accounts.registration.EmailMultiAlternatives.send", return_value=0):
            response = self.client.post(AUTH + "register/code/", DETAILS)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.client.post(AUTH + "register/", payload).status_code, 201)

    @override_settings(
        AUTH_RATE_LIMITS={**settings.AUTH_RATE_LIMITS, "register_code_identity": (2, 3600)}
    )
    def test_email_request_limit_applies_across_ip_addresses(self):
        self.request_code()
        self.allow_resend()
        self.request_code()
        self.allow_resend()
        response = self.client.post(AUTH + "register/code/", DETAILS, REMOTE_ADDR="192.0.2.5")
        self.assertEqual(response.status_code, 429)
        self.assertEqual(len(mail.outbox), 2)
        self.assertNotIn(DETAILS["email"], str(list(AuthRateLimit.objects.values())))

    @override_settings(
        AUTH_RATE_LIMITS={**settings.AUTH_RATE_LIMITS, "register_code_ip": (1, 3600)}
    )
    def test_email_request_ip_limit(self):
        self.request_code()
        response = self.client.post(
            AUTH + "register/code/", {**DETAILS, "email": "other@example.com"}
        )
        self.assertEqual(response.status_code, 429)

    def test_invalid_details_do_not_send_email(self):
        for change in [
            {"phone": "9800000000"},
            {"phone": "+" + "1" * 16},
            {"password": "short"},
            {"role": "ADMIN"},
        ]:
            response = self.client.post(AUTH + "register/code/", {**DETAILS, **change})
            self.assertEqual(response.status_code, 400)
        self.assertFalse(RegistrationVerification.objects.exists())

    def test_optional_phone_and_cleanup_of_expired_challenges(self):
        self.request_code(phone="")
        RegistrationVerification.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        call_command("cleanup_auth", verbosity=0)
        self.assertFalse(RegistrationVerification.objects.exists())


class ConcurrentRegistrationTests(TransactionTestCase):
    def test_simultaneous_verifications_create_one_account(self):
        client = APIClient()
        response = client.post(AUTH + "register/code/", DETAILS)
        self.assertEqual(response.status_code, 202)
        payload = {
            **DETAILS,
            "verification_id": response.json()["data"]["verification_id"],
            "code": emailed_code(),
        }
        barrier = Barrier(2)

        def verify():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return APIClient().post(AUTH + "register/", payload).status_code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(lambda _: verify(), range(2)))
        self.assertEqual(sorted(statuses), [201, 400])
        self.assertEqual(User.objects.count(), 1)
