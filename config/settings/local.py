"""Local settings require an explicit .env file or injected variables."""

from django.core.exceptions import ImproperlyConfigured

from config.environment import env, required_secret
from config.settings.base import *  # noqa: F403

SECRET_KEY = required_secret("DJANGO_SECRET_KEY")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1", "[::1]"])
SIMPLE_JWT["SIGNING_KEY"] = required_secret("JWT_SIGNING_KEY")  # noqa: F405
AUTH_COOKIE_SECURE = env.bool("AUTH_COOKIE_SECURE", default=False)
AUTH_PASSWORD_RESET_URL = env(
    "AUTH_PASSWORD_RESET_URL", default="http://localhost:5173/reset-password"
)
AUTH_PASSWORD_RESET_URL_MOBILE = env(
    "AUTH_PASSWORD_RESET_URL_MOBILE", default="aboundholidays://reset-password"
)
# Put the local runserver override before staticfiles' command. Production keeps its
# independently supervised delivery worker; no threads are started by a web request.
INSTALLED_APPS = ["notifications.apps.NotificationsConfig"] + [
    app
    for app in INSTALLED_APPS  # noqa: F405
    if app != "notifications.apps.NotificationsConfig"
]
AUTH_RESET_DEV_WORKER = env.bool("AUTH_RESET_DEV_WORKER", default=True)
# Email: with SMTP credentials in .env, verification codes and reset links are really sent.
# Without them the in-memory backend is used (it never prints bodies, since reset links contain
# credentials) and the accounts.W001 system check warns that nothing is delivered.
EMAIL_HOST = env("EMAIL_HOST", default="localhost")
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=False)
EMAIL_USE_SSL = env.bool("EMAIL_USE_SSL", default=False)
EMAIL_PORT = env.int("EMAIL_PORT", default=465 if EMAIL_USE_SSL else 587 if EMAIL_USE_TLS else 1025)
EMAIL_BACKEND = env(
    "EMAIL_BACKEND",
    default="django.core.mail.backends.smtp.EmailBackend"
    if EMAIL_HOST_USER
    else "django.core.mail.backends.locmem.EmailBackend",
)
DEFAULT_FROM_EMAIL = env(
    "DEFAULT_FROM_EMAIL",
    default=f"Abound Holidays <{EMAIL_HOST_USER}>"
    if EMAIL_HOST_USER
    else "no-reply@abound.localhost",
)
if EMAIL_USE_TLS and EMAIL_USE_SSL:
    raise ImproperlyConfigured(
        "Set only one of EMAIL_USE_TLS (port 587) or EMAIL_USE_SSL (port 465)."
    )
if EMAIL_HOST_USER and not EMAIL_HOST_PASSWORD:
    raise ImproperlyConfigured("EMAIL_HOST_USER is set but EMAIL_HOST_PASSWORD is empty.")
