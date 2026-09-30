from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken, Token

from accounts.models import AuthSession, User
from accounts.tokens import INVALID_SESSION, password_fingerprint, token_identity


class SessionJWTAuthentication(JWTAuthentication):
    def get_validated_token(self, raw_token: bytes) -> Token:
        try:
            # The library accepts encoded bytes but its constructor annotation is incorrect.
            return AccessToken(raw_token)  # type: ignore[arg-type]
        except TokenError as exc:
            raise AuthenticationFailed(INVALID_SESSION) from exc

    def get_user(self, validated_token: Token) -> User:
        user_uuid, session_id = token_identity(validated_token)
        user = User.objects.filter(uuid=user_uuid, is_active=True).first()
        if (
            user is None
            or not AuthSession.objects.filter(
                pk=session_id,
                user=user,
                revoked_at__isnull=True,
                expires_at__gt=timezone.now(),
                password_fingerprint=password_fingerprint(user),
            ).exists()
        ):
            raise AuthenticationFailed(INVALID_SESSION)
        return user
