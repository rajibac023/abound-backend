from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from common.selectors import database_is_available


class HealthView(APIView):
    authentication_classes = ()
    permission_classes = [AllowAny]

    def get(self, request: Request) -> Response:
        if not database_is_available():
            return Response(
                {"database": ["Unavailable."]}, status=503, headers={"Cache-Control": "no-store"}
            )
        return Response({"status": "ok", "database": "ok"}, headers={"Cache-Control": "no-store"})


class LivenessView(APIView):
    authentication_classes = ()
    permission_classes = [AllowAny]

    def get(self, request: Request) -> Response:
        return Response({"status": "ok"}, headers={"Cache-Control": "no-store"})
