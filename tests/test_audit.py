from rest_framework.test import APITestCase

from accounts.models import User
from accounts.roles import Role
from audit.models import AuditEvent
from pricing.services import update_rates
from tests.test_vehicles import PASSWORD, vehicle_data
from vehicles.models import Vehicle
from vehicles.services import create_vehicle

ADMIN = "/api/v1/admin/"


class AuditEntityTypeTests(APITestCase):
    """entity_type is set automatically by each record_*_event function - no call site
    anywhere in vehicles/bookings/accounts/etc needs to know about it."""

    def setUp(self) -> None:
        self.admin = User.objects.create_user(
            email="admin@example.com", password=PASSWORD, role=Role.ADMIN
        )
        self.vehicle = create_vehicle(actor=self.admin, data=vehicle_data())

    def test_vehicle_and_pricing_events_carry_the_right_entity_type(self) -> None:
        update_rates(
            actor=self.admin, vehicle_uuid=self.vehicle.uuid, data={"daily_rate": "2500.00"}
        )
        vehicle_event = AuditEvent.objects.filter(action="vehicles.created").get()
        pricing_event = AuditEvent.objects.filter(action="pricing.rates_updated").get()
        self.assertEqual(vehicle_event.entity_type, "vehicle")
        self.assertEqual(pricing_event.entity_type, "pricing")


class AuditPreviousNewValueTests(APITestCase):
    def setUp(self) -> None:
        self.admin = User.objects.create_user(
            email="admin@example.com", password=PASSWORD, role=Role.ADMIN
        )
        self.vehicle = Vehicle.objects.create(**vehicle_data())

    def test_price_change_records_the_actual_before_and_after_rate(self) -> None:
        update_rates(
            actor=self.admin, vehicle_uuid=self.vehicle.uuid, data={"daily_rate": "2750.50"}
        )
        event = AuditEvent.objects.get(action="pricing.rates_updated")
        self.assertEqual(event.previous_value, {"daily_rate": "2000.00"})
        self.assertEqual(event.new_value, {"daily_rate": "2750.50"})

    def test_an_unchanged_rate_write_records_no_event_or_value(self) -> None:
        update_rates(
            actor=self.admin, vehicle_uuid=self.vehicle.uuid, data={"daily_rate": "2000.00"}
        )
        self.assertFalse(AuditEvent.objects.filter(action="pricing.rates_updated").exists())

    def test_vehicle_status_change_surfaces_status_into_previous_and_new_value(self) -> None:
        from vehicles.services import deactivate_vehicle

        deactivate_vehicle(actor=self.admin, vehicle_uuid=self.vehicle.uuid)
        event = AuditEvent.objects.get(action="vehicles.deactivated")
        self.assertEqual(event.previous_value, {"status": "AVAILABLE"})
        self.assertEqual(event.new_value, {"status": "INACTIVE"})


class AuditRequestMetadataTests(APITestCase):
    def setUp(self) -> None:
        self.admin = User.objects.create_user(
            email="admin@example.com", password=PASSWORD, role=Role.ADMIN
        )
        self.client.force_authenticate(self.admin)

    def test_a_request_driven_event_captures_ip_and_user_agent(self) -> None:
        self.client.post(
            ADMIN + "vehicles/",
            vehicle_data(),
            format="json",
            REMOTE_ADDR="203.0.113.7",
            HTTP_USER_AGENT="AboundAdminWeb/1.0",
        )
        event = AuditEvent.objects.get(action="vehicles.created")
        self.assertEqual(event.ip_address, "203.0.113.7")
        self.assertEqual(event.user_agent, "AboundAdminWeb/1.0")


class AuditFilteringTests(APITestCase):
    def setUp(self) -> None:
        self.super_admin = User.objects.create_user(
            email="super@example.com", password=PASSWORD, role=Role.SUPER_ADMIN
        )
        self.other_vehicle = create_vehicle(
            actor=self.super_admin,
            data=vehicle_data(registration_number="BA-01-OTHER", vin="TESTCHASSIS0099"),
        )
        self.vehicle = create_vehicle(actor=self.super_admin, data=vehicle_data())
        self.client.force_authenticate(self.super_admin)

    def test_filtering_by_entity_type_excludes_other_entities(self) -> None:
        response = self.client.get(ADMIN + "audit/", {"entity_type": "vehicle"})
        self.assertEqual(response.status_code, 200, response.content)
        rows = response.json()["data"]["results"]
        self.assertTrue(rows)
        self.assertTrue(all(row["entity_type"] == "vehicle" for row in rows))

    def test_filtering_by_subject_uuid_scopes_to_one_entity(self) -> None:
        response = self.client.get(ADMIN + "audit/", {"subject_uuid": str(self.vehicle.uuid)})
        rows = response.json()["data"]["results"]
        self.assertTrue(rows)
        self.assertTrue(all(row["subject_uuid"] == str(self.vehicle.uuid) for row in rows))

    def test_invalid_date_range_is_rejected(self) -> None:
        response = self.client.get(
            ADMIN + "audit/", {"start_date": "2026-06-10", "end_date": "2026-06-01"}
        )
        self.assertEqual(response.status_code, 400)
