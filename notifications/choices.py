from django.db import models


class NotificationChannel(models.TextChoices):
    EMAIL = "email"
    PUSH = "push"
    SMS = "sms"


class NotificationEvent(models.TextChoices):
    ACCOUNT_REGISTERED = "account_registered"
    BOOKING_CREATED = "booking_created"
    BOOKING_CONFIRMED = "booking_confirmed"
    BOOKING_REJECTED = "booking_rejected"
    BOOKING_CANCELLED = "booking_cancelled"
    BOOKING_ACTIVATED = "booking_activated"
    BOOKING_COMPLETED = "booking_completed"
    BOOKING_REMINDER = "booking_reminder"
    MAINTENANCE_REMINDER = "maintenance_reminder"


class NotificationStatus(models.TextChoices):
    PENDING = "pending"
    SENT = "sent"
    SKIPPED = "skipped"
    FAILED = "failed"


# Channels attempted for each event, before per-user preference and per-channel
# reachability (e.g. no phone number on file) narrow it further. Callers in other
# apps only ever name an event — this table is the one place that decides how it is
# delivered, so adding a channel to an event never touches booking/account code.
EVENT_CHANNELS: dict[str, tuple[str, ...]] = {
    NotificationEvent.ACCOUNT_REGISTERED: (NotificationChannel.EMAIL,),
    NotificationEvent.BOOKING_CREATED: (NotificationChannel.EMAIL,),
    NotificationEvent.BOOKING_CONFIRMED: (NotificationChannel.EMAIL, NotificationChannel.PUSH),
    NotificationEvent.BOOKING_REJECTED: (NotificationChannel.EMAIL,),
    NotificationEvent.BOOKING_CANCELLED: (NotificationChannel.EMAIL, NotificationChannel.PUSH),
    NotificationEvent.BOOKING_ACTIVATED: (NotificationChannel.EMAIL, NotificationChannel.PUSH),
    NotificationEvent.BOOKING_COMPLETED: (NotificationChannel.EMAIL,),
    NotificationEvent.BOOKING_REMINDER: (
        NotificationChannel.EMAIL,
        NotificationChannel.PUSH,
        NotificationChannel.SMS,
    ),
    NotificationEvent.MAINTENANCE_REMINDER: (NotificationChannel.EMAIL,),
}
