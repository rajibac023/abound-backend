from datetime import timedelta

from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import User
from accounts.roles import Role
from bookings.services import create_booking, transition_booking
from maintenance.models import MaintenanceRecord
from tests.test_vehicles import PASSWORD, vehicle_data
from vehicles.models import Vehicle

REPORTS = "/api/v1/admin/reports/"


class ReportAccessTests(APITestCase):
    def setUp(self) -> None:
        self.staff = User.objects.create_user(
            email="staff@example.com", password=PASSWORD, role=Role.STAFF
        )
        self.admin = User.objects.create_user(
            email="admin@example.com", password=PASSWORD, role=Role.ADMIN
        )

    def test_staff_cannot_view_reports(self) -> None:
        self.client.force_authenticate(self.staff)
        for path in ("bookings/", "revenue/", "vehicle-availability/"):
            response = self.client.get(REPORTS + path)
            self.assertEqual(response.status_code, 403, path)

    def test_admin_can_view_reports(self) -> None:
        self.client.force_authenticate(self.admin)
        response = self.client.get(REPORTS + "bookings/")
        self.assertEqual(response.status_code, 200, response.content)

    def test_anonymous_is_rejected(self) -> None:
        response = self.client.get(REPORTS + "bookings/")
        self.assertEqual(response.status_code, 401)

    def test_invalid_range_is_rejected(self) -> None:
        self.client.force_authenticate(self.admin)
        response = self.client.get(
            REPORTS + "bookings/", {"start_date": "2026-06-10", "end_date": "2026-06-01"}
        )
        self.assertEqual(response.status_code, 400)

    def test_missing_range_defaults_to_the_last_30_days(self) -> None:
        self.client.force_authenticate(self.admin)
        response = self.client.get(REPORTS + "bookings/")
        data = response.json()["data"]
        today = timezone.localdate()
        self.assertEqual(data["end_date"], today.isoformat())
        self.assertEqual(data["start_date"], (today - timedelta(days=30)).isoformat())


class BookingAndRevenueReportTests(APITestCase):
    def setUp(self) -> None:
        self.admin = User.objects.create_user(
            email="admin@example.com", password=PASSWORD, role=Role.ADMIN
        )
        self.staff = User.objects.create_user(
            email="staff@example.com", password=PASSWORD, role=Role.STAFF
        )
        self.customer = User.objects.create_user(email="customer@example.com", password=PASSWORD)
        self.vehicle = Vehicle.objects.create(**vehicle_data())
        self.client.force_authenticate(self.admin)
        self.pickup = timezone.localdate() + timedelta(days=5)

    def _book(self, pickup, days=4):
        return create_booking(
            actor=self.customer,
            data={
                "vehicle": str(self.vehicle.uuid),
                "pickup_date": pickup.isoformat(),
                "return_date": (pickup + timedelta(days=days)).isoformat(),
            },
        )

    def test_bookings_report_counts_by_status(self) -> None:
        pending = self._book(self.pickup)
        confirmed = self._book(self.pickup + timedelta(days=10))
        transition_booking(actor=self.staff, booking_uuid=confirmed.uuid, target_status="CONFIRMED")
        response = self.client.get(
            REPORTS + "bookings/",
            {
                "start_date": self.pickup.isoformat(),
                "end_date": (self.pickup + timedelta(days=20)).isoformat(),
            },
        )
        data = response.json()["data"]
        self.assertEqual(data["total_bookings"], 2)
        self.assertEqual(data["by_status"]["PENDING"], 1)
        self.assertEqual(data["by_status"]["CONFIRMED"], 1)
        self.assertIsNotNone(pending)

    def test_revenue_report_only_counts_confirmed_onward_bookings(self) -> None:
        pending = self._book(self.pickup)
        confirmed = self._book(self.pickup + timedelta(days=10))
        transition_booking(actor=self.staff, booking_uuid=confirmed.uuid, target_status="CONFIRMED")
        response = self.client.get(
            REPORTS + "revenue/",
            {
                "start_date": self.pickup.isoformat(),
                "end_date": (self.pickup + timedelta(days=20)).isoformat(),
            },
        )
        data = response.json()["data"]
        self.assertEqual(len(data["by_currency"]), 1)
        row = data["by_currency"][0]
        self.assertEqual(row["currency"], "NPR")
        self.assertEqual(row["booking_count"], 1)
        self.assertEqual(row["total"], "8000.00")
        self.assertIsNotNone(pending)

    def test_cancellations_report_computes_rate(self) -> None:
        confirmed = self._book(self.pickup)
        cancelled = self._book(self.pickup + timedelta(days=10))
        transition_booking(actor=self.staff, booking_uuid=cancelled.uuid, target_status="CANCELLED")
        response = self.client.get(
            REPORTS + "cancellations/",
            {
                "start_date": self.pickup.isoformat(),
                "end_date": (self.pickup + timedelta(days=20)).isoformat(),
            },
        )
        data = response.json()["data"]
        self.assertEqual(data["total_bookings"], 2)
        self.assertEqual(data["cancelled_or_rejected"], 1)
        self.assertEqual(data["cancellation_rate_pct"], 50.0)
        self.assertIsNotNone(confirmed)


class VehicleReportTests(APITestCase):
    def setUp(self) -> None:
        self.admin = User.objects.create_user(
            email="admin@example.com", password=PASSWORD, role=Role.ADMIN
        )
        self.customer = User.objects.create_user(email="customer@example.com", password=PASSWORD)
        self.booked_vehicle = Vehicle.objects.create(**vehicle_data())
        self.idle_vehicle = Vehicle.objects.create(
            **vehicle_data(registration_number="BA-01-IDLE", vin="TESTCHASSIS0002")
        )
        self.client.force_authenticate(self.admin)

    def test_vehicle_utilization_clips_bookings_to_the_range_and_includes_idle_vehicles(
        self,
    ) -> None:
        start = timezone.localdate() + timedelta(days=10)
        end = start + timedelta(days=9)  # a 10-day window
        # Booking spans days 5..15 of a range that runs 10..19: only 5 of its 10
        # nights (days 10..14, since return is exclusive) fall inside the window.
        create_booking(
            actor=self.customer,
            data={
                "vehicle": str(self.booked_vehicle.uuid),
                "pickup_date": (start - timedelta(days=5)).isoformat(),
                "return_date": (start + timedelta(days=5)).isoformat(),
            },
        )
        response = self.client.get(
            REPORTS + "vehicle-utilization/",
            {"start_date": start.isoformat(), "end_date": end.isoformat()},
        )
        rows = {row["vehicle_uuid"]: row for row in response.json()["data"]["vehicles"]}
        booked = rows[str(self.booked_vehicle.uuid)]
        idle = rows[str(self.idle_vehicle.uuid)]
        self.assertEqual(booked["booked_days"], 5)
        self.assertEqual(booked["utilization_pct"], 50.0)
        self.assertEqual(idle["booked_days"], 0)
        self.assertEqual(idle["utilization_pct"], 0.0)

    def test_vehicle_availability_report_counts_by_status(self) -> None:
        response = self.client.get(REPORTS + "vehicle-availability/")
        data = response.json()["data"]
        self.assertEqual(data["total_vehicles"], 2)
        self.assertEqual(data["by_status"]["AVAILABLE"], 2)


class MaintenanceAndCustomerReportTests(APITestCase):
    def setUp(self) -> None:
        self.admin = User.objects.create_user(
            email="admin@example.com", password=PASSWORD, role=Role.ADMIN
        )
        self.vehicle = Vehicle.objects.create(**vehicle_data())
        self.client.force_authenticate(self.admin)

    def test_maintenance_report_sums_cost_by_type(self) -> None:
        start = timezone.localdate() + timedelta(days=3)
        MaintenanceRecord.objects.create(
            vehicle=self.vehicle,
            maintenance_type="ROUTINE_SERVICE",
            start_date=start,
            end_date=start,
            cost="1500.00",
            created_by=self.admin,
        )
        MaintenanceRecord.objects.create(
            vehicle=self.vehicle,
            maintenance_type="ROUTINE_SERVICE",
            start_date=start,
            end_date=start,
            cost="500.00",
            created_by=self.admin,
        )
        response = self.client.get(
            REPORTS + "maintenance/",
            {"start_date": start.isoformat(), "end_date": start.isoformat()},
        )
        data = response.json()["data"]
        row = next(r for r in data["by_type"] if r["maintenance_type"] == "ROUTINE_SERVICE")
        self.assertEqual(row["count"], 2)
        self.assertEqual(row["total_cost"], "2000.00")
        self.assertEqual(data["by_status"]["SCHEDULED"], 2)

    def test_customers_report_counts_new_signups_and_ranks_top_bookers(self) -> None:
        start = timezone.localdate()
        customer = User.objects.create_user(email="new@example.com", password=PASSWORD)
        create_booking(
            actor=customer,
            data={
                "vehicle": str(self.vehicle.uuid),
                "pickup_date": (start + timedelta(days=2)).isoformat(),
                "return_date": (start + timedelta(days=5)).isoformat(),
            },
        )
        response = self.client.get(
            REPORTS + "customers/",
            {
                "start_date": start.isoformat(),
                "end_date": (start + timedelta(days=10)).isoformat(),
            },
        )
        data = response.json()["data"]
        self.assertEqual(data["new_customers"], 1)
        top = data["top_customers"][0]
        self.assertEqual(top["customer__email"], "new@example.com")
        self.assertEqual(top["booking_count"], 1)
