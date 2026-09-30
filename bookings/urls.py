from django.urls import path

from bookings.views import BookingCancelView, BookingDetailView, BookingListView

urlpatterns = [
    path("", BookingListView.as_view(), name="booking-list"),
    path("<uuid:uuid>/", BookingDetailView.as_view(), name="booking-detail"),
    path("<uuid:uuid>/cancel/", BookingCancelView.as_view(), name="booking-cancel"),
]
