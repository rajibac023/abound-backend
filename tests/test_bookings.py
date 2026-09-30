from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

from django.db import close_old_connections, connection
from django.test import TransactionTestCase
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIClient, APITestCase

from accounts.models import User
from accounts.roles import Role
from accounts.services import login
from audit.models import AuditEvent
from bookings.models import Booking
from bookings.reference import next_booking_reference
from bookings.services import create_booking, transition_booking
from tests.test_vehicles import PASSWORD, vehicle_data
from vehicles.models import Vehicle

BOOKINGS = "/api/v1/bookings/"
ADMIN = "/api/v1/admin/bookings/"


class BookingCreationAPITests(APITestCase):
    def setUp(self) -> None:
        self.customer = User.objects.create_user(email="customer@example.com", password=PASSWORD)
        self.other_customer = User.objects.create_user(email="other@example.com", password=PASSWORD)
        self.staff = User.objects.create_user(
            email="staff@example.com", password=PASSWORD, role=Role.STAFF
        )
        self.vehicle = Vehicle.objects.create(**vehicle_data())
        self.pickup = timezone.localdate() + timedelta(days=10)
        self.payload = {
            "vehicle": str(self.vehicle.uuid),
            "pickup_date": self.pickup.isoformat(),
            "return_date": (self.pickup + timedelta(days=4)).isoformat(),
        }

    def authorize(self, user: User) -> None:
        self.client.force_authenticate(user)

    def test_successful_booking_returns_reference_status_and_snapshot(self) -> None:
        self.authorize(self.customer)
        response = self.client.post(BOOKINGS, self.payload)
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()["data"]
        self.assertRegex(data["booking_reference"], r"^ABH-\d{4}-\d{6}$")
        self.assertEqual(data["status"], "PENDING")
        self.assertEqual(data["rental_days"], 4)
        self.assertEqual(data["subtotal"], "8000.00")
        self.assertEqual(data["discount"], "0.00")
        self.assertEqual(data["total_amount"], "8000.00")
        self.assertEqual(data["currency"], "NPR")
        self.assertEqual(data["customer"], str(self.customer.uuid))
        self.assertEqual(data["vehicle"], str(self.vehicle.uuid))
        for hidden in ("admin_note", "created_by", "idempotency_key"):
            self.assertNotIn(hidden, data)
        self.assertTrue(
            AuditEvent.objects.filter(
                action="bookings.created", subject_uuid=data["uuid"]
            ).exists()
        )

    def test_booking_reference_increments_sequentially(self) -> None:
        self.authorize(self.customer)
        first = self.client.post(BOOKINGS, self.payload).json()["data"]
        later_payload = {
            **self.payload,
            "pickup_date": (self.pickup + timedelta(days=20)).isoformat(),
            "return_date": (self.pickup + timedelta(days=24)).isoformat(),
        }
        second = self.client.post(BOOKINGS, later_payload).json()["data"]
        first_seq = int(first["booking_reference"].rsplit("-", 1)[1])
        second_seq = int(second["booking_reference"].rsplit("-", 1)[1])
        self.assertEqual(second_seq, first_seq + 1)

    def test_reference_sequence_is_independent_per_year(self) -> None:
        from datetime import date

        first = next_booking_reference(as_of=date(2030, 1, 1))
        second = next_booking_reference(as_of=date(2030, 6, 1))
        third = next_booking_reference(as_of=date(2031, 1, 1))
        self.assertEqual(first, "ABH-2030-000001")
        self.assertEqual(second, "ABH-2030-000002")
        self.assertEqual(third, "ABH-2031-000001")

    def test_unavailable_vehicle_status_returns_404(self) -> None:
        Vehicle.objects.filter(pk=self.vehicle.pk).update(status="MAINTENANCE")
        self.authorize(self.customer)
        response = self.client.post(BOOKINGS, self.payload)
        self.assertEqual(response.status_code, 404)
        self.assertFalse(Booking.objects.exists())

    def test_overlapping_booking_for_another_customer_is_rejected(self) -> None:
        self.authorize(self.customer)
        self.assertEqual(self.client.post(BOOKINGS, self.payload).status_code, 201)
        self.authorize(self.other_customer)
        response = self.client.post(BOOKINGS, self.payload)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Booking.objects.filter(vehicle=self.vehicle).count(), 1)

    def test_invalid_and_past_dates_are_rejected(self) -> None:
        self.authorize(self.customer)
        for returned in (
            self.payload["pickup_date"],
            (self.pickup - timedelta(days=1)).isoformat(),
            "not-a-date",
        ):
            response = self.client.post(BOOKINGS, {**self.payload, "return_date": returned})
            self.assertEqual(response.status_code, 400, returned)
        past_pickup = (timezone.localdate() - timedelta(days=1)).isoformat()
        response = self.client.post(BOOKINGS, {**self.payload, "pickup_date": past_pickup})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Booking.objects.exists())

    def test_client_cannot_supply_price_status_or_reference(self) -> None:
        self.authorize(self.customer)
        for field in (
            "booking_reference",
            "status",
            "subtotal",
            "total_amount",
            "rental_days",
            "created_by",
        ):
            response = self.client.post(BOOKINGS, {**self.payload, field: "0"})
            self.assertEqual(response.status_code, 400, field)

    def test_repeated_request_with_same_idempotency_key_returns_the_same_booking(self) -> None:
        self.authorize(self.customer)
        first = self.client.post(BOOKINGS, self.payload, HTTP_IDEMPOTENCY_KEY="retry-key-1")
        second = self.client.post(BOOKINGS, self.payload, HTTP_IDEMPOTENCY_KEY="retry-key-1")
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(first.json()["data"]["uuid"], second.json()["data"]["uuid"])
        self.assertEqual(Booking.objects.filter(vehicle=self.vehicle).count(), 1)

    def test_different_idempotency_keys_create_different_bookings(self) -> None:
        self.authorize(self.customer)
        first = self.client.post(BOOKINGS, self.payload, HTTP_IDEMPOTENCY_KEY="key-a")
        later_payload = {
            **self.payload,
            "pickup_date": (self.pickup + timedelta(days=30)).isoformat(),
            "return_date": (self.pickup + timedelta(days=34)).isoformat(),
        }
        second = self.client.post(BOOKINGS, later_payload, HTTP_IDEMPOTENCY_KEY="key-b")
        self.assertNotEqual(first.json()["data"]["uuid"], second.json()["data"]["uuid"])

    def test_staff_can_create_for_a_customer_but_a_customer_cannot_create_for_others(self) -> None:
        self.authorize(self.customer)
        response = self.client.post(
            BOOKINGS, {**self.payload, "customer": str(self.other_customer.uuid)}
        )
        self.assertEqual(response.status_code, 403)
        self.authorize(self.staff)
        response = self.client.post(
            BOOKINGS, {**self.payload, "customer": str(self.other_customer.uuid)}
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["data"]["customer"], str(self.other_customer.uuid))

    def test_anonymous_cannot_create_or_list(self) -> None:
        self.assertEqual(self.client.post(BOOKINGS, self.payload).status_code, 401)
        self.assertEqual(self.client.get(BOOKINGS).status_code, 401)


class BookingListDetailAPITests(APITestCase):
    def setUp(self) -> None:
        self.customer = User.objects.create_user(email="customer@example.com", password=PASSWORD)
        self.other_customer = User.objects.create_user(email="other@example.com", password=PASSWORD)
        self.staff = User.objects.create_user(
            email="staff@example.com", password=PASSWORD, role=Role.STAFF
        )
        self.vehicle = Vehicle.objects.create(**vehicle_data())
        pickup = timezone.localdate() + timedelta(days=10)
        self.mine = create_booking(
            actor=self.customer,
            data={
                "vehicle": str(self.vehicle.uuid),
                "pickup_date": pickup.isoformat(),
                "return_date": (pickup + timedelta(days=2)).isoformat(),
            },
        )
        other_vehicle = Vehicle.objects.create(
            **vehicle_data(registration_number="OTHERBOOK", vin=None)
        )
        self.theirs = create_booking(
            actor=self.other_customer,
            data={
                "vehicle": str(other_vehicle.uuid),
                "pickup_date": pickup.isoformat(),
                "return_date": (pickup + timedelta(days=2)).isoformat(),
            },
        )

    def test_customer_sees_only_their_own_bookings(self) -> None:
        self.client.force_authenticate(self.customer)
        results = self.client.get(BOOKINGS).json()["data"]["results"]
        self.assertEqual([item["uuid"] for item in results], [str(self.mine.uuid)])
        self.assertEqual(
            self.client.get(f"{BOOKINGS}{self.mine.uuid}/").status_code, 200
        )
        self.assertEqual(
            self.client.get(f"{BOOKINGS}{self.theirs.uuid}/").status_code, 404
        )

    def test_staff_sees_no_bookings_via_the_customer_route_but_all_via_admin(self) -> None:
        self.client.force_authenticate(self.staff)
        results = self.client.get(BOOKINGS).json()["data"]["results"]
        self.assertEqual(results, [])
        admin_results = self.client.get(ADMIN).json()["data"]["results"]
        self.assertEqual(
            {item["uuid"] for item in admin_results}, {str(self.mine.uuid), str(self.theirs.uuid)}
        )

    def test_admin_search_matches_reference_or_customer_identity(self) -> None:
        self.client.force_authenticate(self.staff)
        by_reference = self.client.get(ADMIN, {"search": self.mine.booking_reference}).json()
        self.assertEqual(
            [item["uuid"] for item in by_reference["data"]["results"]], [str(self.mine.uuid)]
        )
        by_email = self.client.get(ADMIN, {"search": "other@example.com"}).json()
        self.assertEqual(
            [item["uuid"] for item in by_email["data"]["results"]], [str(self.theirs.uuid)]
        )
        by_nothing = self.client.get(ADMIN, {"search": "no-such-booking"}).json()
        self.assertEqual(by_nothing["data"]["results"], [])

    def test_customer_cannot_reach_admin_routes(self) -> None:
        self.client.force_authenticate(self.customer)
        self.assertEqual(self.client.get(ADMIN).status_code, 403)
        self.assertEqual(self.client.get(f"{ADMIN}{self.mine.uuid}/").status_code, 403)


class BookingStatusTransitionTests(APITestCase):
    def setUp(self) -> None:
        self.customer = User.objects.create_user(email="customer@example.com", password=PASSWORD)
        self.other_customer = User.objects.create_user(email="other@example.com", password=PASSWORD)
        self.staff = User.objects.create_user(
            email="staff@example.com", password=PASSWORD, role=Role.STAFF
        )
        self.admin = User.objects.create_user(
            email="admin@example.com", password=PASSWORD, role=Role.ADMIN
        )
        self.vehicle = Vehicle.objects.create(**vehicle_data())
        self.pickup = timezone.localdate() + timedelta(days=10)
        self.booking = create_booking(
            actor=self.customer,
            data={
                "vehicle": str(self.vehicle.uuid),
                "pickup_date": self.pickup.isoformat(),
                "return_date": (self.pickup + timedelta(days=3)).isoformat(),
            },
        )

    def test_customer_can_cancel_their_own_pending_booking(self) -> None:
        self.client.force_authenticate(self.customer)
        response = self.client.post(
            f"{BOOKINGS}{self.booking.uuid}/cancel/", {"note": "Change of plans"}
        )
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        self.assertEqual(data["status"], "CANCELLED")
        self.assertEqual(data["customer_note"], "Change of plans")
        self.assertTrue(
            AuditEvent.objects.filter(
                action="bookings.status_changed", subject_uuid=self.booking.uuid
            ).exists()
        )

    def test_customer_cannot_cancel_someone_elses_booking(self) -> None:
        self.client.force_authenticate(self.other_customer)
        response = self.client.post(f"{BOOKINGS}{self.booking.uuid}/cancel/")
        self.assertEqual(response.status_code, 404)

    def test_customer_cannot_confirm_or_activate_via_service(self) -> None:
        with self.assertRaises(PermissionDenied):
            transition_booking(
                actor=self.customer, booking_uuid=self.booking.uuid, target_status="CONFIRMED"
            )

    def test_admin_confirm_activate_complete_flow(self) -> None:
        self.client.force_authenticate(self.admin)
        url = f"{ADMIN}{self.booking.uuid}/transition/"
        response = self.client.post(url, {"status": "CONFIRMED"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["data"]["status"], "CONFIRMED")
        response = self.client.post(url, {"status": "ACTIVE"})
        self.assertEqual(response.json()["data"]["status"], "ACTIVE")
        response = self.client.post(url, {"status": "COMPLETED", "admin_note": "Returned clean"})
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        self.assertEqual(data["status"], "COMPLETED")

    def test_admin_can_reject_a_pending_booking(self) -> None:
        self.client.force_authenticate(self.admin)
        url = f"{ADMIN}{self.booking.uuid}/transition/"
        response = self.client.post(url, {"status": "REJECTED"})
        self.assertEqual(response.json()["data"]["status"], "REJECTED")

    def test_invalid_transitions_are_rejected(self) -> None:
        self.client.force_authenticate(self.admin)
        url = f"{ADMIN}{self.booking.uuid}/transition/"
        # PENDING cannot skip straight to ACTIVE or COMPLETED.
        self.assertEqual(self.client.post(url, {"status": "ACTIVE"}).status_code, 400)
        self.assertEqual(self.client.post(url, {"status": "COMPLETED"}).status_code, 400)
        self.client.post(url, {"status": "REJECTED"})
        # REJECTED is terminal.
        self.assertEqual(self.client.post(url, {"status": "CONFIRMED"}).status_code, 400)

    def test_transition_endpoint_rejects_cancelled_status(self) -> None:
        self.client.force_authenticate(self.admin)
        url = f"{ADMIN}{self.booking.uuid}/transition/"
        self.assertEqual(self.client.post(url, {"status": "CANCELLED"}).status_code, 400)

    def test_staff_with_cancel_permission_can_use_admin_cancel(self) -> None:
        self.client.force_authenticate(self.staff)
        response = self.client.post(f"{ADMIN}{self.booking.uuid}/cancel/", {"note": "Ops call"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["data"]["admin_note"], "Ops call")

    def test_customer_cannot_reach_admin_cancel_route(self) -> None:
        self.client.force_authenticate(self.customer)
        response = self.client.post(f"{ADMIN}{self.booking.uuid}/cancel/")
        self.assertEqual(response.status_code, 403)

    def test_admin_can_update_the_admin_note_without_changing_status(self) -> None:
        self.client.force_authenticate(self.admin)
        url = f"{ADMIN}{self.booking.uuid}/"
        response = self.client.patch(url, {"admin_note": "Customer called about pickup time"})
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        self.assertEqual(data["admin_note"], "Customer called about pickup time")
        self.assertEqual(data["status"], "PENDING")
        self.assertTrue(
            AuditEvent.objects.filter(
                action="bookings.note_updated", subject_uuid=self.booking.uuid
            ).exists()
        )

    def test_staff_can_update_note_but_customer_and_anonymous_cannot(self) -> None:
        url = f"{ADMIN}{self.booking.uuid}/"
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.client.patch(url, {"admin_note": "Staff note"}).status_code, 200)
        self.client.force_authenticate(self.customer)
        self.assertEqual(self.client.patch(url, {"admin_note": "Nope"}).status_code, 403)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.patch(url, {"admin_note": "Nope"}).status_code, 401)

    def test_cancelling_frees_the_vehicle_for_a_new_booking(self) -> None:
        transition_booking(
            actor=self.customer, booking_uuid=self.booking.uuid, target_status="CANCELLED"
        )
        replacement = create_booking(
            actor=self.other_customer,
            data={
                "vehicle": str(self.vehicle.uuid),
                "pickup_date": self.pickup.isoformat(),
                "return_date": (self.pickup + timedelta(days=3)).isoformat(),
            },
        )
        self.assertIsNotNone(replacement.uuid)

    def test_completed_booking_still_blocks_future_overlap_check_is_not_required(self) -> None:
        # A COMPLETED booking is historical; its dates must free up like any resolved booking.
        transition_booking(
            actor=self.admin, booking_uuid=self.booking.uuid, target_status="CONFIRMED"
        )
        transition_booking(
            actor=self.admin, booking_uuid=self.booking.uuid, target_status="ACTIVE"
        )
        transition_booking(
            actor=self.admin, booking_uuid=self.booking.uuid, target_status="COMPLETED"
        )
        replacement = create_booking(
            actor=self.other_customer,
            data={
                "vehicle": str(self.vehicle.uuid),
                "pickup_date": self.pickup.isoformat(),
                "return_date": (self.pickup + timedelta(days=3)).isoformat(),
            },
        )
        self.assertIsNotNone(replacement.uuid)

    def test_price_snapshot_is_unaffected_by_status_transitions(self) -> None:
        snapshot = {
            "subtotal": self.booking.subtotal,
            "total_amount": self.booking.total_amount,
            "daily_rate_snapshot": self.booking.daily_rate_snapshot,
        }
        transition_booking(
            actor=self.admin, booking_uuid=self.booking.uuid, target_status="CONFIRMED"
        )
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.subtotal, snapshot["subtotal"])
        self.assertEqual(self.booking.total_amount, snapshot["total_amount"])
        self.assertEqual(self.booking.daily_rate_snapshot, snapshot["daily_rate_snapshot"])


class ConcurrentBookingCreationTests(TransactionTestCase):
    def test_two_customers_booking_the_same_vehicle_only_one_succeeds(self) -> None:
        customer_a = User.objects.create_user(email="a@example.com", password=PASSWORD)
        customer_b = User.objects.create_user(email="b@example.com", password=PASSWORD)
        vehicle = Vehicle.objects.create(**vehicle_data())
        pickup = timezone.localdate() + timedelta(days=15)
        payload = {
            "vehicle": str(vehicle.uuid),
            "pickup_date": pickup.isoformat(),
            "return_date": (pickup + timedelta(days=3)).isoformat(),
        }
        tokens = [
            login(email=user.email, password=PASSWORD, client="mobile")[1]["access"]
            for user in (customer_a, customer_b)
        ]
        barrier = Barrier(2)

        def attempt(access: str) -> int:
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET statement_timeout = '10s'")
                client = APIClient()
                client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
                barrier.wait(timeout=10)
                return client.post(BOOKINGS, payload).status_code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            tasks = [executor.submit(attempt, token) for token in tokens]
            results = sorted(task.result(timeout=15) for task in tasks)
        self.assertEqual(results, [201, 400])
        self.assertEqual(Booking.objects.filter(vehicle=vehicle).count(), 1)

    def test_concurrent_retries_with_the_same_idempotency_key_create_one_booking(self) -> None:
        customer = User.objects.create_user(email="retry@example.com", password=PASSWORD)
        vehicle = Vehicle.objects.create(**vehicle_data())
        pickup = timezone.localdate() + timedelta(days=16)
        payload = {
            "vehicle": str(vehicle.uuid),
            "pickup_date": pickup.isoformat(),
            "return_date": (pickup + timedelta(days=2)).isoformat(),
        }
        access = login(email=customer.email, password=PASSWORD, client="mobile")[1]["access"]
        barrier = Barrier(2)

        def attempt() -> int:
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET statement_timeout = '10s'")
                client = APIClient()
                client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
                barrier.wait(timeout=10)
                return client.post(BOOKINGS, payload, HTTP_IDEMPOTENCY_KEY="same-key").status_code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            tasks = [executor.submit(attempt) for _ in range(2)]
            results = [task.result(timeout=15) for task in tasks]
        self.assertEqual(results, [201, 201])
        self.assertEqual(Booking.objects.filter(vehicle=vehicle).count(), 1)


class BookingModelConstraintTests(APITestCase):
    def test_status_and_reference_constraints_are_enforced_by_postgresql(self) -> None:
        from django.db import IntegrityError, transaction

        customer = User.objects.create_user(email="customer@example.com", password=PASSWORD)
        vehicle = Vehicle.objects.create(**vehicle_data())
        pickup = timezone.localdate() + timedelta(days=10)
        booking = create_booking(
            actor=customer,
            data={
                "vehicle": str(vehicle.uuid),
                "pickup_date": pickup.isoformat(),
                "return_date": (pickup + timedelta(days=2)).isoformat(),
            },
        )
        for values in ({"status": "BOGUS"}, {"booking_reference": "not-a-reference"}):
            with self.assertRaises(IntegrityError), transaction.atomic():
                Booking.objects.filter(pk=booking.pk).update(**values)
