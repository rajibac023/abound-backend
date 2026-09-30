from django.urls import path

from maintenance.views import AdminMaintenanceDetailView, AdminMaintenanceListView

urlpatterns = [
    path("", AdminMaintenanceListView.as_view(), name="admin-maintenance-list"),
    path(
        "<uuid:uuid>/", AdminMaintenanceDetailView.as_view(), name="admin-maintenance-detail"
    ),
]
