"""Routes available only to tests; domain functionality is not exposed prematurely."""

from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.urls import include, path
from rest_framework import serializers
from rest_framework.exceptions import NotFound, Throttled
from rest_framework.generics import ListAPIView
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from common.urls import urlpatterns as health_patterns


class InputSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=30)


class EchoView(APIView):
    authentication_classes = ()
    permission_classes = [AllowAny]

    def post(self, request: Request) -> Response:
        serializer = InputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(serializer.validated_data, status=201)

    def get(self, request: Request) -> Response:
        return Response({"version": request.version})

    def delete(self, request: Request) -> Response:
        return Response(status=204)


class PrivateView(APIView):
    def get(self, request: Request) -> Response:
        return Response({"email": request.user.email})


class StaffView(PrivateView):
    permission_classes = [IsAdminUser]


class MissingView(APIView):
    authentication_classes = ()
    permission_classes = [AllowAny]

    def get(self, request: Request) -> Response:
        raise NotFound()


class FailureView(APIView):
    authentication_classes = ()
    permission_classes = [AllowAny]

    def get(self, request: Request) -> Response:
        raise RuntimeError("private-database-password-must-not-appear-in-response")


class RateLimitedView(APIView):
    authentication_classes = ()
    permission_classes = [AllowAny]

    def get(self, request: Request) -> Response:
        raise Throttled(wait=30)


class CollectionView(ListAPIView):
    authentication_classes = ()
    permission_classes = [AllowAny]
    serializer_class = InputSerializer
    queryset = [{"name": str(index)} for index in range(150)]


def django_failure(request: HttpRequest) -> HttpResponse:
    raise RuntimeError("private-framework-error")


def django_forbidden(request: HttpRequest) -> HttpResponse:
    raise PermissionDenied("private-permission-detail")


patterns = [
    *health_patterns,
    path("echo/", EchoView.as_view()),
    path("private/", PrivateView.as_view()),
    path("staff/", StaffView.as_view()),
    path("missing/", MissingView.as_view()),
    path("failure/", FailureView.as_view()),
    path("limited/", RateLimitedView.as_view()),
    path("collection/", CollectionView.as_view()),
    path("django-failure/", django_failure),
    path("django-forbidden/", django_forbidden),
]
urlpatterns = [path("api/v1/", include((patterns, "api"), namespace="v1"))]
handler400 = "common.errors.bad_request"
handler403 = "common.errors.permission_denied"
handler404 = "common.errors.not_found"
handler500 = "common.errors.server_error"
