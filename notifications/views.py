from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from notifications.serializers import NotificationPreferenceSerializer
from notifications.services import list_preferences, set_preference


class NotificationPreferenceListView(APIView):
    """Self-service: every authenticated user manages only their own preferences.
    A channel absent from the response body has never been customized and is
    enabled by default (see `notifications.services.channel_enabled`)."""

    def get(self, request: Request) -> Response:
        return Response(list_preferences(request.user))

    def patch(self, request: Request) -> Response:
        serializer = NotificationPreferenceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        set_preference(user=request.user, **serializer.validated_data)
        return Response(list_preferences(request.user))
