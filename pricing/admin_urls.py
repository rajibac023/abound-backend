from django.urls import path

from pricing.views import (
    AdminDiscountDetailView,
    AdminDiscountListView,
    AdminQuoteView,
    AdminRatesView,
)

urlpatterns = [
    path("quote/", AdminQuoteView.as_view(), name="admin-pricing-quote"),
    path("vehicles/<uuid:uuid>/", AdminRatesView.as_view(), name="admin-pricing-rates"),
    path("discounts/", AdminDiscountListView.as_view(), name="admin-pricing-discount-list"),
    path(
        "discounts/<uuid:uuid>/",
        AdminDiscountDetailView.as_view(),
        name="admin-pricing-discount-detail",
    ),
]
