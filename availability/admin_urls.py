from django.urls import path

from availability.views import (
    AdminAvailabilityCheckView,
    AdminBlockDetailView,
    AdminBlockListView,
)

urlpatterns = [
    path("check/", AdminAvailabilityCheckView.as_view(), name="admin-availability-check"),
    path("blocks/", AdminBlockListView.as_view(), name="admin-availability-block-list"),
    path(
        "blocks/<uuid:uuid>/",
        AdminBlockDetailView.as_view(),
        name="admin-availability-block-detail",
    ),
]
