from django.urls import path

from availability.views import PublicAvailabilityCheckView

urlpatterns = [path("check/", PublicAvailabilityCheckView.as_view(), name="availability-check")]
