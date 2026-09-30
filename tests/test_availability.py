from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

from django.db import close_old_connections, connection
from django.test import TransactionTestCase
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APITestCase

from accounts.models import User
from accounts.roles import Role
from audit.models import AuditEvent
from availability.models import AvailabilityBlock
from availability.services import assert_period_available
from bookings.models import Booking
from bookings.services import create_booking
from maintenance.models import MaintenanceRecord
from tests.test_vehicles import PASSWORD, vehicle_data
from vehicles.choices import VehicleStatus
from vehicles.models import Vehicle

CHECK = "/api/v1/availability/check/"
ADMIN_CHECK = "/api/v1/admin/availability/check/"
BLOCKS = "/api/v1/admin/availability/blocks/"


class OverlapProtectionTests(APITestCase):
    """Direct service-level tests of every independent unavailability rule."""

    def setUp(self) -> None:
        self.customer = User.objects.create_user(email="customer@example.com", password=PASSWORD)
        self.admin = User.objects.create_user(
            email="admin@example.com", password=PASSWORD, role=Role.ADMIN
        )
        self.vehicle = Vehicle.objects.create(**vehicle_data())
        self.base = timezone.localdate() + timedelta(days=30)

    def book(self, pickup, return_, **overrides):
        return create_booking(
            actor=self.customer,
            data={
                "vehicle": str(self.vehicle.uuid),
                "pickup_date": pickup.isoformat(),
                "return_date": return_.isoformat(),
                **overrides,
            },
        )

    def test_same_day_overlap_is_rejected(self) -> None:
        self.book(self.base, self.base + timedelta(days=5))
        with self.assertRaises(ValidationError):
            self.book(self.base, self.base + timedelta(days=2))

    def test_partial_overlap_is_rejected(self) -> None:
        self.book(self.base, self.base + timedelta(days=5))
        for pickup, returned in (
            (self.base + timedelta(days=3), self.base + timedelta(days=8)),  # tail overlap
            (self.base - timedelta(days=2), self.base + timedelta(days=2)),  # head overlap
        ):
            with self.assertRaises(ValidationError):
                self.book(pickup, returned)

    def test_complete_overlap_is_rejected_both_directions(self) -> None:
        self.book(self.base, self.base + timedelta(days=10))
        with self.assertRaises(ValidationError):
            self.book(self.base + timedelta(days=2), self.base + timedelta(days=5))
        with self.assertRaises(ValidationError):
            self.book(self.base - timedelta(days=5), self.base + timedelta(days=15))

    def test_adjacent_dates_do_not_overlap(self) -> None:
        first = self.book(self.base, self.base + timedelta(days=5))
        # New pickup on the existing return date is allowed: the hand-off day is shared.
        second = self.book(self.base + timedelta(days=5), self.base + timedelta(days=8))
        self.assertNotEqual(first.uuid, second.uuid)
        self.assertEqual(Booking.objects.filter(vehicle=self.vehicle).count(), 2)

    def test_active_maintenance_blocks_overlapping_and_adjacent_dates_are_free(self) -> None:
        MaintenanceRecord.objects.create(
            vehicle=self.vehicle,
            maintenance_type="REPAIR",
            start_date=self.base,
            end_date=self.base + timedelta(days=4),
            status="SCHEDULED",
            created_by=self.admin,
        )
        with self.assertRaises(ValidationError):
            self.book(self.base + timedelta(days=3), self.base + timedelta(days=6))
        # The vehicle returns to service the day after end_date.
        booking = self.book(self.base + timedelta(days=5), self.base + timedelta(days=8))
        self.assertIsNotNone(booking.uuid)

    def test_completed_and_cancelled_maintenance_do_not_block(self) -> None:
        for status in ("COMPLETED", "CANCELLED"):
            MaintenanceRecord.objects.create(
                vehicle=self.vehicle,
                maintenance_type="REPAIR",
                start_date=self.base,
                end_date=self.base + timedelta(days=4),
                status=status,
                created_by=self.admin,
            )
        booking = self.book(self.base, self.base + timedelta(days=3))
        self.assertIsNotNone(booking.uuid)

    def test_active_availability_block_blocks_the_period(self) -> None:
        AvailabilityBlock.objects.create(
            vehicle=self.vehicle,
            reason="Reserved for a photo shoot",
            start_date=self.base,
            end_date=self.base + timedelta(days=2),
            created_by=self.admin,
        )
        with self.assertRaises(ValidationError):
            self.book(self.base + timedelta(days=1), self.base + timedelta(days=4))
        # Adjacent to the block boundary is free.
        booking = self.book(self.base + timedelta(days=3), self.base + timedelta(days=5))
        self.assertIsNotNone(booking.uuid)

    def test_inactive_block_does_not_apply(self) -> None:
        AvailabilityBlock.objects.create(
            vehicle=self.vehicle,
            reason="Old block",
            start_date=self.base,
            end_date=self.base + timedelta(days=2),
            is_active=False,
            created_by=self.admin,
        )
        booking = self.book(self.base, self.base + timedelta(days=2))
        self.assertIsNotNone(booking.uuid)

    def test_unavailable_vehicle_status_is_rejected_at_booking_creation(self) -> None:
        from rest_framework.exceptions import NotFound

        blocking_statuses = (
            VehicleStatus.UNAVAILABLE,
            VehicleStatus.MAINTENANCE,
            VehicleStatus.INACTIVE,
        )
        for status in blocking_statuses:
            Vehicle.objects.filter(pk=self.vehicle.pk).update(status=status)
            with self.assertRaises(NotFound):
                self.book(self.base, self.base + timedelta(days=2))
        Vehicle.objects.filter(pk=self.vehicle.pk).update(status=VehicleStatus.AVAILABLE)

    def test_assert_period_available_excludes_the_given_booking(self) -> None:
        booking = self.book(self.base, self.base + timedelta(days=5))
        # A booking never conflicts with itself when re-validated (e.g. future reschedule flows).
        assert_period_available(
            vehicle=self.vehicle,
            pickup_date=booking.pickup_date,
            return_date=booking.return_date,
            exclude_booking_uuid=booking.uuid,
        )
        with self.assertRaises(ValidationError):
            assert_period_available(
                vehicle=self.vehicle,
                pickup_date=booking.pickup_date,
                return_date=booking.return_date,
            )


class AvailabilityCheckAPITests(APITestCase):
    def setUp(self) -> None:
        self.users = {
            role: User.objects.create_user(
                email=f"{role.lower()}@example.com", password=PASSWORD, role=role
            )
            for role in Role.values
        }
        self.vehicle = Vehicle.objects.create(**vehicle_data())
        self.base = timezone.localdate() + timedelta(days=30)
        self.payload = {
            "vehicle": str(self.vehicle.uuid),
            "pickup_date": self.base.isoformat(),
            "return_date": (self.base + timedelta(days=3)).isoformat(),
        }

    def test_public_check_reports_available_and_unavailable(self) -> None:
        response = self.client.post(CHECK, self.payload)
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        self.assertTrue(data["available"])
        self.assertEqual(data["reasons"], [])
        self.assertEqual(data["rental_days"], 3)
        create_booking(actor=self.users[Role.CUSTOMER], data=self.payload)
        response = self.client.post(CHECK, self.payload)
        data = response.json()["data"]
        self.assertFalse(data["available"])
        self.assertEqual(data["reasons"], ["BOOKING"])

    def test_public_check_404s_for_hidden_vehicle(self) -> None:
        Vehicle.objects.filter(pk=self.vehicle.pk).update(status="MAINTENANCE")
        self.assertEqual(self.client.post(CHECK, self.payload).status_code, 404)

    def test_admin_check_reports_status_reason_for_hidden_vehicle(self) -> None:
        self.client.force_authenticate(self.users[Role.ADMIN])
        Vehicle.objects.filter(pk=self.vehicle.pk).update(status="MAINTENANCE")
        response = self.client.post(ADMIN_CHECK, self.payload)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn("STATUS", response.json()["data"]["reasons"])

    def test_client_cannot_supply_availability_or_reasons(self) -> None:
        for field in ("available", "reasons", "rental_days"):
            response = self.client.post(CHECK, {**self.payload, field: "0"})
            self.assertEqual(response.status_code, 400, field)


class AvailabilityBlockAPITests(APITestCase):
    def setUp(self) -> None:
        self.users = {
            role: User.objects.create_user(
                email=f"{role.lower()}@example.com", password=PASSWORD, role=role
            )
            for role in Role.values
        }
        self.vehicle = Vehicle.objects.create(**vehicle_data())
        self.base = timezone.localdate() + timedelta(days=30)
        self.payload = {
            "vehicle": str(self.vehicle.uuid),
            "reason": "Scheduled repaint",
            "start_date": self.base.isoformat(),
            "end_date": (self.base + timedelta(days=2)).isoformat(),
        }

    def authorize(self, role: str) -> None:
        self.client.force_authenticate(self.users[role])

    def test_admin_can_create_update_and_deactivate_a_block(self) -> None:
        self.authorize(Role.ADMIN)
        response = self.client.post(BLOCKS, self.payload)
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()["data"]
        self.assertTrue(data["is_active"])
        self.assertEqual(data["created_by"], str(self.users[Role.ADMIN].uuid))
        self.assertTrue(AuditEvent.objects.filter(action="availability.block_created").exists())
        url = BLOCKS + data["uuid"] + "/"
        response = self.client.patch(url, {"reason": "Extended repaint"})
        self.assertEqual(response.json()["data"]["reason"], "Extended repaint")
        response = self.client.delete(url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["data"]["is_active"])
        self.assertTrue(AvailabilityBlock.objects.filter(uuid=data["uuid"]).exists())

    def test_invalid_dates_are_rejected(self) -> None:
        self.authorize(Role.ADMIN)
        response = self.client.post(
            BLOCKS, {**self.payload, "end_date": (self.base - timedelta(days=1)).isoformat()}
        )
        self.assertEqual(response.status_code, 400)

    def test_staff_can_view_but_not_manage_blocks(self) -> None:
        self.authorize(Role.ADMIN)
        created = self.client.post(BLOCKS, self.payload).json()["data"]
        self.authorize(Role.STAFF)
        self.assertEqual(self.client.get(BLOCKS).status_code, 200)
        self.assertEqual(self.client.post(BLOCKS, self.payload).status_code, 403)
        self.assertEqual(
            self.client.patch(BLOCKS + created["uuid"] + "/", {"reason": "x"}).status_code, 403
        )

    def test_customer_and_anonymous_are_denied(self) -> None:
        for role in (None, Role.CUSTOMER):
            self.client.force_authenticate(self.users[role] if role else None)
            self.assertIn(self.client.get(BLOCKS).status_code, (401, 403))


class ConcurrentBookingAvailabilityTests(TransactionTestCase):
    def test_concurrent_overlapping_booking_attempts_only_one_succeeds(self) -> None:
        customer = User.objects.create_user(email="customer@example.com", password=PASSWORD)
        other = User.objects.create_user(email="other@example.com", password=PASSWORD)
        vehicle = Vehicle.objects.create(**vehicle_data())
        pickup = timezone.localdate() + timedelta(days=10)
        returned = pickup + timedelta(days=3)
        barrier = Barrier(2)

        def attempt(actor: User) -> tuple[str, object]:
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET statement_timeout = '10s'")
                barrier.wait(timeout=10)
                try:
                    booking = create_booking(
                        actor=actor,
                        data={
                            "vehicle": str(vehicle.uuid),
                            "pickup_date": pickup.isoformat(),
                            "return_date": returned.isoformat(),
                        },
                    )
                    return "ok", booking.uuid
                except ValidationError as exc:
                    return "error", exc.detail
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            tasks = [executor.submit(attempt, customer), executor.submit(attempt, other)]
            results = [task.result(timeout=15) for task in tasks]
        self.assertEqual(sorted(outcome for outcome, _ in results), ["error", "ok"])
        self.assertEqual(Booking.objects.filter(vehicle=vehicle).count(), 1)
