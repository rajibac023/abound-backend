from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.middleware.csrf import get_token
from django.shortcuts import render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.debug import sensitive_post_parameters
from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts import registration, services
from accounts.policy import permissions_for
from accounts.serializers import (
    ChangePasswordSerializer,
    EmailSerializer,
    LoginSerializer,
    PasswordResetRequestSerializer,
    RefreshSerializer,
    RegistrationSerializer,
    ResetConfirmSerializer,
    UserSerializer,
    VerifiedRegistrationSerializer,
)
from accounts.throttles import AuthIPThrottle, consume_limit


def enforce_csrf(request: Request) -> None:
    SessionAuthentication().enforce_csrf(request)


def token_response(tokens: dict[str, Any], *, client: str, user: Any = None) -> Response:
    payload = tokens.copy()
    if client == "web":
        payload.pop("refresh")
    if user is not None:
        payload["user"] = UserSerializer(user).data
    response = Response(payload)
    if client == "web":
        response.set_cookie(
            settings.AUTH_REFRESH_COOKIE_NAME,
            tokens["refresh"],
            max_age=max(0, tokens["refresh_expires_at"] - int(timezone.now().timestamp())),
            path="/api/v1/auth/",
            secure=settings.AUTH_COOKIE_SECURE,
            httponly=True,
            samesite="Strict",
        )
    return response


def clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        settings.AUTH_REFRESH_COOKIE_NAME, path="/api/v1/auth/", samesite="Strict"
    )


@method_decorator(sensitive_post_parameters(), name="dispatch")
class AuthView(APIView):
    throttle_classes = [AuthIPThrottle]
    throttle_scope = "account"

    def get_authenticate_header(self, request: Request) -> str:
        return 'Bearer realm="api"'


class PublicAuthView(AuthView):
    authentication_classes = ()
    permission_classes = [AllowAny]


class CSRFView(PublicAuthView):
    throttle_scope = "csrf"

    def get(self, request: Request) -> Response:
        return Response({"csrf_token": get_token(request._request)})


class RegistrationCodeView(PublicAuthView):
    throttle_scope = "register_code"

    def post(self, request: Request) -> Response:
        serializer = RegistrationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]
        consume_limit("register_code_identity", email)
        challenge = registration.send_registration_code(email=email)
        return Response(
            {
                "verification_id": str(challenge.pk),
                "email": challenge.email,
                "expires_in": settings.AUTH_REGISTRATION_CODE_SECONDS,
                "resend_after": settings.AUTH_REGISTRATION_RESEND_SECONDS,
            },
            status=202,
        )


class RegisterView(PublicAuthView):
    throttle_scope = "register"

    def post(self, request: Request) -> Response:
        serializer = VerifiedRegistrationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data.copy()
        data.pop("password_confirm")
        user = registration.complete_registration(**data)
        return Response(UserSerializer(user).data, status=201)


class LoginView(PublicAuthView):
    throttle_scope = "login"

    def post(self, request: Request) -> Response:
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if data["client"] == "web":
            enforce_csrf(request)
        consume_limit("login_identity", data["email"])
        user, tokens = services.login(**data)
        return token_response(tokens, client=data["client"], user=user)


class RefreshView(PublicAuthView):
    throttle_scope = "refresh"

    def post(self, request: Request) -> Response:
        serializer = RefreshSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        cookie = request.COOKIES.get(settings.AUTH_REFRESH_COOKIE_NAME)
        raw_token = serializer.validated_data.get("refresh")
        if cookie:
            enforce_csrf(request)
            if raw_token:
                raise ValidationError({"refresh": ["Use only one refresh transport."]})
            raw_token, client = cookie, "web"
        else:
            client = "mobile"
        if not raw_token:
            raise ValidationError({"refresh": ["A refresh token is required."]})
        tokens = services.refresh_session(raw_token=raw_token, client=client)
        return token_response(tokens, client=client)


class LogoutView(AuthView):
    def post(self, request: Request) -> Response:
        if request.COOKIES.get(settings.AUTH_REFRESH_COOKIE_NAME):
            enforce_csrf(request)
        services.logout(user=request.user, session_id=UUID(request.auth["sid"]))
        response = Response({"logged_out": True})
        clear_refresh_cookie(response)
        return response


class CurrentUserView(AuthView):
    def get(self, request: Request) -> Response:
        return Response(UserSerializer(request.user).data)

    def patch(self, request: Request) -> Response:
        user = services.update_profile(user=request.user, data=request.data)
        return Response(UserSerializer(user).data)


class CurrentPermissionsView(AuthView):
    def get(self, request: Request) -> Response:
        return Response(
            {"role": request.user.role, "permissions": sorted(permissions_for(request.user))}
        )


class ChangePasswordView(AuthView):
    def post(self, request: Request) -> Response:
        serializer = ChangePasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        consume_limit("password_change_identity", str(request.user.uuid))
        services.change_password(user=request.user, **serializer.validated_data)
        response = Response({"reauthentication_required": True})
        clear_refresh_cookie(response)
        return response


def _reset_url_for_client(client: str) -> str:
    mobile_url = getattr(settings, "AUTH_PASSWORD_RESET_URL_MOBILE", "")
    if client == "mobile" and mobile_url:
        return mobile_url
    return settings.AUTH_PASSWORD_RESET_URL


class PasswordResetView(PublicAuthView):
    throttle_scope = "reset"

    def post(self, request: Request) -> Response:
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]
        client = serializer.validated_data["client"]
        consume_limit("reset_identity", email)
        services.request_password_reset(email=email, reset_url=_reset_url_for_client(client))
        return Response(
            {"detail": "If an eligible account exists, password reset instructions will be sent."},
            status=202,
        )


class PasswordResetConfirmView(PublicAuthView):
    throttle_scope = "reset_confirm"

    def post(self, request: Request) -> Response:
        serializer = ResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.confirm_password_reset(**serializer.validated_data)
        response = Response({"reauthentication_required": True})
        clear_refresh_cookie(response)
        return response


class PasswordResetOpenView(View):
    """Bridge page: email button links here (plain http://), JS redirects into the app."""

    def get(self, request: HttpRequest) -> HttpResponse:
        uuid = request.GET.get("uuid", "")
        token = request.GET.get("token", "")
        mobile_base = getattr(settings, "AUTH_PASSWORD_RESET_URL_MOBILE", "aboundholidays://reset-password")
        params = urlencode({"uuid": uuid, "token": token})
        deep_link = f"{mobile_base}?{params}"
        return render(request, "accounts/password_reset_open.html", {"deep_link": deep_link})
