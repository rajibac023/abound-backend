import logging
import math
from datetime import timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode
from uuid import UUID

from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.views.decorators.debug import sensitive_variables

from accounts.tokens import password_fingerprint
from notifications.choices import EVENT_CHANNELS, NotificationChannel, NotificationStatus
from notifications.messages import render_message
from notifications.models import Notification, NotificationPreference, PasswordResetDelivery
from notifications.providers import get_provider

if TYPE_CHECKING:
    from accounts.models import User

logger = logging.getLogger(__name__)


def _build_reset_link(base_url: str, uuid_str: str, token: str) -> str:
    params = urlencode({"uuid": uuid_str, "token": token})
    # Web URLs use a hash fragment so the token never reaches the server in access logs.
    # Custom-scheme deep links (mobile) use query params since native apps can read them
    # via useLocalSearchParams(); hash fragments are not accessible that way.
    if base_url.startswith(("http://", "https://")):
        return base_url + "#" + params
    return base_url + "?" + params


@sensitive_variables("token", "link")
def deliver_one_password_reset() -> bool:
    """Claim one row with SKIP LOCKED; SMTP has a bounded timeout and retry count."""
    with transaction.atomic():
        delivery = (
            PasswordResetDelivery.objects.select_for_update(skip_locked=True)
            .filter(status=PasswordResetDelivery.Status.PENDING, available_at__lte=timezone.now())
            .order_by("pk")
            .first()
        )
        if delivery is None:
            return False
        user = delivery.user
        if (
            user is None
            or not user.is_active
            or not user.has_usable_password()
            or delivery.expires_at <= timezone.now()
            or not constant_time_compare(delivery.password_fingerprint, password_fingerprint(user))
        ):
            delivery.status = PasswordResetDelivery.Status.SKIPPED
        else:
            base_url = delivery.reset_url or settings.AUTH_PASSWORD_RESET_URL
            is_web = base_url.startswith(("http://", "https://"))
            token = default_token_generator.make_token(user)
            if is_web:
                link = _build_reset_link(base_url, str(user.uuid), token)
            else:
                # Custom-scheme deep links are blocked by email clients — route through
                # the backend bridge page (plain http://) which JS-redirects into the app.
                backend_base = getattr(settings, "AUTH_BACKEND_BASE_URL", "http://localhost:8000").rstrip("/")
                params = urlencode({"uuid": str(user.uuid), "token": token})
                link = f"{backend_base}/api/v1/auth/password/reset/open/?{params}"
            delivery.attempts += 1
            try:
                timeout_minutes = math.ceil(settings.PASSWORD_RESET_TIMEOUT / 60)
                context = {
                    "link": link,
                    "minutes": timeout_minutes,
                    "is_web": is_web,
                    "first_name": user.first_name or "there",
                }
                message = EmailMultiAlternatives(
                    subject="Reset your Abound Holidays password",
                    body=render_to_string("notifications/password_reset.txt", context),
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    to=[user.email],
                )
                message.attach_alternative(
                    render_to_string("notifications/password_reset.html", context), "text/html"
                )
                sent = message.send(fail_silently=False)
                if sent != 1:
                    raise RuntimeError("Delivery was not accepted.")
                delivery.status = PasswordResetDelivery.Status.SENT
            except Exception:
                # Transport exceptions may include addresses or credentials; do not log their text.
                logger.warning("Password reset delivery failed; retry policy applied")
                delivery.available_at = timezone.now() + timedelta(
                    seconds=settings.AUTH_RESET_RETRY_SECONDS
                )
                if delivery.attempts >= settings.AUTH_RESET_MAX_ATTEMPTS:
                    delivery.status = PasswordResetDelivery.Status.FAILED
        delivery.save(update_fields=["status", "attempts", "available_at"])
    return True


def channel_enabled(user: "User", channel: str) -> bool:
    """Absence of a preference row means the channel is enabled (opt-out model)."""
    preference = NotificationPreference.objects.filter(user=user, channel=channel).first()
    return preference is None or preference.enabled


def set_preference(*, user: "User", channel: str, enabled: bool) -> None:
    NotificationPreference.objects.update_or_create(
        user=user, channel=channel, defaults={"enabled": enabled}
    )


def list_preferences(user: "User") -> list[dict[str, Any]]:
    overrides = {p.channel: p.enabled for p in NotificationPreference.objects.filter(user=user)}
    return [
        {"channel": channel, "enabled": overrides.get(channel, True)}
        for channel in NotificationChannel.values
    ]


def notify(
    *,
    event: str,
    recipient: "User",
    context: dict[str, Any],
    related_type: str = "",
    related_uuid: UUID | None = None,
    occurrence: str = "",
) -> None:
    """Record one notification per channel configured for `event`. This only ever
    writes rows — it never calls a provider — so it is safe to call from inside a
    booking (or any other) transaction: a genuine failure here is a real DB error that
    should roll back with the caller, and there is no network call that could hang or
    fail independently. Actual delivery happens later via `deliver_one_notification`,
    run by the `deliver_notifications` management command.

    Callers only ever name an event, never a channel or provider — see
    `notifications.choices.EVENT_CHANNELS` for how an event maps to channels.
    """
    subject, body = render_message(event, context)
    for channel in EVENT_CHANNELS.get(event, ()):
        reachable = channel != NotificationChannel.SMS or bool(recipient.phone)
        enabled = reachable and channel_enabled(recipient, channel)
        dedupe_key = ":".join(
            [event, channel, related_type, str(related_uuid), str(recipient.pk), occurrence]
        )
        Notification.objects.get_or_create(
            dedupe_key=dedupe_key,
            defaults={
                "recipient": recipient,
                "event": event,
                "channel": channel,
                "subject": subject,
                "body": body,
                "context": context,
                "related_type": related_type,
                "related_uuid": related_uuid,
                "status": NotificationStatus.PENDING if enabled else NotificationStatus.SKIPPED,
            },
        )


def deliver_one_notification() -> bool:
    """Claim one pending notification with SKIP LOCKED and attempt delivery. Provider
    failures are caught here and never propagate — a bad send can only ever affect its
    own row, retried with backoff up to NOTIFICATION_MAX_ATTEMPTS."""
    with transaction.atomic():
        notification = (
            Notification.objects.select_for_update(skip_locked=True)
            .filter(status=NotificationStatus.PENDING, available_at__lte=timezone.now())
            .order_by("pk")
            .first()
        )
        if notification is None:
            return False
        if notification.recipient is None:
            notification.status = NotificationStatus.SKIPPED
            notification.save(update_fields=["status"])
            return True
        notification.attempts += 1
        try:
            get_provider(notification.channel).send(notification)
            notification.status = NotificationStatus.SENT
            notification.sent_at = timezone.now()
            notification.error_message = ""
        except Exception as exc:  # noqa: BLE001 - a provider bug must never break delivery
            logger.warning(
                "notification delivery failed; retry policy applied",
                extra={
                    "notification_uuid": str(notification.uuid),
                    "channel": notification.channel,
                    "event": notification.event,
                    "attempts": notification.attempts,
                },
            )
            notification.available_at = timezone.now() + timedelta(
                seconds=settings.NOTIFICATION_RETRY_SECONDS
            )
            notification.error_message = str(exc)[:500]
            if notification.attempts >= settings.NOTIFICATION_MAX_ATTEMPTS:
                notification.status = NotificationStatus.FAILED
        notification.save(
            update_fields=["status", "attempts", "available_at", "sent_at", "error_message"]
        )
    return True
