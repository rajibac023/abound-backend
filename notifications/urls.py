from django.urls import path

from notifications import views

urlpatterns = [
    path(
        "preferences/",
        views.NotificationPreferenceListView.as_view(),
        name="notification-preferences",
    ),
]
