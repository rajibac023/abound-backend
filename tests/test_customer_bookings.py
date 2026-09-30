from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

from django.db import close_old_connections, connection
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APITestCase

from accounts.models import User
from accounts.roles import Role
from accounts.services import login
from availability.models import AvailabilityBlock
from availability.services import save_block
from bookings.models import Booking
from bookings.services import create_booking, transition_booking
from maintenance.models import MaintenanceRecord
from maintenance.services import create_maintenance_record, update_maintenance_record
from tests.test_vehicles import PASSWORD, vehicle_data
from vehicles.models import Vehicle, VehicleImage

BOOKINGS = "/api/v1/bookings/"
AVAILABILITY = "/api/v1/availability/check/"


class CustomerMobileBookingTests(APITestCase):
    def setUp(self) -> None:
        self.customer = User.objects.create_user(email="mobile@example.com", password=PASSWORD)
        self.other = User.objects.create_user(email="other-mobile@example.com", password=PASSWORD)
        self.admin = User.objects.create_user(
            email="operations@example.com", password=PASSWORD, role=Role.ADMIN
        )
        self.vehicle = Vehicle.objects.create(**vehicle_data())
        self.pickup = timezone.localdate() + timedelta(days=15)
        self.payload = {
            "vehicle": str(self.vehicle.uuid),
            "pickup_date": self.pickup.isoformat(),
            "return_date": (self.pickup + timedelta(days=3)).isoformat(),
        }
        self.client.force_authenticate(self.customer)

    def test_jwt_booking_flow_has_safe_vehicle_price_and_cancel_capability(self) -> None:
        self.client.force_authenticate(None)
        access = login(email=self.customer.email, password=PASSWORD, client="mobile")[1]["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        image = VehicleImage.objects.create(
            vehicle=self.vehicle,
            image=f"vehicle-images/{self.vehicle.uuid}/mobile.jpg",
            is_primary=True,
        )
        check = self.client.post(AVAILABILITY, self.payload)
        self.assertTrue(check.json()["data"]["available"])
        response = self.client.post(BOOKINGS, self.payload, HTTP_IDEMPOTENCY_KEY="mobile-request")
        self.assertEqual(response.status_code, 201, response.content)
        booking = response.json()["data"]
        self.assertEqual(booking["status"], "PENDING")
        self.assertTrue(booking["can_cancel"])
        self.assertEqual(booking["vehicle"], str(self.vehicle.uuid))
        details = booking["vehicle_details"]
        self.assertEqual(set(details), {"uuid", "vehicle_name", "brand", "model", "primary_image"})
        self.assertEqual(details["primary_image"]["uuid"], str(image.uuid))
        self.assertEqual(
            details["primary_image"]["image"],
            f"/api/v1/vehicles/{self.vehicle.uuid}/images/{image.uuid}/file/",
        )
        self.assertEqual(booking["total_amount"], "6000.00")
        self.assertEqual(booking["currency"], "NPR")
        for private in ("admin_note", "created_by", "idempotency_key", "registration_number"):
            self.assertNotIn(private, booking)
            self.assertNotIn(private, details)
        Vehicle.objects.filter(pk=self.vehicle.pk).update(daily_rate="9999.00")
        fetched = self.client.get(f"{BOOKINGS}{booking['uuid']}/").json()["data"]
        self.assertEqual(fetched["total_amount"], "6000.00")
        self.assertFalse(self.client.post(AVAILABILITY, self.payload).json()["data"]["available"])
        cancelled = self.client.post(f"{BOOKINGS}{booking['uuid']}/cancel/", {}).json()["data"]
        self.assertEqual(cancelled["status"], "CANCELLED")
        self.assertFalse(cancelled["can_cancel"])
        self.assertTrue(self.client.post(AVAILABILITY, self.payload).json()["data"]["available"])

    def test_hidden_vehicle_keeps_owned_booking_details_without_public_image(self) -> None:
        booking = create_booking(actor=self.customer, data=self.payload)
        VehicleImage.objects.create(
            vehicle=self.vehicle, image="vehicle-images/private.jpg", is_primary=True
        )
        Vehicle.objects.filter(pk=self.vehicle.pk).update(status="INACTIVE")
        response = self.client.get(f"{BOOKINGS}{booking.uuid}/")
        self.assertEqual(response.status_code, 200)
        details = response.json()["data"]["vehicle_details"]
        self.assertEqual(details["vehicle_name"], self.vehicle.vehicle_name)
        self.assertIsNone(details["primary_image"])

    def test_vehicle_images_and_details_do_not_leak_other_customer_bookings(self) -> None:
        booking = create_booking(actor=self.customer, data=self.payload)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(BOOKINGS).json()["data"]["count"], 0)
        self.assertEqual(self.client.get(f"{BOOKINGS}{booking.uuid}/").status_code, 404)
        self.assertEqual(self.client.post(f"{BOOKINGS}{booking.uuid}/cancel/", {}).status_code, 404)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(f"{BOOKINGS}{booking.uuid}/").status_code, 401)

    def test_cancel_capability_matches_pending_confirmed_and_terminal_policy(self) -> None:
        booking = create_booking(actor=self.customer, data=self.payload)
        detail = f"{BOOKINGS}{booking.uuid}/"
        self.assertTrue(self.client.get(detail).json()["data"]["can_cancel"])
        transition_booking(actor=self.admin, booking_uuid=booking.uuid, target_status="CONFIRMED")
        self.assertTrue(self.client.get(detail).json()["data"]["can_cancel"])
        transition_booking(actor=self.admin, booking_uuid=booking.uuid, target_status="ACTIVE")
        self.assertFalse(self.client.get(detail).json()["data"]["can_cancel"])
        self.assertEqual(self.client.post(detail + "cancel/", {}).status_code, 400)
        transition_booking(actor=self.admin, booking_uuid=booking.uuid, target_status="COMPLETED")
        self.assertFalse(self.client.get(detail).json()["data"]["can_cancel"])

    def test_reused_key_cannot_silently_return_a_different_booking(self) -> None:
        first = self.client.post(BOOKINGS, self.payload, HTTP_IDEMPOTENCY_KEY="attempt-1")
        self.assertEqual(first.status_code, 201)
        changed = {**self.payload, "return_date": (self.pickup + timedelta(days=5)).isoformat()}
        response = self.client.post(BOOKINGS, changed, HTTP_IDEMPOTENCY_KEY="attempt-1")
        self.assertEqual(response.status_code, 400)
        self.assertIn("idempotency_key", response.json()["errors"])
        self.assertEqual(Booking.objects.count(), 1)

    def test_replay_survives_price_status_and_vehicle_visibility_changes(self) -> None:
        first = self.client.post(BOOKINGS, self.payload, HTTP_IDEMPOTENCY_KEY="attempt-1")
        booking = first.json()["data"]
        self.client.post(f"{BOOKINGS}{booking['uuid']}/cancel/", {"note": "Plans changed"})
        Vehicle.objects.filter(pk=self.vehicle.pk).update(status="INACTIVE", daily_rate="9999")
        retry = self.client.post(BOOKINGS, self.payload, HTTP_IDEMPOTENCY_KEY="attempt-1")
        self.assertEqual(retry.status_code, 201, retry.content)
        self.assertEqual(retry.json()["data"]["uuid"], booking["uuid"])
        self.assertEqual(retry.json()["data"]["total_amount"], booking["total_amount"])
        self.assertEqual(retry.json()["data"]["status"], "CANCELLED")

    def test_invalid_idempotency_keys_return_validation_instead_of_database_errors(self) -> None:
        for key in ("a" * 256, "has spaces", "bad\tkey", "ümlaut"):
            response = self.client.post(BOOKINGS, self.payload, HTTP_IDEMPOTENCY_KEY=key)
            self.assertEqual(response.status_code, 400, response.content)
        self.assertFalse(Booking.objects.exists())

    def test_list_query_count_is_bounded_with_vehicle_images(self) -> None:
        for index in range(12):
            vehicle = Vehicle.objects.create(
                **vehicle_data(registration_number=f"MOBILE{index}", vin=None)
            )
            VehicleImage.objects.create(
                vehicle=vehicle, image=f"vehicle-images/{index}.jpg", is_primary=True
            )
            create_booking(actor=self.customer, data={**self.payload, "vehicle": str(vehicle.uuid)})
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(BOOKINGS)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["count"], 12)
        self.assertLessEqual(len(captured), 6)
        self.assertTrue(
            all(
                row["vehicle_details"]["primary_image"]
                for row in response.json()["data"]["results"]
            )
        )


class OperationalScheduleProtectionTests(APITestCase):
    def setUp(self) -> None:
        self.customer = User.objects.create_user(email="schedule@example.com", password=PASSWORD)
        self.admin = User.objects.create_user(
            email="schedule-admin@example.com", password=PASSWORD, role=Role.ADMIN
        )
        self.vehicle = Vehicle.objects.create(**vehicle_data())
        self.pickup = timezone.localdate() + timedelta(days=20)
        self.payload = {
            "vehicle": str(self.vehicle.uuid),
            "pickup_date": self.pickup.isoformat(),
            "return_date": (self.pickup + timedelta(days=3)).isoformat(),
        }
        self.closure = {
            "vehicle": str(self.vehicle.uuid),
            "start_date": self.pickup.isoformat(),
            "end_date": (self.pickup + timedelta(days=1)).isoformat(),
        }

    def test_closures_cannot_invalidate_reserved_bookings(self) -> None:
        booking = create_booking(actor=self.customer, data=self.payload)
        with self.assertRaises(ValidationError):
            save_block(actor=self.admin, data={**self.closure, "reason": "Private operation"})
        with self.assertRaises(ValidationError):
            create_maintenance_record(
                actor=self.admin, data={**self.closure, "maintenance_type": "REPAIR"}
            )
        self.assertFalse(AvailabilityBlock.objects.exists())
        self.assertFalse(MaintenanceRecord.objects.exists())
        transition_booking(
            actor=self.customer, booking_uuid=booking.uuid, target_status="CANCELLED"
        )
        self.assertIsNotNone(
            save_block(actor=self.admin, data={**self.closure, "reason": "Service"}).pk
        )

    def test_inactive_closures_cannot_be_reactivated_across_a_reserved_booking(self) -> None:
        block = save_block(
            actor=self.admin, data={**self.closure, "reason": "Service", "is_active": False}
        )
        record = create_maintenance_record(
            actor=self.admin,
            data={**self.closure, "maintenance_type": "REPAIR", "status": "CANCELLED"},
        )
        create_booking(actor=self.customer, data=self.payload)
        with self.assertRaises(ValidationError):
            save_block(actor=self.admin, block_uuid=block.uuid, data={"is_active": True})
        with self.assertRaises(ValidationError):
            update_maintenance_record(
                actor=self.admin, record_uuid=record.uuid, data={"status": "SCHEDULED"}
            )
        block.refresh_from_db()
        record.refresh_from_db()
        self.assertFalse(block.is_active)
        self.assertEqual(record.status, "CANCELLED")

    def test_inclusive_closure_endpoint_and_adjacent_booking_boundary(self) -> None:
        create_booking(actor=self.customer, data=self.payload)
        previous = (self.pickup - timedelta(days=1)).isoformat()
        save_block(
            actor=self.admin,
            data={**self.closure, "reason": "Before", "start_date": previous, "end_date": previous},
        )
        with self.assertRaises(ValidationError):
            save_block(
                actor=self.admin,
                data={
                    **self.closure,
                    "reason": "Overlap",
                    "start_date": previous,
                    "end_date": self.pickup.isoformat(),
                },
            )
        returned = self.payload["return_date"]
        save_block(
            actor=self.admin,
            data={**self.closure, "reason": "After", "start_date": returned, "end_date": returned},
        )


class ConcurrentOperationalBookingTests(TransactionTestCase):
    def test_booking_and_closure_writers_share_the_same_vehicle_lock(self) -> None:
        customer = User.objects.create_user(email="race@example.com", password=PASSWORD)
        admin = User.objects.create_user(
            email="race-admin@example.com", password=PASSWORD, role=Role.ADMIN
        )
        pickup = timezone.localdate() + timedelta(days=10)

        for kind in ("block", "maintenance"):
            with self.subTest(kind=kind):
                vehicle = Vehicle.objects.create(
                    **vehicle_data(registration_number=f"RACE{kind}", vin=None)
                )
                barrier = Barrier(2)

                def attempt(
                    is_booking: bool,
                    vehicle: Vehicle = vehicle,
                    barrier: Barrier = barrier,
                    kind: str = kind,
                ) -> str:
                    close_old_connections()
                    try:
                        with connection.cursor() as cursor:
                            cursor.execute("SET statement_timeout = '10s'")
                        barrier.wait(timeout=10)
                        try:
                            if is_booking:
                                create_booking(
                                    actor=customer,
                                    data={
                                        "vehicle": str(vehicle.uuid),
                                        "pickup_date": pickup.isoformat(),
                                        "return_date": (pickup + timedelta(days=3)).isoformat(),
                                    },
                                )
                            else:
                                values = {
                                    "vehicle": str(vehicle.uuid),
                                    "start_date": pickup.isoformat(),
                                    "end_date": (pickup + timedelta(days=1)).isoformat(),
                                }
                                if kind == "block":
                                    save_block(actor=admin, data={**values, "reason": "Race test"})
                                else:
                                    create_maintenance_record(
                                        actor=admin, data={**values, "maintenance_type": "REPAIR"}
                                    )
                            return "ok"
                        except ValidationError:
                            return "conflict"
                    finally:
                        close_old_connections()

                with ThreadPoolExecutor(max_workers=2) as executor:
                    attempts = [executor.submit(attempt, flag) for flag in (True, False)]
                    outcomes = [task.result(timeout=15) for task in attempts]
                self.assertEqual(sorted(outcomes), ["conflict", "ok"])
                reservations = Booking.objects.filter(vehicle=vehicle).count()
                reservations += AvailabilityBlock.objects.filter(vehicle=vehicle).count()
                reservations += MaintenanceRecord.objects.filter(vehicle=vehicle).count()
                self.assertEqual(reservations, 1)
