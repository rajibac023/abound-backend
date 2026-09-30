import uuid as uuid_lib

from django.conf import settings
from django.db import models
from django.utils import timezone

from notifications.choices import NotificationChannel, NotificationEvent, NotificationStatus


class PasswordResetDelivery(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        SKIPPED = "skipped", "Skipped"
        FAILED = "failed", "Failed"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    password_fingerprint = models.CharField(max_length=64, blank=True)
    reset_url = models.TextField(blank=True, default='')
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    requested_at = models.DateTimeField(auto_now_add=True)
    available_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()

    class Meta:
        indexes = [
            models.Index(fields=["status", "available_at"], name="reset_delivery_pending_idx")
        ]


class Notification(models.Model):
    """One channel attempt for one event. The row itself is the delivery trace: status,
    attempt count, and error are updated in place rather than logged elsewhere, so the
    full history of what was sent, skipped, or failed is always queryable from the DB.
    """

    uuid = models.UUIDField(default=uuid_lib.uuid4, unique=True, editable=False)
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="notifications",
    )
    event = models.CharField(max_length=32, choices=NotificationEvent.choices)
    channel = models.CharField(max_length=8, choices=NotificationChannel.choices)
    # Uniquely identifies one (event, channel, related object, recipient[, occurrence])
    # attempt, so re-running an event dispatch (a queuing command re-run, a retried
    # request) can never create a second notification for the same thing.
    dedupe_key = models.CharField(max_length=255, unique=True)
    subject = models.CharField(max_length=255, blank=True)
    body = models.TextField(blank=True)
    context = models.JSONField(default=dict, blank=True)
    related_type = models.CharField(max_length=32, blank=True)
    related_uuid = models.UUIDField(null=True, blank=True)
    status = models.CharField(
        max_length=8, choices=NotificationStatus.choices, default=NotificationStatus.PENDING
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    error_message = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    available_at = models.DateTimeField(default=timezone.now)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["status", "available_at"], name="notification_pending_idx"),
            models.Index(fields=["related_type", "related_uuid"], name="notification_related_idx"),
        ]


class NotificationPreference(models.Model):
    """Absence of a row means the channel is enabled; a row only exists once a user has
    made an explicit choice, so opting back in is a delete rather than a second write.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notification_preferences"
    )
    channel = models.CharField(max_length=8, choices=NotificationChannel.choices)
    enabled = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "channel"], name="notification_pref_unique")
        ]
