import logging
from typing import Protocol

from django.conf import settings
from django.core.mail import send_mail

from notifications.choices import NotificationChannel
from notifications.models import Notification

logger = logging.getLogger(__name__)


class NotificationDeliveryError(Exception):
    """A provider could not deliver a notification. The delivery worker retries with
    backoff and marks the notification FAILED once it runs out of attempts."""


class NotificationProvider(Protocol):
    def send(self, notification: Notification) -> None:
        """Deliver one notification, or raise NotificationDeliveryError."""


class EmailProvider:
    def send(self, notification: Notification) -> None:
        if not notification.recipient or not notification.recipient.email:
            raise NotificationDeliveryError("Recipient has no email address on file.")
        try:
            sent = send_mail(
                notification.subject,
                notification.body,
                settings.DEFAULT_FROM_EMAIL,
                [notification.recipient.email],
                fail_silently=False,
            )
        except Exception as exc:
            raise NotificationDeliveryError(str(exc)) from exc
        if sent != 1:
            raise NotificationDeliveryError("Email backend rejected the message.")


class LoggingPushProvider:
    """No push gateway (FCM/APNS) is wired up yet, and the app captures no device
    tokens. This logs instead of silently dropping the notification, so the channel is
    fully exercised end-to-end and a real gateway can be substituted in `PROVIDERS`
    without touching any calling code."""

    def send(self, notification: Notification) -> None:
        if not notification.recipient:
            raise NotificationDeliveryError("Recipient no longer exists.")
        logger.info(
            "push notification (no gateway configured): recipient=%s event=%s subject=%s",
            notification.recipient.uuid,
            notification.event,
            notification.subject,
        )


class LoggingSMSProvider:
    """No SMS gateway (e.g. Twilio) is wired up yet. Requires a phone number on file so
    the log line reflects a real, reachable destination rather than a placeholder."""

    def send(self, notification: Notification) -> None:
        if not notification.recipient or not notification.recipient.phone:
            raise NotificationDeliveryError("Recipient has no phone number on file.")
        logger.info(
            "sms notification (no gateway configured): recipient=%s event=%s",
            notification.recipient.uuid,
            notification.event,
        )


PROVIDERS: dict[str, NotificationProvider] = {
    NotificationChannel.EMAIL: EmailProvider(),
    NotificationChannel.PUSH: LoggingPushProvider(),
    NotificationChannel.SMS: LoggingSMSProvider(),
}


def get_provider(channel: str) -> NotificationProvider:
    return PROVIDERS[channel]
