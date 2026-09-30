import hashlib
from typing import Any
from uuid import UUID

from django.utils import timezone
from django.utils.crypto import salted_hmac
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.tokens import AccessToken, Token

from accounts.models import AuthSession, User

INVALID_CREDENTIALS = "Invalid email or password."
INVALID_SESSION = "The session is invalid or has expired."


class SessionRefreshToken(Token):
    token_type = "refresh"
    lifetime = api_settings.REFRESH_TOKEN_LIFETIME


def password_fingerprint(user: User) -> str:
    return salted_hmac("accounts.session.password", user.password, algorithm="sha256").hexdigest()


def refresh_digest(jti: str) -> str:
    return hashlib.sha256(jti.encode()).hexdigest()


def token_identity(token: Token) -> tuple[UUID, UUID]:
    try:
        return UUID(str(token[api_settings.USER_ID_CLAIM])), UUID(str(token["sid"]))
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        raise AuthenticationFailed(INVALID_SESSION) from exc


def parse_refresh(raw_token: str) -> SessionRefreshToken:
    try:
        # Simple JWT 5.5 incorrectly annotates encoded token input as Token rather than str.
        token = SessionRefreshToken(raw_token)  # type: ignore[arg-type]
    except TokenError as exc:
        raise AuthenticationFailed(INVALID_SESSION) from exc
    if not isinstance(token[api_settings.JTI_CLAIM], str):
        raise AuthenticationFailed(INVALID_SESSION)
    return token


def issue_tokens(session: AuthSession, user: User) -> dict[str, Any]:
    """Caller holds the user/session locks; token strings are never persisted."""
    now = timezone.now()
    refresh = SessionRefreshToken()
    access = AccessToken()
    for token, lifetime in (
        (refresh, api_settings.REFRESH_TOKEN_LIFETIME),
        (access, api_settings.ACCESS_TOKEN_LIFETIME),
    ):
        token[api_settings.USER_ID_CLAIM] = str(user.uuid)
        token["sid"] = str(session.pk)
        token.set_exp(from_time=now, lifetime=min(lifetime, session.expires_at - now))
    session.refresh_digest = refresh_digest(refresh[api_settings.JTI_CLAIM])
    session.save(update_fields=["refresh_digest"])
    return {
        "access": str(access),
        "refresh": str(refresh),
        "access_expires_at": access["exp"],
        "refresh_expires_at": refresh["exp"],
    }
