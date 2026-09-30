from django.urls import path

from reports import views

urlpatterns = [
    path("bookings/", views.BookingsReportView.as_view(), name="report-bookings"),
    path("revenue/", views.RevenueReportView.as_view(), name="report-revenue"),
    path(
        "vehicle-utilization/",
        views.VehicleUtilizationReportView.as_view(),
        name="report-vehicle-utilization",
    ),
    path(
        "vehicle-availability/",
        views.VehicleAvailabilityReportView.as_view(),
        name="report-vehicle-availability",
    ),
    path("maintenance/", views.MaintenanceReportView.as_view(), name="report-maintenance"),
    path("customers/", views.CustomersReportView.as_view(), name="report-customers"),
    path(
        "cancellations/", views.CancellationsReportView.as_view(), name="report-cancellations"
    ),
]
