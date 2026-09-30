from django.urls import include, path

from audit.views import AuditEventListView
from common.urls import urlpatterns as health_patterns
from vehicles.media_views import FeatureDetailView, FeatureListView

app_name = "api"
urlpatterns = [
    *health_patterns,
    path("auth/", include("accounts.urls")),
    path("admin/", include("accounts.admin_urls")),
    path("admin/audit/", AuditEventListView.as_view(), name="admin-audit"),
    path("admin/vehicles/", include("vehicles.admin_urls")),
    path("admin/vehicle-features/", FeatureListView.as_view(), name="admin-feature-list"),
    path(
        "admin/vehicle-features/<uuid:uuid>/",
        FeatureDetailView.as_view(),
        name="admin-feature-detail",
    ),
    path("vehicles/", include("vehicles.urls")),
    path("pricing/", include("pricing.urls")),
    path("admin/pricing/", include("pricing.admin_urls")),
    path("availability/", include("availability.urls")),
    path("admin/availability/", include("availability.admin_urls")),
    path("admin/maintenance/", include("maintenance.admin_urls")),
    path("bookings/", include("bookings.urls")),
    path("admin/bookings/", include("bookings.admin_urls")),
    path("admin/customers/", include("customers.admin_urls")),
    path("notifications/", include("notifications.urls")),
    path("admin/reports/", include("reports.urls")),
]
