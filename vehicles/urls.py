from django.urls import path

from vehicles.media_views import CustomerVehicleImageFileView
from vehicles.views import CustomerVehicleDetailView, CustomerVehicleListView

urlpatterns = [
    path("", CustomerVehicleListView.as_view(), name="vehicle-list"),
    path("<uuid:uuid>/", CustomerVehicleDetailView.as_view(), name="vehicle-detail"),
    path(
        "<uuid:uuid>/images/<uuid:image_uuid>/file/",
        CustomerVehicleImageFileView.as_view(),
        name="vehicle-image-file",
    ),
]
