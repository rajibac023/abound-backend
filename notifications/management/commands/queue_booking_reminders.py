from datetime import timedelta
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from bookings.choices import BookingStatus
from bookings.models import Booking
from notifications.choices import NotificationEvent
from notifications.services import notify


class Command(BaseCommand):
    help = "Queue a pickup reminder for every confirmed booking starting in N days."

    def handle(self, *args: Any, **options: Any) -> None:
        target_date = timezone.localdate() + timedelta(days=settings.BOOKING_REMINDER_DAYS_BEFORE)
        bookings = Booking.objects.select_related("customer", "vehicle").filter(
            status=BookingStatus.CONFIRMED, pickup_date=target_date
        )
        queued = 0
        for booking in bookings:
            with transaction.atomic():
                notify(
                    event=NotificationEvent.BOOKING_REMINDER,
                    recipient=booking.customer,
                    context={
                        "first_name": booking.customer.first_name,
                        "booking_reference": booking.booking_reference,
                        "vehicle_name": booking.vehicle.vehicle_name,
                        "pickup_date": str(booking.pickup_date),
                    },
                    related_type="booking",
                    related_uuid=booking.uuid,
                    # One reminder per booking per calendar day this command runs for -
                    # a re-run on the same day (cron retry) is a no-op via the dedupe key.
                    occurrence=str(target_date),
                )
            queued += 1
        self.stdout.write(f"Queued {queued} booking reminder(s) for {target_date}.")
