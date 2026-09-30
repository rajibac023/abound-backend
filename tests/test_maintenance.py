from datetime import timedelta

from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import User
from accounts.roles import Role
from audit.models import AuditEvent
from maintenance.models import MaintenanceRecord
from tests.test_vehicles import PASSWORD, vehicle_data
from vehicles.models import Vehicle

ADMIN = "/api/v1/admin/maintenance/"


class MaintenanceAPITests(APITestCase):
    def setUp(self) -> None:
        self.users = {
            role: User.objects.create_user(
                email=f"{role.lower()}@example.com", password=PASSWORD, role=role
            )
            for role in Role.values
        }
        self.vehicle = Vehicle.objects.create(**vehicle_data())
        self.today = timezone.localdate()
        self.payload = {
            "vehicle": str(self.vehicle.uuid),
            "maintenance_type": "ROUTINE_SERVICE",
            "description": "Oil change and inspection",
            "start_date": (self.today + timedelta(days=5)).isoformat(),
            "end_date": (self.today + timedelta(days=6)).isoformat(),
            "odometer": 15000,
            "cost": "3500.00",
            "service_provider": "Kathmandu Auto Care",
            "status": "SCHEDULED",
            "notes": "Customer requested an early slot",
        }

    def authorize(self, role: str) -> None:
        self.client.force_authenticate(self.users[role])

    def create(self, **overrides):
        response = self.client.post(ADMIN, {**self.payload, **overrides})
        assert response.status_code == 201, response.content
        return response.json()["data"]

    def test_admin_can_create_and_list_maintenance_records(self) -> None:
        self.authorize(Role.ADMIN)
        response = self.client.post(ADMIN, self.payload)
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()["data"]
        self.assertEqual(data["status"], "SCHEDULED")
        self.assertEqual(data["vehicle"], str(self.vehicle.uuid))
        self.assertEqual(data["created_by"], str(self.users[Role.ADMIN].uuid))
        self.assertTrue(
            AuditEvent.objects.filter(
                action="maintenance.created", subject_uuid=data["uuid"]
            ).exists()
        )
        listing = self.client.get(ADMIN).json()["data"]["results"]
        self.assertEqual(len(listing), 1)

    def test_staff_can_create_and_update_records(self) -> None:
        self.authorize(Role.STAFF)
        created = self.create()
        url = ADMIN + created["uuid"] + "/"
        response = self.client.patch(url, {"status": "IN_PROGRESS"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["data"]["status"], "IN_PROGRESS")

    def test_status_transitions_and_cost_updates(self) -> None:
        self.authorize(Role.ADMIN)
        created = self.create()
        url = ADMIN + created["uuid"] + "/"
        self.assertEqual(self.client.patch(url, {"status": "IN_PROGRESS"}).status_code, 200)
        response = self.client.patch(
            url, {"status": "COMPLETED", "cost": "4200.00", "odometer": 15120}
        )
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        self.assertEqual(data["cost"], "4200.00")
        self.assertEqual(data["odometer"], 15120)
        response = self.client.patch(url, {"status": "CANCELLED"})
        self.assertEqual(response.json()["data"]["status"], "CANCELLED")

    def test_filter_by_vehicle_and_status(self) -> None:
        self.authorize(Role.ADMIN)
        self.create()
        other_vehicle = Vehicle.objects.create(
            **vehicle_data(registration_number="OTHERMTN", vin=None)
        )
        self.create(vehicle=str(other_vehicle.uuid), status="COMPLETED")
        by_vehicle = self.client.get(
            ADMIN, {"vehicle": str(self.vehicle.uuid)}
        ).json()["data"]["results"]
        self.assertEqual(len(by_vehicle), 1)
        by_status = self.client.get(ADMIN, {"status": "COMPLETED"}).json()["data"]["results"]
        self.assertEqual(len(by_status), 1)
        self.assertEqual(by_status[0]["status"], "COMPLETED")

    def test_invalid_dates_and_values_are_rejected(self) -> None:
        self.authorize(Role.ADMIN)
        for changes in (
            {"end_date": (self.today + timedelta(days=4)).isoformat()},  # before start_date
            {"cost": "-1"},
            {"odometer": -1},
            {"maintenance_type": "UNKNOWN"},
            {"status": "UNKNOWN"},
            {"vehicle": "not-a-uuid"},
        ):
            response = self.client.post(ADMIN, {**self.payload, **changes})
            self.assertEqual(response.status_code, 400, (changes, response.content))

    def test_created_by_is_set_from_the_authenticated_actor(self) -> None:
        self.authorize(Role.ADMIN)
        created = self.create()
        record = MaintenanceRecord.objects.get(uuid=created["uuid"])
        self.assertEqual(record.created_by, self.users[Role.ADMIN])

    def test_every_role_enforces_maintenance_grants(self) -> None:
        self.authorize(Role.ADMIN)
        record = self.create()
        url = ADMIN + record["uuid"] + "/"
        for role in (None, *Role.values):
            with self.subTest(role=role):
                self.client.force_authenticate(self.users[role] if role else None)
                read = role in {Role.SUPER_ADMIN, Role.ADMIN, Role.STAFF}
                write = role in {Role.SUPER_ADMIN, Role.ADMIN, Role.STAFF}
                denied = 403 if role else 401
                self.assertEqual(self.client.get(ADMIN).status_code, 200 if read else denied)
                self.assertEqual(self.client.get(url).status_code, 200 if read else denied)
                self.assertEqual(
                    self.client.post(ADMIN, self.payload).status_code, 201 if write else denied
                )
                status = 200 if write else denied
                self.assertEqual(
                    self.client.patch(url, {"notes": "Updated"}).status_code, status
                )

    def test_unauthenticated_and_customer_are_denied(self) -> None:
        for role in (None, Role.CUSTOMER):
            self.client.force_authenticate(self.users[role] if role else None)
            self.assertIn(self.client.get(ADMIN).status_code, (401, 403))
            self.assertIn(self.client.post(ADMIN, self.payload).status_code, (401, 403))
        self.assertFalse(MaintenanceRecord.objects.exists())
