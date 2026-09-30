from datetime import timedelta
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import AuthRateLimit, AuthSession, RegistrationVerification
from notifications.models import PasswordResetDelivery


class Command(BaseCommand):
    help = "Remove expired rate counters and old sessions/reset jobs; preserve audit events."

    def handle(self, *args: Any, **options: Any) -> None:
        now = timezone.now()
        cutoff = now - timedelta(days=settings.AUTH_RECORD_RETENTION_DAYS)
        counters, _ = AuthRateLimit.objects.filter(expires_at__lt=now).delete()
        sessions, _ = AuthSession.objects.filter(expires_at__lt=cutoff).delete()
        deliveries, _ = PasswordResetDelivery.objects.filter(expires_at__lt=cutoff).delete()
        verifications, _ = RegistrationVerification.objects.filter(expires_at__lt=now).delete()
        self.stdout.write(
            f"Removed {counters} counters, {sessions} sessions, {deliveries} reset jobs, "
            f"{verifications} registration codes."
        )
