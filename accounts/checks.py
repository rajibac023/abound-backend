from django.conf import settings
from django.core.checks import Warning, register

UNDELIVERED_BACKENDS = {
    "django.core.mail.backends.locmem.EmailBackend",
    "django.core.mail.backends.dummy.EmailBackend",
    "django.core.mail.backends.console.EmailBackend",
    "django.core.mail.backends.filebased.EmailBackend",
}


@register()
def email_delivery_check(app_configs, **kwargs):
    """Warn when verification codes and reset links would silently never reach an inbox."""
    if (
        settings.SETTINGS_MODULE.endswith(".test")
        or settings.EMAIL_BACKEND not in UNDELIVERED_BACKENDS
    ):
        return []
    return [
        Warning(
            "Emails are not delivered: registration verification codes and password reset "
            f"links are kept by {settings.EMAIL_BACKEND.rsplit('.', 2)[-2]} and never sent.",
            hint="Set EMAIL_HOST, EMAIL_HOST_USER, EMAIL_HOST_PASSWORD and EMAIL_USE_TLS or "
            "EMAIL_USE_SSL in backend/.env (see .env.example), restart, then run "
            "`manage.py sendtestemail you@example.com`.",
            id="accounts.W001",
        )
    ]
