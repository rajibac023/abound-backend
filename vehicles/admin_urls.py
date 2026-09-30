from django.urls import path

from vehicles.document_views import (
    AdminVehicleDocumentDetailView,
    AdminVehicleDocumentFileView,
    AdminVehicleDocumentListView,
)
from vehicles.media_views import (
    AdminVehicleImageFileView,
    VehicleFeaturesView,
    VehicleImageDetailView,
    VehicleImageListView,
)
from vehicles.views import AdminVehicleDetailView, AdminVehicleListView

urlpatterns = [
    path("", AdminVehicleListView.as_view(), name="admin-vehicle-list"),
    path("<uuid:uuid>/", AdminVehicleDetailView.as_view(), name="admin-vehicle-detail"),
    path("<uuid:uuid>/features/", VehicleFeaturesView.as_view(), name="admin-vehicle-features"),
    path("<uuid:uuid>/images/", VehicleImageListView.as_view(), name="admin-vehicle-image-list"),
    path(
        "<uuid:uuid>/images/<uuid:image_uuid>/",
        VehicleImageDetailView.as_view(),
        name="admin-vehicle-image-detail",
    ),
    path(
        "<uuid:uuid>/images/<uuid:image_uuid>/file/",
        AdminVehicleImageFileView.as_view(),
        name="admin-vehicle-image-file",
    ),
    path(
        "<uuid:uuid>/documents/",
        AdminVehicleDocumentListView.as_view(),
        name="admin-vehicle-document-list",
    ),
    path(
        "<uuid:uuid>/documents/<uuid:document_uuid>/",
        AdminVehicleDocumentDetailView.as_view(),
        name="admin-vehicle-document-detail",
    ),
    path(
        "<uuid:uuid>/documents/<uuid:document_uuid>/file/",
        AdminVehicleDocumentFileView.as_view(),
        name="admin-vehicle-document-file",
    ),
]
