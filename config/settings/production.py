"""Fail-closed production configuration loaded by WSGI and ASGI."""

from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured

from config.environment import env, positive_integer, required_secret
from config.settings.base import *  # noqa: F403

SECRET_KEY = required_secret("DJANGO_SECRET_KEY")
SIMPLE_JWT["SIGNING_KEY"] = required_secret("JWT_SIGNING_KEY")  # noqa: F405
if SIMPLE_JWT["SIGNING_KEY"] == SECRET_KEY:  # noqa: F405
    raise ImproperlyConfigured("JWT_SIGNING_KEY must differ from DJANGO_SECRET_KEY.")

if not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS:  # noqa: F405
    raise ImproperlyConfigured("Production requires explicit DJANGO_ALLOWED_HOSTS.")

if not DATABASES["default"]["PASSWORD"]:  # noqa: F405
    raise ImproperlyConfigured("Production requires POSTGRES_PASSWORD.")

DATABASES["default"]["OPTIONS"]["sslmode"] = env(  # noqa: F405
    "POSTGRES_SSLMODE", default="verify-full"
)
if DATABASES["default"]["OPTIONS"]["sslmode"] not in {"verify-ca", "verify-full"}:  # noqa: F405
    raise ImproperlyConfigured("Production PostgreSQL must use verify-ca or verify-full TLS.")
if ssl_root_cert := env("POSTGRES_SSLROOTCERT", default=""):
    DATABASES["default"]["OPTIONS"]["sslrootcert"] = ssl_root_cert  # noqa: F405

for origin in [*CORS_ALLOWED_ORIGINS, *CSRF_TRUSTED_ORIGINS]:  # noqa: F405
    if not origin.startswith("https://"):
        raise ImproperlyConfigured("Production browser origins must use HTTPS.")

DEBUG = False
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = positive_integer("DJANGO_HSTS_SECONDS", default=31536000)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool("DJANGO_HSTS_INCLUDE_SUBDOMAINS", default=False)
SECURE_HSTS_PRELOAD = False
SECURE_REFERRER_POLICY = "same-origin"

# These recommendations need a deployment-domain policy. HTTPS and HSTS on the
# configured API host are enforced; blanket subdomain coverage/preload are not.
# All other deployment warnings remain fatal in CI.
SILENCED_SYSTEM_CHECKS = ["security.W005", "security.W021"]

# Enable only behind an ingress that strips and overwrites this header.
if env.bool("DJANGO_TRUST_PROXY_SSL_HEADER", default=False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = env("EMAIL_HOST")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = True
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL")

reset_url = urlsplit(AUTH_PASSWORD_RESET_URL)  # noqa: F405
if (
    reset_url.scheme != "https"
    or not reset_url.hostname
    or reset_url.username
    or reset_url.password
    or reset_url.query
    or reset_url.fragment
):
    raise ImproperlyConfigured(
        "Production requires an HTTPS AUTH_PASSWORD_RESET_URL "
        "without credentials, query, or fragment."
    )
if not EMAIL_HOST or not DEFAULT_FROM_EMAIL:
    raise ImproperlyConfigured(
        "Configure EMAIL_HOST and DEFAULT_FROM_EMAIL for password reset delivery."
    )
