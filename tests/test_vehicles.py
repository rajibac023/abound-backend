from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch
from uuid import UUID, uuid4

from django.db import IntegrityError, close_old_connections, connection, transaction
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIClient, APITestCase

from accounts.models import User
from accounts.roles import Role
from accounts.services import login
from audit.models import AuditEvent
from vehicles.choices import VehicleStatus
from vehicles.models import Vehicle
from vehicles.services import create_vehicle, update_vehicle

PASSWORD = "Mountain!River-Compass-827"
ADMIN = "/api/v1/admin/vehicles/"
PUBLIC = "/api/v1/vehicles/"


def vehicle_data(**overrides):
    return {
        "vehicle_name": "Test SUV",
        "brand": "Example Motors",
        "model": "Trail",
        "variant": "Premium",
        "vehicle_type": "SUV",
        "manufacturing_year": 2024,
        "registration_number": "BA-01-TEST",
        "vin": "TESTCHASSIS0001",
        "color": "White",
        "short_description": "A comfortable SUV.",
        "description": "Customer-safe vehicle description.",
        "internal_notes": "PRIVATE-OPERATIONS-SECRET",
        "featured": True,
        "status": "AVAILABLE",
        "engine_type": "Four-cylinder",
        "engine_capacity_cc": 1500,
        "engine_power": "88.50",
        "torque": "250.00",
        "fuel_type": "DIESEL",
        "transmission": "MANUAL",
        "drive_type": "FWD",
        "number_of_gears": 6,
        "mileage": "16.50",
        "mileage_unit": "KM_PER_LITRE",
        "seat_count": 5,
        "passenger_capacity": 5,
        "door_count": 5,
        "luggage_capacity": 3,
        "boot_capacity": 450,
        "currency": "NPR",
        "daily_rate": "2000.00",
        "weekly_rate": "12000.00",
        "monthly_rate": "45000.00",
        "security_deposit": "10000.00",
        "included_kilometers": 200,
        "extra_kilometer_rate": "25.00",
        "driver_available": True,
        "driver_daily_rate": "1000.00",
        "fuel_included": False,
        "insurance_included": True,
        **overrides,
    }


class VehicleTests(APITestCase):
    def setUp(self) -> None:
        self.users = {
            role: User.objects.create_user(
                email=f"{role.lower()}@example.com", password=PASSWORD, role=role
            )
            for role in Role.values
        }
        self.tokens = {
            role: login(email=user.email, password=PASSWORD, client="mobile")[1]
            for role, user in self.users.items()
        }
        self.vehicle = Vehicle.objects.create(**vehicle_data())

    def authorize(self, role: str | None = Role.ADMIN) -> None:
        if role is None:
            self.client.credentials()
        else:
            self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.tokens[role]['access']}")

    def detail(self, vehicle=None, *, public=False) -> str:
        return (PUBLIC if public else ADMIN) + str((vehicle or self.vehicle).uuid) + "/"

    def new_data(self, **overrides):
        return vehicle_data(registration_number="BA-NEW-2", vin="CHASSISNEW2", **overrides)

    def test_create_persists_all_vehicle_fields_with_decimal_rates(self) -> None:
        self.authorize()
        payload = self.new_data()
        response = self.client.post(ADMIN, payload)
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()["data"]
        UUID(data["uuid"])
        self.assertEqual(data["registration_number"], "BANEW2")
        for field, value in payload.items():
            if field != "registration_number":
                self.assertEqual(data[field], value, field)
        saved = Vehicle.objects.get(uuid=data["uuid"])
        self.assertEqual(saved.daily_rate, Decimal("2000.00"))
        self.assertIsNotNone(saved.created_at.tzinfo)
        event = AuditEvent.objects.get(action="vehicles.created")
        self.assertEqual(event.actor, self.users[Role.ADMIN])
        self.assertEqual(str(event.subject_uuid), data["uuid"])
        self.assertEqual(str(event.request_id), response["X-Request-ID"])
        self.assertNotIn(payload["internal_notes"], str(event.changes))
        self.assertNotIn(payload["vin"], str(event.changes))

    def test_minimal_create_defaults_to_inactive_without_inventing_optional_rates(self) -> None:
        self.authorize()
        response = self.client.post(
            ADMIN,
            {
                key: value
                for key, value in self.new_data().items()
                if key
                in {
                    "vehicle_name",
                    "brand",
                    "model",
                    "vehicle_type",
                    "manufacturing_year",
                    "registration_number",
                    "seat_count",
                    "passenger_capacity",
                    "daily_rate",
                }
            },
        )
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()["data"]
        self.assertEqual(data["status"], "INACTIVE")
        self.assertEqual(data["currency"], "NPR")
        self.assertIsNone(data["weekly_rate"])
        self.assertIsNone(data["monthly_rate"])
        self.assertIsNone(data["security_deposit"])
        self.assertEqual(self.client.get(PUBLIC + data["uuid"] + "/").status_code, 404)

    def test_required_fields_and_invalid_values_return_400_without_creating(self) -> None:
        self.authorize()
        self.assertEqual(self.client.post(ADMIN, {}).status_code, 400)
        invalid = (
            {"vehicle_name": " "},
            {"brand": ""},
            {"model": ""},
            {"vehicle_type": "unknown"},
            {"status": "ACTIVE"},
            {"manufacturing_year": 1800},
            {"manufacturing_year": timezone.localdate().year + 1},
            {"daily_rate": "-1.00"},
            {"weekly_rate": "NaN"},
            {"monthly_rate": "Infinity"},
            {"daily_rate": "2.001"},
            {"security_deposit": "-1"},
            {"extra_kilometer_rate": "-1"},
            {"driver_daily_rate": "-1"},
            {"included_kilometers": -1},
            {"seat_count": 0},
            {"passenger_capacity": 6},
            {"door_count": 0},
            {"luggage_capacity": -1},
            {"boot_capacity": -1},
            {"number_of_gears": 0},
            {"engine_capacity_cc": -1},
            {"engine_power": "-1"},
            {"torque": "-1"},
            {"mileage": "-1"},
            {"mileage_unit": ""},
            {"transmission": "invalid"},
            {"fuel_type": "invalid"},
            {"drive_type": "invalid"},
            {"currency": "USD"},
            {"registration_number": "---"},
            {"vin": "bad@chassis"},
        )
        for changes in invalid:
            with self.subTest(changes=changes):
                payload = {**self.new_data(), **changes}
                response = self.client.post(ADMIN, payload)
                self.assertEqual(response.status_code, 400, response.content)
                self.assertFalse(response.json()["success"])
        self.assertEqual(Vehicle.objects.count(), 1)

    def test_write_allowlist_rejects_identity_audit_and_document_injection(self) -> None:
        self.authorize()
        for field, value in (
            ("uuid", str(uuid4())),
            ("id", 999),
            ("created_at", "2024-01-01"),
            ("updated_at", "2024-01-01"),
            ("private_documents", []),
            ("audit", {}),
            ("owner_id", 1),
        ):
            response = self.client.post(ADMIN, {**self.new_data(), field: value})
            self.assertEqual(response.status_code, 400)
        self.assertEqual(Vehicle.objects.count(), 1)

    def test_registration_uniqueness_ignores_case_spaces_and_hyphens(self) -> None:
        self.authorize()
        for registration in ("BA01TEST", " ba-01-test ", "BA 01 TEST"):
            response = self.client.post(
                ADMIN, {**self.new_data(), "registration_number": registration}
            )
            self.assertEqual(response.status_code, 400)
            self.assertIn("registration_number", response.json()["errors"])

    def test_vin_is_unique_but_multiple_missing_vins_are_supported(self) -> None:
        self.authorize()
        duplicate = self.client.post(ADMIN, {**self.new_data(), "vin": " test-chassis-0001 "})
        self.assertEqual(duplicate.status_code, 400)
        self.assertIn("vin", duplicate.json()["errors"])
        for index, vin in enumerate((None, "", "   ")):
            response = self.client.post(
                ADMIN, {**self.new_data(), "registration_number": f"MISSING{index}", "vin": vin}
            )
            self.assertEqual(response.status_code, 201, response.content)
            self.assertIsNone(response.json()["data"]["vin"])

    def test_electric_technical_information_does_not_require_combustion_fields(self) -> None:
        self.authorize()
        response = self.client.post(
            ADMIN,
            self.new_data(
                engine_type="Electric motor",
                engine_capacity_cc=None,
                fuel_type="ELECTRIC",
                transmission="SINGLE_SPEED",
                number_of_gears=1,
                mileage="6.50",
                mileage_unit="KM_PER_KWH",
            ),
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertIsNone(response.json()["data"]["engine_capacity_cc"])

    def test_patch_validates_against_existing_capacity_and_supports_atomic_changes(self) -> None:
        self.authorize()
        denied = self.client.patch(self.detail(), {"seat_count": 4})
        self.assertEqual(denied.status_code, 400)
        self.vehicle.refresh_from_db()
        self.assertEqual(self.vehicle.seat_count, 5)
        response = self.client.patch(
            self.detail(), {"seat_count": 4, "passenger_capacity": 4, "daily_rate": "1234.56"}
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["data"]["daily_rate"], "1234.56")
        self.assertEqual(
            response.json()["data"]["registration_number"], self.vehicle.registration_number
        )
        self.assertTrue(
            AuditEvent.objects.filter(
                action="vehicles.updated", subject_uuid=self.vehicle.uuid
            ).exists()
        )

    def test_full_update_requires_required_fields_and_preserves_uuid(self) -> None:
        self.authorize()
        self.assertEqual(
            self.client.put(self.detail(), {"vehicle_name": "Replacement"}).status_code, 400
        )
        response = self.client.put(self.detail(), vehicle_data(vehicle_name="Replacement"))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["data"]["uuid"], str(self.vehicle.uuid))
        self.assertEqual(response.json()["data"]["vehicle_name"], "Replacement")

    def test_update_cannot_reuse_another_identity(self) -> None:
        other = Vehicle.objects.create(**self.new_data())
        self.authorize()
        for field in ("registration_number", "vin"):
            response = self.client.patch(self.detail(), {field: getattr(other, field)})
            self.assertEqual(response.status_code, 400)
            self.assertIn(field, response.json()["errors"])

    def test_delete_deactivates_preserves_identifiers_and_is_idempotent(self) -> None:
        self.authorize()
        for _ in range(2):
            response = self.client.delete(self.detail())
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                response.json()["data"],
                {"uuid": str(self.vehicle.uuid), "status": "INACTIVE", "deactivated": True},
            )
        self.vehicle.refresh_from_db()
        self.assertEqual(self.vehicle.status, VehicleStatus.INACTIVE)
        self.assertEqual(Vehicle.objects.count(), 1)
        self.assertEqual(self.client.get(self.detail()).status_code, 200)
        self.assertEqual(self.client.get(self.detail(public=True)).status_code, 404)
        self.assertEqual(AuditEvent.objects.filter(action="vehicles.deactivated").count(), 1)
        duplicate = self.client.post(
            ADMIN, {**self.new_data(), "registration_number": self.vehicle.registration_number}
        )
        self.assertEqual(duplicate.status_code, 400)
        self.assertEqual(self.client.patch(self.detail(), {"status": "AVAILABLE"}).status_code, 200)
        self.assertEqual(self.client.get(self.detail(public=True)).status_code, 200)

    def test_admin_missing_vehicle_returns_404_for_each_allowed_method(self) -> None:
        self.authorize()
        for method in ("get", "put", "patch", "delete"):
            self.assertEqual(
                getattr(self.client, method)(ADMIN + str(uuid4()) + "/").status_code, 404
            )

    def test_every_role_has_appropriate_admin_crud_access(self) -> None:
        for role in (None, *Role.values):
            self.authorize(role)
            can_read = role in {Role.SUPER_ADMIN, Role.ADMIN, Role.STAFF}
            can_write = role in {Role.SUPER_ADMIN, Role.ADMIN}
            denied = 401 if role is None else 403
            for method in ("get", "head", "options"):
                for url in (ADMIN, self.detail()):
                    self.assertEqual(
                        getattr(self.client, method)(url).status_code, 200 if can_read else denied
                    )
            payload = {**self.new_data(), "registration_number": f"ROLE{role}", "vin": None}
            self.assertEqual(
                self.client.post(ADMIN, payload).status_code, 201 if can_write else denied
            )
            self.assertEqual(
                self.client.patch(self.detail(), {"color": "Blue"}).status_code,
                200 if can_write else denied,
            )
            self.assertEqual(
                self.client.put(self.detail(), vehicle_data()).status_code,
                200 if can_write else denied,
            )
            self.assertEqual(
                self.client.delete(self.detail()).status_code, 200 if can_write else denied
            )

    def test_inactive_administrator_cannot_read_or_mutate_vehicles(self) -> None:
        self.authorize()
        User.objects.filter(pk=self.users[Role.ADMIN].pk).update(is_active=False)
        self.assertEqual(self.client.get(ADMIN).status_code, 401)
        self.assertEqual(self.client.post(ADMIN, self.new_data()).status_code, 401)

    def test_service_enforces_permissions_and_rechecks_demoted_actor(self) -> None:
        with self.assertRaises(PermissionDenied):
            create_vehicle(actor=self.users[Role.STAFF], data=self.new_data())
        actor = self.users[Role.ADMIN]
        User.objects.filter(pk=actor.pk).update(role=Role.STAFF, is_staff=False)
        with self.assertRaises(PermissionDenied):
            update_vehicle(
                actor=actor, vehicle_uuid=self.vehicle.uuid, data={"daily_rate": "0"}, partial=True
            )

    def test_audit_failure_rolls_back_creation_and_update(self) -> None:
        with patch(
            "vehicles.services.record_vehicle_event", side_effect=RuntimeError("audit failed")
        ):
            with self.assertRaises(RuntimeError):
                create_vehicle(actor=self.users[Role.ADMIN], data=self.new_data())
            with self.assertRaises(RuntimeError):
                update_vehicle(
                    actor=self.users[Role.ADMIN],
                    vehicle_uuid=self.vehicle.uuid,
                    data={"color": "Blue"},
                    partial=True,
                )
        self.assertEqual(Vehicle.objects.count(), 1)
        self.vehicle.refresh_from_db()
        self.assertEqual(self.vehicle.color, "White")

    def test_public_list_and_detail_exclude_all_private_fields_even_for_admins(self) -> None:
        for role in (None, Role.CUSTOMER, Role.ADMIN):
            self.authorize(role)
            listing = self.client.get(PUBLIC)
            detail = self.client.get(self.detail(public=True))
            self.assertEqual(listing.status_code, 200)
            self.assertEqual(detail.status_code, 200)
            for data in (listing.json()["data"]["results"][0], detail.json()["data"]):
                self.assertFalse(
                    {
                        "id",
                        "registration_number",
                        "vin",
                        "internal_notes",
                        "private_documents",
                        "audit",
                        "created_at",
                        "updated_at",
                    }
                    & set(data)
                )
                self.assertEqual(data["daily_rate"], "2000.00")
            self.assertNotIn(self.vehicle.internal_notes, detail.content.decode())
            self.assertNotIn(self.vehicle.vin, detail.content.decode())
            self.assertNotIn(self.vehicle.registration_number, detail.content.decode())
            self.assertEqual(detail["Cache-Control"], "no-store")

    def test_customer_only_sees_available_vehicles_and_cannot_change_visibility(self) -> None:
        hidden = []
        for status in (
            VehicleStatus.UNAVAILABLE,
            VehicleStatus.MAINTENANCE,
            VehicleStatus.INACTIVE,
        ):
            hidden.append(
                Vehicle.objects.create(
                    **vehicle_data(registration_number=str(status), vin=None, status=status)
                )
            )
        response = self.client.get(PUBLIC)
        self.assertEqual(response.json()["data"]["count"], 1)
        for vehicle in hidden:
            response = self.client.get(self.detail(vehicle, public=True))
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.json(), self.client.get(PUBLIC + str(uuid4()) + "/").json())
        self.assertEqual(self.client.get(PUBLIC, {"status": "INACTIVE"}).status_code, 400)

    def test_customer_cannot_search_filter_or_sort_by_private_information(self) -> None:
        for search in (
            self.vehicle.vin,
            self.vehicle.registration_number,
            self.vehicle.internal_notes,
        ):
            self.assertEqual(self.client.get(PUBLIC, {"search": search}).json()["data"]["count"], 0)
        for params in (
            {"vin": self.vehicle.vin},
            {"registration_number": self.vehicle.registration_number},
            {"ordering": "vin"},
            {"ordering": "registration_number"},
            {"ordering": "internal_notes"},
        ):
            self.assertEqual(self.client.get(PUBLIC, params).status_code, 400)

    def test_public_routes_are_read_only(self) -> None:
        for method in ("post", "put", "patch", "delete"):
            for url in (PUBLIC, self.detail(public=True)):
                self.assertEqual(
                    getattr(self.client, method)(url, self.new_data()).status_code, 405
                )
        self.assertEqual(Vehicle.objects.count(), 1)

    def test_admin_filtering_search_and_sorting(self) -> None:
        other = Vehicle.objects.create(
            **vehicle_data(
                registration_number="SECOND",
                vin="SECONDCHASSIS",
                vehicle_name="Small Car",
                model="City",
                brand="Other",
                vehicle_type="SEDAN",
                featured=False,
                driver_available=False,
                status="MAINTENANCE",
                fuel_type="PETROL",
                transmission="AUTOMATIC",
                daily_rate="1000.00",
            )
        )
        self.authorize(Role.STAFF)
        cases = [
            ({"status": "MAINTENANCE"}, other.uuid),
            ({"featured": "false"}, other.uuid),
            ({"vehicle_type": "SEDAN"}, other.uuid),
            ({"brand": "other"}, other.uuid),
            ({"fuel_type": "PETROL"}, other.uuid),
            ({"transmission": "AUTOMATIC"}, other.uuid),
            ({"driver_available": "false"}, other.uuid),
            ({"search": "Trail"}, self.vehicle.uuid),
            ({"search": "second-chassis"}, other.uuid),
            ({"registration_number": "ba-01-test"}, self.vehicle.uuid),
            ({"vin": "second-chassis"}, other.uuid),
            ({"min_daily_rate": "1500", "max_daily_rate": "2200"}, self.vehicle.uuid),
        ]
        self.assertEqual(self.client.get(ADMIN).json()["data"]["count"], 2)
        for params, expected in cases:
            with self.subTest(params=params):
                response = self.client.get(ADMIN, params)
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(
                    [row["uuid"] for row in response.json()["data"]["results"]], [str(expected)]
                )
        self.assertEqual(self.client.get(ADMIN, {"min_seats": 6}).json()["data"]["count"], 0)
        for ordering, first in (("daily_rate", other.uuid), ("-daily_rate", self.vehicle.uuid)):
            self.assertEqual(
                self.client.get(ADMIN, {"ordering": ordering}).json()["data"]["results"][0]["uuid"],
                str(first),
            )

    def test_public_filtering_is_identical_for_allowed_fields(self) -> None:
        other = Vehicle.objects.create(
            **vehicle_data(
                registration_number="PUBLICOTHER",
                vin=None,
                vehicle_type="SEDAN",
                featured=False,
                driver_available=False,
                daily_rate="500.00",
            )
        )
        self.assertEqual(self.client.get(PUBLIC).json()["data"]["count"], 2)
        for params in (
            {"vehicle_type": "SEDAN"},
            {"featured": "false"},
            {"driver_available": "false"},
            {"max_daily_rate": "1000"},
        ):
            response = self.client.get(PUBLIC, params)
            self.assertEqual(
                [row["uuid"] for row in response.json()["data"]["results"]], [str(other.uuid)]
            )

    def test_query_validation_rejects_unsafe_or_ambiguous_queries(self) -> None:
        self.authorize()
        for params in (
            {"featured": "maybe"},
            {"min_daily_rate": "-1"},
            {"min_daily_rate": "500", "max_daily_rate": "100"},
            {"min_seats": 0},
            {"page": 0},
            {"page_size": 0},
            {"ordering": "password"},
            {"ordering": "daily_rate;DROP TABLE vehicles_vehicle"},
            {"ordering": ""},
            {"unexpected": "true"},
            {"search": "x" * 101},
        ):
            for url in (PUBLIC, ADMIN):
                self.assertEqual(self.client.get(url, params).status_code, 400)
        self.assertEqual(self.client.get(PUBLIC + "?featured=true&featured=false").status_code, 400)

    def test_fleet_growth_and_stable_bounded_pagination_over_one_thousand_vehicles(self) -> None:
        Vehicle.objects.bulk_create(
            [
                Vehicle(**vehicle_data(registration_number=f"FLEET{index}", vin=None))
                for index in range(1004)
            ]
        )
        with CaptureQueriesContext(connection) as queries:
            first = self.client.get(PUBLIC, {"page_size": 10000, "ordering": "daily_rate"})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["data"]["count"], 1005)
        self.assertEqual(len(first.json()["data"]["results"]), 100)
        self.assertLessEqual(
            len(queries), 4
        )  # No per-vehicle queries when serializing the catalog.
        second = self.client.get(PUBLIC, {"page_size": 100, "page": 2, "ordering": "daily_rate"})
        self.assertFalse(
            {row["uuid"] for row in first.json()["data"]["results"]}
            & {row["uuid"] for row in second.json()["data"]["results"]}
        )
        last = self.client.get(PUBLIC, {"page_size": 100, "page": 11, "ordering": "daily_rate"})
        self.assertEqual(len(last.json()["data"]["results"]), 5)
        self.assertIsNone(last.json()["data"]["next"])
        self.assertEqual(self.client.get(PUBLIC, {"page_size": 100, "page": 12}).status_code, 404)
        self.authorize()
        self.assertEqual(self.client.get(ADMIN).json()["data"]["count"], 1005)
        self.assertEqual(len(self.client.get(ADMIN).json()["data"]["results"]), 25)

    def test_database_constraints_protect_non_api_writes(self) -> None:
        invalid = (
            {"registration_number": "lowercase"},
            {"vin": "lowercase"},
            {"status": "UNKNOWN"},
            {"vehicle_type": "UNKNOWN"},
            {"currency": "USD"},
            {"daily_rate": Decimal("-1")},
            {"seat_count": 0},
            {"passenger_capacity": 6},
            {"mileage_unit": ""},
            {"fuel_type": "UNKNOWN"},
        )
        for changes in invalid:
            with (
                self.subTest(changes=changes),
                self.assertRaises(IntegrityError),
                transaction.atomic(),
            ):
                Vehicle.objects.filter(pk=self.vehicle.pk).update(**changes)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Vehicle.objects.create(**vehicle_data(vin=None))
        # The ORM already rejects NaN; exercise the PostgreSQL constraint directly too.
        with self.assertRaises(IntegrityError), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "UPDATE vehicles_vehicle SET daily_rate = %s WHERE id = %s",
                [Decimal("NaN"), self.vehicle.pk],
            )

    def test_required_identifier_status_type_and_feature_indexes_exist(self) -> None:
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(cursor, Vehicle._meta.db_table)
        for field in ("registration_number", "vin"):
            self.assertTrue(
                any(item["unique"] and item["columns"] == [field] for item in constraints.values())
            )
        for field in ("status", "vehicle_type", "featured"):
            self.assertTrue(
                any(item["index"] and item["columns"][0] == field for item in constraints.values())
            )


class ConcurrentVehicleIdentityTests(TransactionTestCase):
    def test_simultaneous_duplicate_registration_creates_one_vehicle_and_one_audit_event(
        self,
    ) -> None:
        from vehicles.services import persist_vehicle

        actors = [
            User.objects.create_user(
                email=f"admin-{index}@example.com", password=PASSWORD, role=Role.ADMIN
            )
            for index in range(2)
        ]
        tokens = [
            login(email=actor.email, password=PASSWORD, client="mobile")[1]["access"]
            for actor in actors
        ]
        barrier = Barrier(2)

        def synchronized_save(vehicle):
            barrier.wait(timeout=10)
            persist_vehicle(vehicle)

        def create(access):
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET statement_timeout = '10s'")
                client = APIClient()
                client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
                return client.post(ADMIN, vehicle_data(vin=None)).status_code
            finally:
                close_old_connections()

        with (
            patch("vehicles.services.persist_vehicle", side_effect=synchronized_save),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            tasks = [executor.submit(create, token) for token in tokens]
            results = sorted(task.result(timeout=15) for task in tasks)
        self.assertEqual(results, [201, 400])
        self.assertEqual(Vehicle.objects.count(), 1)
        self.assertEqual(AuditEvent.objects.filter(action="vehicles.created").count(), 1)
