from datetime import date, timedelta

from django.utils import timezone
from rest_framework.request import Request
from rest_framework.response import Response

from accounts.admin_views import AdministrativeView
from accounts.policy import Permission
from reports import selectors
from reports.serializers import DateRangeQuerySerializer

# A report with no explicit range must still be a bounded query, never an implicit
# all-time scan; 30 days is a sensible default operational window.
DEFAULT_RANGE_DAYS = 30


class ReportView(AdministrativeView):
    """Every report is read-only and scoped to REPORTS_VIEW - the same permission for
    all seven, since they're all facets of one operational-reporting capability."""

    required_permissions = {"GET": Permission.REPORTS_VIEW, "OPTIONS": Permission.REPORTS_VIEW}

    def date_range(self, request: Request) -> tuple[date, date]:
        filters = DateRangeQuerySerializer(data=request.query_params)
        filters.is_valid(raise_exception=True)
        end_date = filters.validated_data.get("end_date", timezone.localdate())
        start_date = filters.validated_data.get(
            "start_date", end_date - timedelta(days=DEFAULT_RANGE_DAYS)
        )
        return start_date, end_date


class BookingsReportView(ReportView):
    def get(self, request: Request) -> Response:
        start_date, end_date = self.date_range(request)
        return Response(selectors.bookings_report(start_date=start_date, end_date=end_date))


class RevenueReportView(ReportView):
    def get(self, request: Request) -> Response:
        start_date, end_date = self.date_range(request)
        return Response(selectors.revenue_report(start_date=start_date, end_date=end_date))


class VehicleUtilizationReportView(ReportView):
    def get(self, request: Request) -> Response:
        start_date, end_date = self.date_range(request)
        return Response(
            selectors.vehicle_utilization_report(start_date=start_date, end_date=end_date)
        )


class VehicleAvailabilityReportView(ReportView):
    def get(self, request: Request) -> Response:
        return Response(selectors.vehicle_availability_report())


class MaintenanceReportView(ReportView):
    def get(self, request: Request) -> Response:
        start_date, end_date = self.date_range(request)
        return Response(selectors.maintenance_report(start_date=start_date, end_date=end_date))


class CustomersReportView(ReportView):
    def get(self, request: Request) -> Response:
        start_date, end_date = self.date_range(request)
        return Response(selectors.customers_report(start_date=start_date, end_date=end_date))


class CancellationsReportView(ReportView):
    def get(self, request: Request) -> Response:
        start_date, end_date = self.date_range(request)
        return Response(selectors.cancellations_report(start_date=start_date, end_date=end_date))
