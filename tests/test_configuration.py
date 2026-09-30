import json
import os
import subprocess
import sys
from unittest.mock import patch

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from config.environment import BASE_DIR, positive_integer, required_secret, timezone_name

TEST_DJANGO_SECRET = "config-test-django-secret-5f06cd1e599241f78f3b5ce456f483ce4bc7"


class ConfigurationTests(SimpleTestCase):
    def test_postgresql_and_custom_user_are_configured(self) -> None:
        self.assertEqual(settings.DATABASES["default"]["ENGINE"], "django.db.backends.postgresql")
        self.assertEqual(settings.AUTH_USER_MODEL, "accounts.User")
        self.assertTrue(settings.USE_TZ)

    def test_domain_apps_are_registered(self) -> None:
        for name in [
            "accounts",
            "vehicles",
            "pricing",
            "availability",
            "bookings",
            "maintenance",
            "customers",
            "notifications",
            "audit",
            "reports",
            "common",
        ]:
            with self.subTest(app=name):
                self.assertTrue(apps.is_installed(name))

    def test_static_and_media_locations_are_separate(self) -> None:
        self.assertNotEqual(settings.STATIC_ROOT, settings.MEDIA_ROOT)
        self.assertEqual(settings.STATIC_URL, "/static/")
        self.assertEqual(settings.MEDIA_URL, "/media/")

    def test_nonpositive_environment_values_fail(self) -> None:
        with (
            patch.dict(os.environ, {"JWT_ACCESS_MINUTES": "0"}),
            self.assertRaises(ImproperlyConfigured),
        ):
            positive_integer("JWT_ACCESS_MINUTES", default=5)

    def test_invalid_timezone_fails(self) -> None:
        with (
            patch.dict(os.environ, {"DJANGO_TIME_ZONE": "Invalid/Timezone"}),
            self.assertRaises(ImproperlyConfigured),
        ):
            timezone_name()

    def test_placeholder_secret_fails(self) -> None:
        with (
            patch.dict(os.environ, {"DJANGO_SECRET_KEY": "change-me-" + "a" * 70}),
            self.assertRaises(ImproperlyConfigured),
        ):
            required_secret("DJANGO_SECRET_KEY")


class ProductionConfigurationTests(SimpleTestCase):
    """Import production settings in fresh processes, without touching a live database."""

    def production_settings(self, **changes: str | None) -> subprocess.CompletedProcess[str]:
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(
                ("DJANGO_", "POSTGRES_", "JWT_", "CORS_", "CSRF_", "EMAIL_", "AUTH_")
            )
        }
        environment.update(
            {
                "DJANGO_SETTINGS_MODULE": "config.settings.production",
                "DJANGO_SECRET_KEY": TEST_DJANGO_SECRET,
                "JWT_SIGNING_KEY": "config-test-jwt-secret-56087545b08047078d714d368b27020f110a",
                "DJANGO_ALLOWED_HOSTS": "api.example.com",
                "POSTGRES_DB": "config_checks_only",
                "POSTGRES_USER": "abound_runtime",
                "POSTGRES_PASSWORD": "config-test-password",
                "POSTGRES_HOST": "database.example.com",
                "AUTH_PASSWORD_RESET_URL": "https://app.example.com/reset-password",
                "EMAIL_HOST": "smtp.example.com",
                "DEFAULT_FROM_EMAIL": "no-reply@example.com",
            }
        )
        for key, value in changes.items():
            if value is None:
                environment.pop(key, None)
            else:
                environment[key] = value
        code = """
import json
import django
django.setup()
from django.conf import settings
print(json.dumps({
    'debug': settings.DEBUG,
    'redirect': settings.SECURE_SSL_REDIRECT,
    'session_secure': settings.SESSION_COOKIE_SECURE,
    'csrf_secure': settings.CSRF_COOKIE_SECURE,
    'hsts': settings.SECURE_HSTS_SECONDS,
    'sslmode': settings.DATABASES['default']['OPTIONS']['sslmode'],
    'engine': settings.DATABASES['default']['ENGINE'],
    'proxy_trusted': getattr(settings, 'SECURE_PROXY_SSL_HEADER', None) is not None,
    'wildcard_cors': settings.CORS_ALLOW_ALL_ORIGINS,
}))
"""
        return subprocess.run(
            [sys.executable, "-c", code],
            cwd=BASE_DIR,
            env=environment,
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )

    def test_production_is_secure_even_when_debug_is_requested(self) -> None:
        result = self.production_settings(DJANGO_DEBUG="True")
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads(result.stdout)
        self.assertFalse(config["debug"])
        self.assertTrue(config["redirect"])
        self.assertTrue(config["session_secure"])
        self.assertTrue(config["csrf_secure"])
        self.assertGreater(config["hsts"], 0)
        self.assertEqual(config["sslmode"], "verify-full")
        self.assertEqual(config["engine"], "django.db.backends.postgresql")
        self.assertFalse(config["proxy_trusted"])
        self.assertFalse(config["wildcard_cors"])

    def test_missing_secret_fails_without_reading_local_env(self) -> None:
        result = self.production_settings(DJANGO_SECRET_KEY=None)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DJANGO_SECRET_KEY", result.stderr)

    def test_insecure_production_settings_are_rejected(self) -> None:
        invalid = [
            {"DJANGO_ALLOWED_HOSTS": ""},
            {"DJANGO_ALLOWED_HOSTS": "*"},
            {"POSTGRES_PASSWORD": ""},
            {"POSTGRES_SSLMODE": "disable"},
            {"POSTGRES_SSLMODE": "require"},
            {"CORS_ALLOWED_ORIGINS": "http://admin.example.com"},
            {"CSRF_TRUSTED_ORIGINS": "http://admin.example.com"},
            {"JWT_SIGNING_KEY": TEST_DJANGO_SECRET},
            {"AUTH_PASSWORD_RESET_URL": "http://app.example.com/reset-password"},
            {"AUTH_PASSWORD_RESET_URL": "https://app.example.com/reset-password?token=unsafe"},
            {"EMAIL_HOST": ""},
        ]
        for variables in invalid:
            with self.subTest(setting=next(iter(variables))):
                result = self.production_settings(**variables)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("ImproperlyConfigured", result.stderr)

    def test_proxy_header_requires_explicit_opt_in(self) -> None:
        result = self.production_settings(DJANGO_TRUST_PROXY_SSL_HEADER="True")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["proxy_trusted"])


class LocalEmailConfigurationTests(SimpleTestCase):
    """Local settings in fresh processes; every EMAIL_* key is set so .env cannot leak in."""

    def local_email(self, **changes: str) -> subprocess.CompletedProcess[str]:
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("DJANGO_", "JWT_", "EMAIL_", "DEFAULT_FROM_EMAIL"))
        }
        environment.update(
            {
                "DJANGO_SETTINGS_MODULE": "config.settings.local",
                "DJANGO_SECRET_KEY": TEST_DJANGO_SECRET,
                "JWT_SIGNING_KEY": "config-test-jwt-secret-56087545b08047078d714d368b27020f110a",
                "EMAIL_BACKEND": "",
                "EMAIL_HOST": "localhost",
                "EMAIL_HOST_USER": "",
                "EMAIL_HOST_PASSWORD": "",
                "EMAIL_USE_TLS": "False",
                "EMAIL_USE_SSL": "False",
                "DEFAULT_FROM_EMAIL": "",
                **changes,
            }
        )
        # An empty value means "not configured"; drop it so the settings default applies.
        for key in ("EMAIL_BACKEND", "DEFAULT_FROM_EMAIL", "EMAIL_PORT"):
            if not environment.get(key):
                environment.pop(key, None)
        code = """
import json
import django
django.setup()
from django.conf import settings
from accounts.checks import email_delivery_check
print(json.dumps({
    'backend': settings.EMAIL_BACKEND,
    'port': settings.EMAIL_PORT,
    'from': settings.DEFAULT_FROM_EMAIL,
    'warnings': [message.id for message in email_delivery_check(None)],
}))
"""
        return subprocess.run(
            [sys.executable, "-c", code],
            cwd=BASE_DIR,
            env=environment,
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )

    def test_without_smtp_credentials_mail_stays_in_memory_and_warns(self) -> None:
        result = self.local_email()
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads(result.stdout)
        self.assertEqual(config["backend"], "django.core.mail.backends.locmem.EmailBackend")
        self.assertEqual(config["warnings"], ["accounts.W001"])

    def test_smtp_credentials_enable_real_delivery(self) -> None:
        result = self.local_email(
            EMAIL_HOST="smtp.gmail.com",
            EMAIL_HOST_USER="team@example.com",
            EMAIL_HOST_PASSWORD="app-password",
            EMAIL_USE_SSL="True",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads(result.stdout)
        self.assertEqual(config["backend"], "django.core.mail.backends.smtp.EmailBackend")
        self.assertEqual(config["port"], 465)
        self.assertEqual(config["from"], "Abound Holidays <team@example.com>")
        self.assertEqual(config["warnings"], [])

    def test_contradictory_or_incomplete_smtp_settings_fail(self) -> None:
        for variables in [
            {"EMAIL_USE_TLS": "True", "EMAIL_USE_SSL": "True"},
            {"EMAIL_HOST_USER": "team@example.com"},
        ]:
            with self.subTest(**variables):
                result = self.local_email(**variables)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("ImproperlyConfigured", result.stderr)
