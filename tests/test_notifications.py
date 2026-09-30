from datetime import timedelta
from io import StringIO
from typing import Any
from unittest.mock import patch

from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import User
from accounts.roles import Role
from accounts.services import register_user
from bookings.services import create_booking, transition_booking
from maintenance.models import MaintenanceRecord
from notifications.choices import NotificationChannel, NotificationEvent, NotificationStatus
from notifications.models import Notification, NotificationPreference
from notifications.providers import NotificationDeliveryError
from notifications.services import deliver_one_notification, notify
from tests.test_vehicles import PASSWORD, vehicle_data
from vehicles.models import Vehicle

PREFERENCES = "/api/v1/notifications/preferences/"

RECIPIENT_KWARGS: dict[str, Any] = dict(
    event=NotificationEvent.BOOKING_CREATED,
    context={
        "first_name": "Rajib",
        "booking_reference": "ABH-2026-000001",
        "vehicle_name": "Test SUV",
        "pickup_date": "2026-12-01",
        "return_date": "2026-12-05",
        "total_amount": "1000.00",
        "currency": "NPR",
    },
    related_type="booking",
)


class NotifyTests(TestCase):
    def setUp(self) -> None:
        self.customer = User.objects.create_user(email="customer@example.com", password=PASSWORD)

    def test_notify_creates_one_notification_per_configured_channel(self) -> None:
        notify(recipient=self.customer, related_uuid=self.customer.uuid, **RECIPIENT_KWARGS)
        created = Notification.objects.filter(recipient=self.customer)
        self.assertEqual({n.channel for n in created}, {NotificationChannel.EMAIL})
        notification = created.get()
        self.assertEqual(notification.status, NotificationStatus.PENDING)
        self.assertIn("ABH-2026-000001", notification.body)

    def test_repeated_notify_for_the_same_event_does_not_duplicate(self) -> None:
        notify(recipient=self.customer, related_uuid=self.customer.uuid, **RECIPIENT_KWARGS)
        notify(recipient=self.customer, related_uuid=self.customer.uuid, **RECIPIENT_KWARGS)
        self.assertEqual(Notification.objects.filter(recipient=self.customer).count(), 1)

    def test_disabled_channel_preference_is_skipped_but_still_traced(self) -> None:
        NotificationPreference.objects.create(
            user=self.customer, channel=NotificationChannel.EMAIL, enabled=False
        )
        notify(recipient=self.customer, related_uuid=self.customer.uuid, **RECIPIENT_KWARGS)
        notification = Notification.objects.get(recipient=self.customer)
        self.assertEqual(notification.status, NotificationStatus.SKIPPED)

    def test_sms_without_a_phone_number_on_file_is_skipped(self) -> None:
        notify(
            event=NotificationEvent.BOOKING_REMINDER,
            recipient=self.customer,
            context={**RECIPIENT_KWARGS["context"]},
            related_type="booking",
            related_uuid=self.customer.uuid,
        )
        sms = Notification.objects.get(recipient=self.customer, channel=NotificationChannel.SMS)
        self.assertEqual(sms.status, NotificationStatus.SKIPPED)

    def test_sms_with_a_phone_number_is_queued(self) -> None:
        self.customer.phone = "+9779800000000"
        self.customer.save(update_fields=["phone"])
        notify(
            event=NotificationEvent.BOOKING_REMINDER,
            recipient=self.customer,
            context={**RECIPIENT_KWARGS["context"]},
            related_type="booking",
            related_uuid=self.customer.uuid,
        )
        sms = Notification.objects.get(recipient=self.customer, channel=NotificationChannel.SMS)
        self.assertEqual(sms.status, NotificationStatus.PENDING)


class DeliveryTests(TestCase):
    def setUp(self) -> None:
        self.customer = User.objects.create_user(email="customer@example.com", password=PASSWORD)
        notify(recipient=self.customer, related_uuid=self.customer.uuid, **RECIPIENT_KWARGS)
        self.notification = Notification.objects.get(recipient=self.customer)

    def test_successful_delivery_sends_email_and_marks_sent(self) -> None:
        self.assertTrue(deliver_one_notification())
        self.notification.refresh_from_db()
        self.assertEqual(self.notification.status, NotificationStatus.SENT)
        self.assertEqual(self.notification.attempts, 1)
        self.assertIsNotNone(self.notification.sent_at)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("ABH-2026-000001", mail.outbox[0].subject)

    def test_no_pending_notification_returns_false(self) -> None:
        deliver_one_notification()
        self.assertFalse(deliver_one_notification())

    @override_settings(NOTIFICATION_MAX_ATTEMPTS=2, NOTIFICATION_RETRY_SECONDS=0)
    def test_provider_failure_is_retried_then_marked_failed(self) -> None:
        with patch(
            "notifications.providers.EmailProvider.send",
            side_effect=NotificationDeliveryError("smtp unavailable"),
        ):
            self.assertTrue(deliver_one_notification())
            self.notification.refresh_from_db()
            self.assertEqual(self.notification.status, NotificationStatus.PENDING)
            self.assertEqual(self.notification.attempts, 1)
            self.assertEqual(self.notification.error_message, "smtp unavailable")

            self.assertTrue(deliver_one_notification())
            self.notification.refresh_from_db()
            self.assertEqual(self.notification.status, NotificationStatus.FAILED)
            self.assertEqual(self.notification.attempts, 2)
        # A failed provider must never raise out of the delivery loop.
        self.assertEqual(len(mail.outbox), 0)

    def test_an_unexpected_provider_bug_is_caught_not_raised(self) -> None:
        with patch(
            "notifications.providers.EmailProvider.send", side_effect=RuntimeError("boom")
        ):
            self.assertTrue(deliver_one_notification())
        self.notification.refresh_from_db()
        self.assertIn("boom", self.notification.error_message)

    def test_deliver_notifications_command_processes_a_batch(self) -> None:
        out = StringIO()
        call_command("deliver_notifications", stdout=out)
        self.assertIn("Processed 1 notification delivery job", out.getvalue())
        self.notification.refresh_from_db()
        self.assertEqual(self.notification.status, NotificationStatus.SENT)


class BookingLifecycleNotificationTests(TestCase):
    def setUp(self) -> None:
        self.customer = User.objects.create_user(email="customer@example.com", password=PASSWORD)
        self.staff = User.objects.create_user(
            email="staff@example.com", password=PASSWORD, role=Role.STAFF
        )
        self.vehicle = Vehicle.objects.create(**vehicle_data())

    def test_create_booking_queues_a_booking_created_notification(self) -> None:
        pickup = timezone.localdate() + timedelta(days=10)
        booking = create_booking(
            actor=self.customer,
            data={
                "vehicle": str(self.vehicle.uuid),
                "pickup_date": pickup.isoformat(),
                "return_date": (pickup + timedelta(days=4)).isoformat(),
            },
        )
        notification = Notification.objects.get(
            related_type="booking",
            related_uuid=booking.uuid,
            event=NotificationEvent.BOOKING_CREATED,
        )
        self.assertEqual(notification.recipient, self.customer)
        self.assertEqual(notification.status, NotificationStatus.PENDING)

    def test_transition_to_confirmed_queues_a_booking_confirmed_notification(self) -> None:
        pickup = timezone.localdate() + timedelta(days=10)
        booking = create_booking(
            actor=self.customer,
            data={
                "vehicle": str(self.vehicle.uuid),
                "pickup_date": pickup.isoformat(),
                "return_date": (pickup + timedelta(days=4)).isoformat(),
            },
        )
        transition_booking(actor=self.staff, booking_uuid=booking.uuid, target_status="CONFIRMED")
        self.assertTrue(
            Notification.objects.filter(
                related_type="booking",
                related_uuid=booking.uuid,
                event=NotificationEvent.BOOKING_CONFIRMED,
                recipient=self.customer,
            ).exists()
        )

    def test_a_duplicate_notify_call_does_not_break_the_booking_transaction(self) -> None:
        """The booking write and its notification row share one atomic transaction;
        a duplicate insert (same dedupe key) must resolve via get_or_create, not bubble
        an IntegrityError up through the booking service."""
        pickup = timezone.localdate() + timedelta(days=10)
        booking = create_booking(
            actor=self.customer,
            data={
                "vehicle": str(self.vehicle.uuid),
                "pickup_date": pickup.isoformat(),
                "return_date": (pickup + timedelta(days=4)).isoformat(),
            },
        )
        # Simulate a second, identical dispatch for the same booking-created event.
        notify(
            event=NotificationEvent.BOOKING_CREATED,
            recipient=self.customer,
            context={
                "first_name": self.customer.first_name,
                "booking_reference": booking.booking_reference,
                "vehicle_name": self.vehicle.vehicle_name,
                "pickup_date": str(booking.pickup_date),
                "return_date": str(booking.return_date),
                "total_amount": str(booking.total_amount),
                "currency": booking.currency,
            },
            related_type="booking",
            related_uuid=booking.uuid,
        )
        self.assertEqual(
            Notification.objects.filter(
                related_type="booking",
                related_uuid=booking.uuid,
                event=NotificationEvent.BOOKING_CREATED,
            ).count(),
            1,
        )


class NotificationPreferenceAPITests(APITestCase):
    def setUp(self) -> None:
        self.customer = User.objects.create_user(email="customer@example.com", password=PASSWORD)
        self.client.force_authenticate(self.customer)

    def test_defaults_are_all_channels_enabled(self) -> None:
        response = self.client.get(PREFERENCES)
        self.assertEqual(response.status_code, 200, response.content)
        rows = {row["channel"]: row["enabled"] for row in response.json()["data"]}
        self.assertEqual(rows, {"email": True, "push": True, "sms": True})

    def test_patch_disables_a_channel_and_it_is_then_respected_by_notify(self) -> None:
        response = self.client.patch(PREFERENCES, {"channel": "email", "enabled": False})
        self.assertEqual(response.status_code, 200, response.content)
        rows = {row["channel"]: row["enabled"] for row in response.json()["data"]}
        self.assertEqual(rows["email"], False)

        notify(recipient=self.customer, related_uuid=self.customer.uuid, **RECIPIENT_KWARGS)
        notification = Notification.objects.get(recipient=self.customer)
        self.assertEqual(notification.status, NotificationStatus.SKIPPED)

    def test_a_user_cannot_see_or_change_another_users_preferences(self) -> None:
        other = User.objects.create_user(email="other@example.com", password=PASSWORD)
        NotificationPreference.objects.create(
            user=other, channel=NotificationChannel.EMAIL, enabled=False
        )
        response = self.client.get(PREFERENCES)
        rows = {row["channel"]: row["enabled"] for row in response.json()["data"]}
        self.assertEqual(rows["email"], True)

    def test_anonymous_request_is_rejected(self) -> None:
        self.client.force_authenticate(None)
        response = self.client.get(PREFERENCES)
        self.assertEqual(response.status_code, 401)


class AccountRegistrationNotificationTests(TestCase):
    def test_registration_queues_an_account_registered_notification(self) -> None:
        user = register_user(
            email="new@example.com", password=PASSWORD, first_name="New", last_name="User"
        )
        self.assertTrue(
            Notification.objects.filter(
                recipient=user, event=NotificationEvent.ACCOUNT_REGISTERED
            ).exists()
        )


class ReminderCommandTests(TestCase):
    def setUp(self) -> None:
        self.customer = User.objects.create_user(email="customer@example.com", password=PASSWORD)
        self.staff = User.objects.create_user(
            email="staff@example.com", password=PASSWORD, role=Role.STAFF
        )
        self.vehicle = Vehicle.objects.create(**vehicle_data())

    @override_settings(BOOKING_REMINDER_DAYS_BEFORE=1)
    def test_queue_booking_reminders_only_targets_confirmed_bookings_starting_tomorrow(
        self,
    ) -> None:
        pickup = timezone.localdate() + timedelta(days=1)
        booking = create_booking(
            actor=self.customer,
            data={
                "vehicle": str(self.vehicle.uuid),
                "pickup_date": pickup.isoformat(),
                "return_date": (pickup + timedelta(days=4)).isoformat(),
            },
        )
        transition_booking(actor=self.staff, booking_uuid=booking.uuid, target_status="CONFIRMED")
        out = StringIO()
        call_command("queue_booking_reminders", stdout=out)
        self.assertTrue(
            Notification.objects.filter(
                related_type="booking",
                related_uuid=booking.uuid,
                event=NotificationEvent.BOOKING_REMINDER,
            ).exists()
        )
        # Re-running the same day must not queue a second reminder.
        call_command("queue_booking_reminders", stdout=StringIO())
        self.assertEqual(
            Notification.objects.filter(
                related_type="booking",
                related_uuid=booking.uuid,
                event=NotificationEvent.BOOKING_REMINDER,
                channel=NotificationChannel.EMAIL,
            ).count(),
            1,
        )

    @override_settings(MAINTENANCE_REMINDER_DAYS_BEFORE=2)
    def test_queue_maintenance_reminders_notifies_every_administrator(self) -> None:
        start = timezone.localdate() + timedelta(days=2)
        record = MaintenanceRecord.objects.create(
            vehicle=self.vehicle,
            maintenance_type="ROUTINE_SERVICE",
            start_date=start,
            end_date=start,
            created_by=self.staff,
        )
        out = StringIO()
        call_command("queue_maintenance_reminders", stdout=out)
        self.assertTrue(
            Notification.objects.filter(
                related_type="maintenance_record",
                related_uuid=record.uuid,
                recipient=self.staff,
                event=NotificationEvent.MAINTENANCE_REMINDER,
            ).exists()
        )
        # A customer must never receive a maintenance reminder.
        self.assertFalse(
            Notification.objects.filter(
                related_type="maintenance_record", recipient=self.customer
            ).exists()
        )
