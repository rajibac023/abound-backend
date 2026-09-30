from django.urls import path

from common.views import HealthView, LivenessView

app_name = "api"
urlpatterns = [
    path("health/", HealthView.as_view(), name="health"),
    path("health/live/", LivenessView.as_view(), name="liveness"),
]
