from django.urls import path

from pricing.views import PublicQuoteView

urlpatterns = [path("quote/", PublicQuoteView.as_view(), name="pricing-quote")]
