from django.urls import path

from customers.views import AdminCustomerDetailView, AdminCustomerListView

urlpatterns = [
    path("", AdminCustomerListView.as_view(), name="admin-customer-list"),
    path("<uuid:uuid>/", AdminCustomerDetailView.as_view(), name="admin-customer-detail"),
]
