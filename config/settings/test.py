"""PostgreSQL test settings; never selected by a deployed entry point."""

from django.core.exceptions import ImproperlyConfigured

from config.settings.base import *  # noqa: F403

SECRET_KEY = "test-only-django-key-7bfe062189624adfb798fa2516ff0eb528cab990"
SIMPLE_JWT["SIGNING_KEY"] = "test-only-jwt-key-d22f1bd2fb2b4a908b4378e2fc564f85b7b90cf8"  # noqa: F405
DEBUG = False
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
AUTH_COOKIE_SECURE = True
AUTH_PASSWORD_RESET_URL = "https://app.example.com/reset-password"
# Fast, isolated test hashing; production retains Django's password hashers.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

test_database_name = DATABASES["default"]["TEST"]["NAME"]  # noqa: F405
if (
    not test_database_name.startswith("test_") or test_database_name == DATABASES["default"]["NAME"]  # noqa: F405
):
    raise ImproperlyConfigured(
        "POSTGRES_TEST_DB must start with test_ and differ from POSTGRES_DB."
    )
