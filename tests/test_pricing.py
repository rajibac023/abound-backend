from concurrent.futures import ThreadPoolExecutor, TimeoutError
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from threading import Event
from unittest.mock import patch
from uuid import uuid4

from django.db import IntegrityError, close_old_connections, connection, transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.test import APITestCase

from accounts.models import User
from accounts.roles import Role
from accounts.services import login
from audit.models import AuditEvent
from bookings.models import Booking
from bookings.services import create_booking
from pricing import services
from pricing.models import RentalDiscount
from tests.test_vehicles import PASSWORD, vehicle_data
from vehicles.models import Vehicle

QUOTE = "/api/v1/pricing/quote/"
ADMIN = "/api/v1/admin/pricing/"


class PricingAPITests(APITestCase):
    def setUp(self) -> None:
        self.users = {
            role: User.objects.create_user(
                email=f"{role}@example.com", password=PASSWORD, role=role
            )
            for role in Role.values
        }
        self.vehicle = Vehicle.objects.create(**vehicle_data())
        self.pickup = timezone.localdate() + timedelta(days=30)
        self.payload = {
            "vehicle": str(self.vehicle.uuid),
            "pickup_date": self.pickup.isoformat(),
            "return_date": (self.pickup + timedelta(days=8)).isoformat(),
        }
        self.rate_url = f"{ADMIN}vehicles/{self.vehicle.uuid}/"

    def discount(self, **values):
        return RentalDiscount.objects.create(
            label="Public discount",
            kind="PERCENTAGE",
            value=Decimal("10"),
            is_active=True,
            **values,
        )

    def test_public_quote_has_required_format_and_safe_fields(self) -> None:
        response = self.client.post(QUOTE, self.payload)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertTrue(body["success"])
        data = body["data"]
        self.assertEqual(data["rental_days"], 8)
        self.assertEqual(data["subtotal"], "14000.00")
        self.assertEqual(data["discount"], "0.00")
        self.assertEqual(data["total"], "14000.00")
        self.assertEqual(data["currency"], "NPR")
        self.assertEqual(
            [line["pricing_type"] for line in data["pricing_breakdown"]], ["WEEKLY", "DAILY"]
        )
        for secret in (
            "PRIVATE-OPERATIONS-SECRET",
            "registration_number",
            "vin",
            "internal_notes",
            "created_at",
        ):
            self.assertNotIn(secret, str(body))
        self.assertIn("no-store", response["Cache-Control"])

    def test_public_cannot_price_hidden_vehicle_even_with_admin_token(self) -> None:
        self.client.force_authenticate(self.users[Role.ADMIN])
        for status in ("UNAVAILABLE", "MAINTENANCE", "INACTIVE"):
            Vehicle.objects.filter(pk=self.vehicle.pk).update(status=status)
            self.assertEqual(self.client.post(QUOTE, self.payload).status_code, 404)
            self.assertEqual(self.client.post(ADMIN + "quote/", self.payload).status_code, 200)
        self.assertEqual(
            self.client.post(QUOTE, {**self.payload, "vehicle": str(uuid4())}).status_code, 404
        )

    def test_client_cannot_supply_prices_discount_or_duration(self) -> None:
        for field in (
            "daily_rate",
            "weekly_rate",
            "monthly_rate",
            "subtotal",
            "total",
            "discount",
            "rental_days",
            "currency",
            "pricing_breakdown",
            "customer",
        ):
            response = self.client.post(QUOTE, {**self.payload, field: "0"})
            self.assertEqual(response.status_code, 400, (field, response.content))

    def test_invalid_api_dates_and_reversed_ranges(self) -> None:
        for returned in (
            self.payload["pickup_date"],
            (self.pickup - timedelta(days=1)).isoformat(),
            "2028-02-30",
            "2028-01-01T10:30:00Z",
            "not-a-date",
            None,
        ):
            response = self.client.post(QUOTE, {**self.payload, "return_date": returned})
            self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(
            self.client.post(QUOTE, {"vehicle": str(self.vehicle.uuid)}).status_code, 400
        )

    @override_settings(AUTH_RATE_LIMITS={"pricing_quote_ip": (1, 60)})
    def test_quote_endpoint_is_rate_limited(self) -> None:
        self.assertEqual(self.client.post(QUOTE, self.payload).status_code, 200)
        response = self.client.post(QUOTE, self.payload)
        self.assertEqual(response.status_code, 429)
        self.assertIn("Retry-After", response)

    def test_discount_scope_dates_minimum_and_activation(self) -> None:
        rule = self.discount(
            vehicle=self.vehicle,
            starts_on=self.pickup,
            ends_on=self.pickup + timedelta(days=8),
            minimum_days=8,
        )
        self.assertEqual(
            self.client.post(QUOTE, self.payload).json()["data"]["discount"], "1400.00"
        )
        other = Vehicle.objects.create(**vehicle_data(registration_number="OTHERPRICE", vin=None))
        for changes in (
            {"is_active": False},
            {"minimum_days": 9},
            {"starts_on": self.pickup + timedelta(days=1)},
            {"ends_on": self.pickup + timedelta(days=7)},
            {"vehicle": other},
        ):
            with transaction.atomic():
                RentalDiscount.objects.filter(pk=rule.pk).update(**changes)
                self.assertEqual(
                    self.client.post(QUOTE, self.payload).json()["data"]["discount"], "0.00"
                )
                transaction.set_rollback(True)

    def test_global_and_vehicle_discounts_choose_best_without_stacking(self) -> None:
        self.discount()
        rule = RentalDiscount.objects.create(
            label="Fixed offer",
            kind="FIXED",
            value=Decimal("2000"),
            vehicle=self.vehicle,
            is_active=True,
        )
        data = self.client.post(QUOTE, self.payload).json()["data"]
        self.assertEqual(data["discount"], "2000.00")
        self.assertEqual(data["total"], "12000.00")
        self.assertEqual(data["discount_breakdown"][0]["uuid"], str(rule.uuid))
        self.assertEqual(len(data["discount_breakdown"]), 1)

    def test_admin_rates_update_existing_vehicle_source_of_truth(self) -> None:
        self.client.force_authenticate(self.users[Role.ADMIN])
        response = self.client.patch(self.rate_url, {"daily_rate": "3000.00", "weekly_rate": None})
        self.assertEqual(response.status_code, 200, response.content)
        self.vehicle.refresh_from_db()
        self.assertEqual(self.vehicle.daily_rate, Decimal("3000.00"))
        self.assertIsNone(self.vehicle.weekly_rate)
        self.assertEqual(self.client.post(QUOTE, self.payload).json()["data"]["total"], "24000.00")
        self.assertEqual(AuditEvent.objects.filter(action="pricing.rates_updated").count(), 1)
        for changes in (
            {"daily_rate": None},
            {"daily_rate": "-1"},
            {"daily_rate": "NaN"},
            {"daily_rate": "0.001"},
            {"currency": "USD"},
            {"status": "AVAILABLE"},
        ):
            self.assertEqual(self.client.patch(self.rate_url, changes).status_code, 400)

    def test_discount_crud_validation_and_soft_deletion(self) -> None:
        self.client.force_authenticate(self.users[Role.ADMIN])
        payload = {
            "label": "Summer offer",
            "vehicle": str(self.vehicle.uuid),
            "kind": "PERCENTAGE",
            "value": "12.50",
        }
        response = self.client.post(ADMIN + "discounts/", payload)
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()["data"]
        self.assertFalse(data["is_active"])
        url = ADMIN + "discounts/" + data["uuid"] + "/"
        self.assertEqual(self.client.patch(url, {"is_active": True}).status_code, 200)
        self.assertEqual(
            self.client.post(QUOTE, self.payload).json()["data"]["discount"], "1750.00"
        )
        self.assertEqual(self.client.delete(url).status_code, 200)
        self.assertTrue(RentalDiscount.objects.filter(uuid=data["uuid"]).exists())
        self.assertEqual(self.client.post(QUOTE, self.payload).json()["data"]["discount"], "0.00")
        for changes in (
            {"value": "101"},
            {"value": "-1"},
            {"value": "NaN"},
            {"minimum_days": 0},
            {"starts_on": "2028-01-03", "ends_on": "2028-01-03"},
            {"currency": "USD"},
            {"kind": "OTHER"},
            {"vehicle": str(uuid4())},
        ):
            self.assertEqual(self.client.patch(url, changes).status_code, 400, changes)

    def test_every_role_enforces_pricing_grants(self) -> None:
        rule = self.discount()
        url = f"{ADMIN}discounts/{rule.uuid}/"
        for role in (None, *Role.values):
            with self.subTest(role=role):
                self.client.force_authenticate(self.users[role] if role else None)
                read = role in {Role.SUPER_ADMIN, Role.ADMIN, Role.STAFF}
                write = role in {Role.SUPER_ADMIN, Role.ADMIN}
                denied = 403 if role else 401
                for path in (self.rate_url, ADMIN + "discounts/", url):
                    for method in ("get", "head", "options"):
                        self.assertEqual(
                            getattr(self.client, method)(path).status_code, 200 if read else denied
                        )
                self.assertEqual(
                    self.client.post(ADMIN + "quote/", self.payload).status_code,
                    200 if read else denied,
                )
                self.assertEqual(
                    self.client.patch(self.rate_url, {"daily_rate": "2000.00"}).status_code,
                    200 if write else denied,
                )
                self.assertEqual(
                    self.client.post(
                        ADMIN + "discounts/", {"label": "Role offer", "kind": "FIXED", "value": "1"}
                    ).status_code,
                    201 if write else denied,
                )
                self.assertEqual(
                    self.client.patch(url, {"label": "New label"}).status_code,
                    200 if write else denied,
                )
                self.assertEqual(self.client.delete(url).status_code, 200 if write else denied)

    def test_inactive_admin_and_service_demotion_are_rejected(self) -> None:
        actor = self.users[Role.ADMIN]
        tokens = login(email=actor.email, password=PASSWORD, client="mobile")[1]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        User.objects.filter(pk=actor.pk).update(is_active=False)
        self.assertEqual(self.client.get(self.rate_url).status_code, 401)
        User.objects.filter(pk=actor.pk).update(is_active=True, role=Role.STAFF, is_staff=False)
        with self.assertRaises(PermissionDenied):
            services.update_rates(
                actor=actor, vehicle_uuid=self.vehicle.uuid, data={"daily_rate": "1"}
            )
        with self.assertRaises(PermissionDenied):
            services.save_discount(
                actor=actor, data={"label": "Unauthorized", "kind": "FIXED", "value": "1"}
            )

    def test_pricing_mutations_roll_back_when_audit_fails(self) -> None:
        with patch(
            "pricing.services.record_pricing_event", side_effect=RuntimeError("audit unavailable")
        ):
            with self.assertRaises(RuntimeError):
                services.update_rates(
                    actor=self.users[Role.ADMIN],
                    vehicle_uuid=self.vehicle.uuid,
                    data={"daily_rate": "1"},
                )
            with self.assertRaises(RuntimeError):
                services.save_discount(
                    actor=self.users[Role.ADMIN],
                    data={"label": "Rollback", "kind": "FIXED", "value": "1"},
                )
        self.vehicle.refresh_from_db()
        self.assertEqual(self.vehicle.daily_rate, Decimal("2000.00"))
        self.assertFalse(RentalDiscount.objects.exists())

    def test_postgresql_rejects_invalid_discount_values(self) -> None:
        for data in (
            {"kind": "PERCENTAGE", "value": "101"},
            {"kind": "FIXED", "value": "-1"},
        ):
            with self.assertRaises(IntegrityError), transaction.atomic():
                RentalDiscount.objects.create(label="Invalid", **data)
        rule = self.discount()
        with self.assertRaises(IntegrityError), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "UPDATE pricing_rentaldiscount SET value = 'NaN' WHERE id = %s", [rule.pk]
            )


class BookingPriceSnapshotTests(APITestCase):
    def setUp(self) -> None:
        self.customer = User.objects.create_user(email="customer@example.com", password=PASSWORD)
        self.admin = User.objects.create_user(
            email="admin@example.com", password=PASSWORD, role=Role.ADMIN
        )
        self.vehicle = Vehicle.objects.create(**vehicle_data())
        pickup = timezone.localdate() + timedelta(days=30)
        self.data = {
            "vehicle": str(self.vehicle.uuid),
            "pickup_date": pickup.isoformat(),
            "return_date": (pickup + timedelta(days=38)).isoformat(),
        }

    def create(self, **overrides):
        return create_booking(actor=self.customer, data={**self.data, **overrides})

    def test_creation_copies_all_rates_and_calculated_amounts(self) -> None:
        booking = self.create()
        self.assertEqual(booking.status, Booking.Status.PENDING)
        self.assertEqual(booking.customer, self.customer)
        self.assertEqual(booking.rental_days, 38)
        self.assertEqual(booking.daily_rate_snapshot, Decimal("2000.00"))
        self.assertEqual(booking.weekly_rate_snapshot, Decimal("12000.00"))
        self.assertEqual(booking.monthly_rate_snapshot, Decimal("45000.00"))
        self.assertEqual(booking.subtotal, Decimal("59000.00"))
        self.assertEqual(booking.total_amount, Decimal("59000.00"))
        self.assertEqual(booking.currency, "NPR")
        self.assertEqual(
            sum(Decimal(item["amount"]) for item in booking.pricing_breakdown), booking.subtotal
        )
        self.assertIsNotNone(booking.priced_at.tzinfo)
        self.assertTrue(
            AuditEvent.objects.filter(
                action="bookings.created", subject_uuid=booking.uuid
            ).exists()
        )

    def test_rate_and_discount_changes_never_recalculate_historical_booking(self) -> None:
        rule = services.save_discount(
            actor=self.admin,
            data={
                "label": "Original offer",
                "kind": "PERCENTAGE",
                "value": "10",
                "is_active": True,
            },
        )
        booking = self.create()
        snapshot = deepcopy(
            {
                field.attname: getattr(booking, field.attname)
                for field in Booking._meta.concrete_fields
            }
        )
        services.update_rates(
            actor=self.admin,
            vehicle_uuid=self.vehicle.uuid,
            data={"daily_rate": "4000", "weekly_rate": "20000", "monthly_rate": "80000"},
        )
        services.save_discount(
            actor=self.admin,
            discount_uuid=rule.uuid,
            data={"label": "Changed offer", "value": "50", "is_active": False},
        )
        booking.refresh_from_db()
        self.assertEqual(
            {
                field.attname: getattr(booking, field.attname)
                for field in Booking._meta.concrete_fields
            },
            snapshot,
        )
        self.assertEqual(booking.total_amount, Decimal("53100.00"))
        # A non-overlapping period: the first booking still holds its original dates.
        later = timezone.localdate() + timedelta(days=100)
        new = self.create(
            pickup_date=later.isoformat(), return_date=(later + timedelta(days=38)).isoformat()
        )
        self.assertEqual(new.total_amount, Decimal("104000.00"))
        self.assertEqual(booking.discount_breakdown[0]["label"], "Original offer")

    def test_booking_reprices_and_rejects_client_quote_fields(self) -> None:
        self.client.post(QUOTE, self.data)
        services.update_rates(
            actor=self.admin, vehicle_uuid=self.vehicle.uuid, data={"monthly_rate": "40000"}
        )
        self.assertEqual(self.create().total_amount, Decimal("54000.00"))
        for field in (
            "total_amount",
            "daily_rate_snapshot",
            "discount",
            "currency",
            "rental_days",
            "status",
            "pricing_breakdown",
        ):
            with self.assertRaises(ValidationError):
                self.create(**{field: "0"})

    def test_customer_cannot_create_for_someone_else_and_staff_can_create_for_customer(
        self,
    ) -> None:
        other = User.objects.create_user(email="another@example.com", password=PASSWORD)
        with self.assertRaises(PermissionDenied):
            self.create(customer=str(other.uuid))
        staff = User.objects.create_user(
            email="staff@example.com", password=PASSWORD, role=Role.STAFF
        )
        booking = create_booking(actor=staff, data={**self.data, "customer": str(other.uuid)})
        self.assertEqual(booking.customer, other)
        self.assertEqual(booking.created_by, staff)
        with self.assertRaises(ValidationError):
            create_booking(actor=staff, data=self.data)

    def test_inactive_users_hidden_vehicles_and_past_dates_are_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self.create(pickup_date=(timezone.localdate() - timedelta(days=1)).isoformat())
        Vehicle.objects.filter(pk=self.vehicle.pk).update(status="MAINTENANCE")
        with self.assertRaises(NotFound):
            self.create()
        User.objects.filter(pk=self.customer.pk).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            self.create()
        self.assertFalse(Booking.objects.exists())

    def test_booking_snapshot_and_audit_commit_together(self) -> None:
        with (
            patch(
                "bookings.services.record_booking_event",
                side_effect=RuntimeError("audit unavailable"),
            ),
            self.assertRaises(RuntimeError),
        ):
            self.create()
        self.assertFalse(Booking.objects.exists())

    def test_postgresql_enforces_balanced_nonnegative_snapshot(self) -> None:
        booking = self.create()
        for values in (
            {"total_amount": "-1"},
            {"discount": "60000"},
            {"total_amount": "1"},
            {"return_date": booking.pickup_date},
            {"daily_rate_snapshot": "-1"},
        ):
            with self.assertRaises(IntegrityError), transaction.atomic():
                Booking.objects.filter(pk=booking.pk).update(**values)
        with self.assertRaises(IntegrityError), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "UPDATE bookings_booking SET subtotal = 'NaN' WHERE id = %s", [booking.pk]
            )


class ConcurrentBookingPricingTests(TransactionTestCase):
    def test_rate_update_waits_until_booking_snapshot_is_committed(self) -> None:
        customer = User.objects.create_user(email="customer@example.com", password=PASSWORD)
        admin = User.objects.create_user(
            email="admin@example.com", password=PASSWORD, role=Role.ADMIN
        )
        vehicle = Vehicle.objects.create(**vehicle_data())
        pickup = timezone.localdate() + timedelta(days=1)
        locked, release, updating = Event(), Event(), Event()
        original = services.price_vehicle

        def paused_price(**kwargs):
            locked.set()
            if not release.wait(timeout=10):
                raise RuntimeError("Timed out waiting to finish snapshot")
            return original(**kwargs)

        def create():
            close_old_connections()
            try:
                return create_booking(
                    actor=customer,
                    data={
                        "vehicle": str(vehicle.uuid),
                        "pickup_date": pickup.isoformat(),
                        "return_date": (pickup + timedelta(days=1)).isoformat(),
                    },
                ).uuid
            finally:
                close_old_connections()

        def update():
            close_old_connections()
            try:
                updating.set()
                services.update_rates(
                    actor=admin, vehicle_uuid=vehicle.uuid, data={"daily_rate": "9000"}
                )
            finally:
                close_old_connections()

        with (
            patch("bookings.services.pricing.price_vehicle", side_effect=paused_price),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            booking_future = executor.submit(create)
            try:
                self.assertTrue(locked.wait(timeout=10))
                update_future = executor.submit(update)
                self.assertTrue(updating.wait(timeout=10))
                with self.assertRaises(TimeoutError):
                    update_future.result(timeout=0.1)
            finally:
                release.set()
            booking_uuid = booking_future.result(timeout=10)
            update_future.result(timeout=10)
        vehicle.refresh_from_db()
        booking = Booking.objects.get(uuid=booking_uuid)
        self.assertEqual(vehicle.daily_rate, Decimal("9000.00"))
        self.assertEqual(booking.total_amount, Decimal("2000.00"))
        self.assertEqual(booking.daily_rate_snapshot, Decimal("2000.00"))
