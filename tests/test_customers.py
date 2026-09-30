from datetime import timedelta

from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import AuthSession, User
from accounts.roles import Role
from accounts.services import login
from audit.models import AuditEvent
from bookings.services import create_booking
from tests.test_vehicles import PASSWORD, vehicle_data
from vehicles.models import Vehicle

ME = "/api/v1/auth/me/"
ADMIN = "/api/v1/admin/customers/"
BOOKINGS = "/api/v1/bookings/"
ADMIN_BOOKINGS = "/api/v1/admin/bookings/"


class CustomerSelfServiceTests(APITestCase):
    def setUp(self) -> None:
        self.customer = User.objects.create_user(
            email="jane@example.com",
            password=PASSWORD,
            first_name="Jane",
            last_name="Doe",
            phone="9800000000",
        )
        self.other_customer = User.objects.create_user(
            email="other@example.com", password=PASSWORD
        )
        self.client.force_authenticate(self.customer)

    def test_customer_can_view_their_own_profile(self) -> None:
        response = self.client.get(ME)
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["email"], "jane@example.com")
        self.assertEqual(data["first_name"], "Jane")
        self.assertEqual(data["role"], "CUSTOMER")
        self.assertEqual(data["uuid"], str(self.customer.uuid))
        self.assertNotIn("password", data)

    def test_customer_can_update_permitted_fields(self) -> None:
        response = self.client.patch(ME, {"first_name": "Janet", "phone": "9811111111"})
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        self.assertEqual(data["first_name"], "Janet")
        self.assertEqual(data["phone"], "9811111111")
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.first_name, "Janet")
        self.assertTrue(
            AuditEvent.objects.filter(
                action="accounts.profile_updated", subject_uuid=self.customer.uuid
            ).exists()
        )

    def test_customer_cannot_change_email_role_or_active_status_via_profile(self) -> None:
        for payload in (
            {"email": "new@example.com"},
            {"role": "ADMIN"},
            {"is_active": False},
            {"uuid": str(self.other_customer.uuid)},
        ):
            response = self.client.patch(ME, payload)
            self.assertEqual(response.status_code, 400, payload)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.email, "jane@example.com")
        self.assertEqual(self.customer.role, Role.CUSTOMER)
        self.assertTrue(self.customer.is_active)

    def test_empty_update_is_rejected(self) -> None:
        self.assertEqual(self.client.patch(ME, {}).status_code, 400)

    def test_anonymous_cannot_view_or_update_profile(self) -> None:
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(ME).status_code, 401)
        self.assertEqual(self.client.patch(ME, {"first_name": "X"}).status_code, 401)


class AdminCustomerManagementAPITests(APITestCase):
    def setUp(self) -> None:
        self.admin = User.objects.create_user(
            email="admin@example.com", password=PASSWORD, role=Role.ADMIN
        )
        self.staff = User.objects.create_user(
            email="staff@example.com", password=PASSWORD, role=Role.STAFF
        )
        self.alice = User.objects.create_user(
            email="alice@example.com",
            password=PASSWORD,
            first_name="Alice",
            last_name="Anderson",
            phone="9800000001",
        )
        self.bob = User.objects.create_user(
            email="bob@example.com",
            password=PASSWORD,
            first_name="Bob",
            last_name="Brown",
            phone="9800000002",
            is_active=False,
        )
        self.vehicle = Vehicle.objects.create(**vehicle_data())

    def authorize(self, user: User) -> None:
        self.client.force_authenticate(user)

    def test_admin_can_search_customers_by_name_email_or_phone(self) -> None:
        self.authorize(self.admin)
        for query in ("alice", "Anderson", "alice@example.com", "9800000001"):
            response = self.client.get(ADMIN, {"search": query})
            results = response.json()["data"]["results"]
            self.assertEqual([r["uuid"] for r in results], [str(self.alice.uuid)], query)

    def test_admin_can_filter_by_status(self) -> None:
        self.authorize(self.admin)
        active = self.client.get(ADMIN, {"status": "active"}).json()["data"]["results"]
        self.assertEqual({r["uuid"] for r in active}, {str(self.alice.uuid)})
        inactive = self.client.get(ADMIN, {"status": "inactive"}).json()["data"]["results"]
        self.assertEqual({r["uuid"] for r in inactive}, {str(self.bob.uuid)})

    def test_admin_list_never_includes_staff_or_admin_accounts(self) -> None:
        self.authorize(self.admin)
        results = self.client.get(ADMIN).json()["data"]["results"]
        uuids = {r["uuid"] for r in results}
        self.assertNotIn(str(self.admin.uuid), uuids)
        self.assertNotIn(str(self.staff.uuid), uuids)

    def test_admin_can_view_customer_profile(self) -> None:
        self.authorize(self.admin)
        response = self.client.get(f"{ADMIN}{self.alice.uuid}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["email"], "alice@example.com")

    def test_admin_can_view_customer_booking_history(self) -> None:
        pickup = timezone.localdate() + timedelta(days=10)
        booking = create_booking(
            actor=self.alice,
            data={
                "vehicle": str(self.vehicle.uuid),
                "pickup_date": pickup.isoformat(),
                "return_date": (pickup + timedelta(days=2)).isoformat(),
            },
        )
        self.authorize(self.admin)
        response = self.client.get(ADMIN_BOOKINGS, {"customer": str(self.alice.uuid)})
        self.assertEqual(response.status_code, 200, response.content)
        results = response.json()["data"]["results"]
        self.assertEqual([r["uuid"] for r in results], [str(booking.uuid)])

    def test_admin_can_update_permitted_fields(self) -> None:
        self.authorize(self.admin)
        response = self.client.patch(
            f"{ADMIN}{self.alice.uuid}/", {"phone": "9822222222", "last_name": "Aardvark"}
        )
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        self.assertEqual(data["phone"], "9822222222")
        self.assertEqual(data["last_name"], "Aardvark")
        self.assertTrue(
            AuditEvent.objects.filter(
                action="customers.updated", subject_uuid=self.alice.uuid
            ).exists()
        )

    def test_admin_cannot_change_role_through_customer_endpoint(self) -> None:
        self.authorize(self.admin)
        response = self.client.patch(f"{ADMIN}{self.alice.uuid}/", {"role": "STAFF"})
        self.assertEqual(response.status_code, 400)
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.role, Role.CUSTOMER)

    def test_admin_can_deactivate_a_customer_and_it_revokes_sessions(self) -> None:
        access = login(email=self.alice.email, password=PASSWORD, client="mobile")[1]["access"]
        session = AuthSession.objects.get(user=self.alice, revoked_at__isnull=True)
        self.authorize(self.admin)
        response = self.client.patch(f"{ADMIN}{self.alice.uuid}/", {"is_active": False})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(response.json()["data"]["is_active"])
        session.refresh_from_db()
        self.assertIsNotNone(session.revoked_at)
        probe = self.client_class()
        probe.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        self.assertEqual(probe.get(ME).status_code, 401)

    def test_staff_can_search_and_view_but_not_update_or_deactivate(self) -> None:
        self.authorize(self.staff)
        self.assertEqual(self.client.get(ADMIN).status_code, 200)
        self.assertEqual(self.client.get(f"{ADMIN}{self.alice.uuid}/").status_code, 200)
        response = self.client.patch(f"{ADMIN}{self.alice.uuid}/", {"phone": "9800000009"})
        self.assertEqual(response.status_code, 403)
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.phone, "9800000001")

    def test_staff_cannot_see_customers_through_generic_user_admin_routes(self) -> None:
        self.authorize(self.staff)
        self.assertEqual(self.client.get("/api/v1/admin/users/").status_code, 403)

    def test_customer_role_and_anonymous_are_denied_admin_routes(self) -> None:
        for user in (None, self.alice):
            self.client.force_authenticate(user)
            self.assertIn(self.client.get(ADMIN).status_code, (401, 403))
            self.assertIn(self.client.get(f"{ADMIN}{self.alice.uuid}/").status_code, (401, 403))

    def test_invalid_status_filter_is_rejected(self) -> None:
        self.authorize(self.admin)
        self.assertEqual(self.client.get(ADMIN, {"status": "bogus"}).status_code, 400)

    def test_empty_admin_update_is_rejected(self) -> None:
        self.authorize(self.admin)
        self.assertEqual(self.client.patch(f"{ADMIN}{self.alice.uuid}/", {}).status_code, 400)


class CustomerIsolationTests(APITestCase):
    """A customer must never reach another customer's account or booking data."""

    def setUp(self) -> None:
        self.alice = User.objects.create_user(email="alice@example.com", password=PASSWORD)
        self.bob = User.objects.create_user(email="bob@example.com", password=PASSWORD)
        self.vehicle = Vehicle.objects.create(**vehicle_data())
        pickup = timezone.localdate() + timedelta(days=10)
        self.bobs_booking = create_booking(
            actor=self.bob,
            data={
                "vehicle": str(self.vehicle.uuid),
                "pickup_date": pickup.isoformat(),
                "return_date": (pickup + timedelta(days=2)).isoformat(),
            },
        )

    def test_customer_cannot_list_another_customers_bookings(self) -> None:
        self.client.force_authenticate(self.alice)
        results = self.client.get(BOOKINGS).json()["data"]["results"]
        self.assertEqual(results, [])

    def test_customer_requesting_anothers_booking_detail_is_denied_appropriately(self) -> None:
        self.client.force_authenticate(self.alice)
        response = self.client.get(f"{BOOKINGS}{self.bobs_booking.uuid}/")
        # 404, not 403: existence of another customer's booking is not confirmed either.
        self.assertEqual(response.status_code, 404)

    def test_customer_cannot_cancel_anothers_booking(self) -> None:
        self.client.force_authenticate(self.alice)
        response = self.client.post(f"{BOOKINGS}{self.bobs_booking.uuid}/cancel/")
        self.assertEqual(response.status_code, 404)
        self.bobs_booking.refresh_from_db()
        self.assertEqual(self.bobs_booking.status, "PENDING")

    def test_customer_cannot_view_another_customers_admin_profile_route(self) -> None:
        self.client.force_authenticate(self.alice)
        response = self.client.get(f"{ADMIN}{self.bob.uuid}/")
        self.assertEqual(response.status_code, 403)

    def test_customer_profile_endpoint_never_exposes_another_account(self) -> None:
        self.client.force_authenticate(self.alice)
        response = self.client.get(ME)
        self.assertEqual(response.json()["data"]["uuid"], str(self.alice.uuid))
        self.assertNotEqual(response.json()["data"]["uuid"], str(self.bob.uuid))
