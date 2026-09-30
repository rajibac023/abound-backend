import math
import secrets
from datetime import timedelta
from uuid import UUID

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac
from django.views.decorators.debug import sensitive_variables
from rest_framework.exceptions import APIException, Throttled, ValidationError

from accounts.models import RegistrationVerification, User
from accounts.services import register_user
from audit.services import record_event


class VerificationDeliveryUnavailable(APIException):
    status_code = 503
    default_detail = "We couldn’t send your verification email. Please try again shortly."
    default_code = "verification_delivery_unavailable"


def code_digest(verification_id: UUID, code: str) -> str:
    return salted_hmac(
        "accounts.registration_code", f"{verification_id}:{code}", algorithm="sha256"
    ).hexdigest()


@sensitive_variables()
def send_registration_code(*, email: str) -> RegistrationVerification:
    """Serialize resends by email and only report success after mail is accepted.

    SMTP is bounded by EMAIL_TIMEOUT. On failure the transaction keeps the previous
    code valid; neither credentials nor provider exception text are logged.
    """
    with transaction.atomic():
        if User.objects.filter(email=email).exists():
            raise ValidationError({"email": ["Unable to register with this email address."]})
        now = timezone.now()
        challenge, created = RegistrationVerification.objects.select_for_update().get_or_create(
            email=email,
            defaults={"expires_at": now, "sent_at": now},
        )
        if not created:
            remaining = (
                settings.AUTH_REGISTRATION_RESEND_SECONDS
                - (now - challenge.sent_at).total_seconds()
            )
            if remaining > 0:
                raise Throttled(wait=math.ceil(remaining))
        code = f"{secrets.randbelow(1_000_000):06d}"
        challenge.code_digest = code_digest(challenge.pk, code)
        challenge.failed_attempts = 0
        challenge.sent_at = now
        challenge.expires_at = now + timedelta(seconds=settings.AUTH_REGISTRATION_CODE_SECONDS)
        challenge.consumed_at = None
        challenge.save()
        context = {
            "code": code,
            "minutes": math.ceil(settings.AUTH_REGISTRATION_CODE_SECONDS / 60),
        }
        message = EmailMultiAlternatives(
            subject="Verify your email — Abound Holidays",
            body=render_to_string("accounts/registration_code.txt", context),
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[email],
        )
        message.attach_alternative(
            render_to_string("accounts/registration_code.html", context), "text/html"
        )
        try:
            sent = message.send(fail_silently=False)
        except Exception:
            raise VerificationDeliveryUnavailable() from None
        if sent != 1:
            raise VerificationDeliveryUnavailable()
        return challenge


@sensitive_variables()
def complete_registration(
    *,
    verification_id: UUID,
    code: str,
    email: str,
    password: str,
    first_name: str,
    last_name: str,
    phone: str = "",
) -> User:
    error = None
    user = None
    with transaction.atomic():
        challenge = (
            RegistrationVerification.objects.select_for_update()
            .filter(pk=verification_id, email=email)
            .first()
        )
        now = timezone.now()
        if challenge is None or challenge.consumed_at is not None:
            error = "This verification request is no longer valid. Request a new code."
        elif challenge.expires_at <= now:
            error = "Your code has expired. Request a new code."
        elif challenge.failed_attempts >= settings.AUTH_REGISTRATION_MAX_ATTEMPTS:
            error = "Too many incorrect attempts. Request a new code."
        elif not constant_time_compare(challenge.code_digest, code_digest(challenge.pk, code)):
            challenge.failed_attempts += 1
            challenge.save(update_fields=["failed_attempts"])
            remaining = settings.AUTH_REGISTRATION_MAX_ATTEMPTS - challenge.failed_attempts
            error = (
                f"That code is incorrect. {remaining} attempt(s) remaining."
                if remaining
                else "Too many incorrect attempts. Request a new code."
            )
        else:
            user = register_user(
                email=email,
                password=password,
                first_name=first_name,
                last_name=last_name,
                phone=phone,
            )
            user.email_verified_at = now
            user.save(update_fields=["email_verified_at", "updated_at"])
            challenge.consumed_at = now
            challenge.code_digest = ""
            challenge.save(update_fields=["consumed_at", "code_digest"])
            record_event("auth.email_verified", user)
    # Raise outside atomic so failed-attempt counters cannot be rolled back.
    if error:
        raise ValidationError({"code": [error]})
    assert user is not None
    return user
