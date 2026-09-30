from django.urls import path

from bookings.views import (
    AdminBookingCancelView,
    AdminBookingDetailView,
    AdminBookingListView,
    AdminBookingTransitionView,
)

urlpatterns = [
    path("", AdminBookingListView.as_view(), name="admin-booking-list"),
    path("<uuid:uuid>/", AdminBookingDetailView.as_view(), name="admin-booking-detail"),
    path(
        "<uuid:uuid>/transition/",
        AdminBookingTransitionView.as_view(),
        name="admin-booking-transition",
    ),
    path("<uuid:uuid>/cancel/", AdminBookingCancelView.as_view(), name="admin-booking-cancel"),
]
