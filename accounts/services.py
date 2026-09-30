from datetime import timedelta
from typing import Any
from uuid import UUID

from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth.tokens import default_token_generator
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.views.decorators.debug import sensitive_variables
from rest_framework import serializers
from rest_framework.exceptions import AuthenticationFailed

from accounts.models import AuthSession, User
from accounts.serializers import ProfileUpdateSerializer, validate_new_password
from accounts.tokens import (
    INVALID_CREDENTIALS,
    INVALID_SESSION,
    issue_tokens,
    parse_refresh,
    password_fingerprint,
    refresh_digest,
    token_identity,
)
from audit.services import record_event, record_profile_event
from notifications.choices import NotificationEvent
from notifications.models import PasswordResetDelivery
from notifications.services import notify


@sensitive_variables("password")
def register_user(
    *, email: str, password: str, first_name: str, last_name: str, phone: str = ""
) -> User:
    try:
        with transaction.atomic():
            user = User.objects.create_user(
                email=email,
                password=password,
                first_name=first_name,
                last_name=last_name,
                phone=phone,
                role=User.Role.CUSTOMER,
            )
            record_event("auth.registered", user)
            notify(
                event=NotificationEvent.ACCOUNT_REGISTERED,
                recipient=user,
                context={"first_name": user.first_name},
                related_type="user",
                related_uuid=user.uuid,
            )
            return user
    except IntegrityError as exc:
        if User.objects.filter(email=email).exists():
            raise serializers.ValidationError(
                {"email": ["Unable to register with this email address."]}
            ) from exc
        raise


@sensitive_variables("password", "tokens")
def login(*, email: str, password: str, client: str) -> tuple[User, dict[str, Any]]:
    authenticated = authenticate(email=email, password=password)
    if authenticated is None:
        record_event("auth.login_failed")
        raise AuthenticationFailed(INVALID_CREDENTIALS)
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=authenticated.pk)
        if not user.is_active or not constant_time_compare(user.password, authenticated.password):
            raise AuthenticationFailed(INVALID_CREDENTIALS)
        session = AuthSession.objects.create(
            user=user,
            client=client,
            password_fingerprint=password_fingerprint(user),
            expires_at=timezone.now() + timedelta(days=settings.AUTH_SESSION_DAYS),
        )
        tokens = issue_tokens(session, user)
        user.last_login = timezone.now()
        user.save(update_fields=["last_login", "updated_at"])
        record_event("auth.login_succeeded", user, session.pk)
    return user, tokens


@sensitive_variables("raw_token", "token", "tokens")
def refresh_session(*, raw_token: str, client: str) -> dict[str, Any]:
    token = parse_refresh(raw_token)
    user_uuid, session_id = token_identity(token)
    replayed = False
    with transaction.atomic():
        # Every credential-changing workflow uses user -> session lock order.
        user = User.objects.select_for_update().filter(uuid=user_uuid, is_active=True).first()
        if user is None:
            raise AuthenticationFailed(INVALID_SESSION)
        session = AuthSession.objects.select_for_update().filter(pk=session_id, user=user).first()
        if (
            session is None
            or session.revoked_at is not None
            or session.expires_at <= timezone.now()
            or session.client != client
            or not constant_time_compare(session.password_fingerprint, password_fingerprint(user))
        ):
            raise AuthenticationFailed(INVALID_SESSION)
        digest = refresh_digest(token["jti"])
        if not constant_time_compare(session.refresh_digest, digest):
            session.revoked_at = timezone.now()
            session.save(update_fields=["revoked_at"])
            record_event("auth.refresh_replay", user, session.pk)
            replayed = True
        else:
            tokens = issue_tokens(session, user)
    # Raise after commit so detecting replay actually persists revocation.
    if replayed:
        raise AuthenticationFailed(INVALID_SESSION)
    return tokens


def logout(*, user: User, session_id: UUID) -> None:
    with transaction.atomic():
        AuthSession.objects.filter(pk=session_id, user=user, revoked_at__isnull=True).update(
            revoked_at=timezone.now()
        )
        record_event("auth.logged_out", user, session_id)


@sensitive_variables("current_password", "password", "password_confirm")
def change_password(
    *, user: User, current_password: str, password: str, password_confirm: str
) -> None:
    with transaction.atomic():
        locked_user = User.objects.select_for_update().get(pk=user.pk)
        if not locked_user.is_active or not locked_user.check_password(current_password):
            raise serializers.ValidationError(
                {"current_password": ["Current password is incorrect."]}
            )
        validate_new_password(password, password_confirm, locked_user)
        locked_user.set_password(password)
        locked_user.save(update_fields=["password", "updated_at"])
        AuthSession.objects.filter(user=locked_user, revoked_at__isnull=True).update(
            revoked_at=timezone.now()
        )
        record_event("auth.password_changed", locked_user)


def update_profile(*, user: User, data: Any) -> User:
    serializer = ProfileUpdateSerializer(data=data)
    serializer.is_valid(raise_exception=True)
    values = serializer.validated_data
    with transaction.atomic():
        locked_user = User.objects.select_for_update().get(pk=user.pk)
        changed = [
            field for field, value in values.items() if getattr(locked_user, field) != value
        ]
        if changed:
            for field, value in values.items():
                setattr(locked_user, field, value)
            locked_user.save(update_fields=[*changed, "updated_at"])
            record_profile_event(actor=locked_user, fields=changed)
        return locked_user


def request_password_reset(*, email: str, reset_url: str = "") -> None:
    user = User.objects.filter(email=email, is_active=True).first()
    if user is not None and not user.has_usable_password():
        user = None
    # Queue unknown identities too; all HTTP responses match and delivery happens out of band.
    PasswordResetDelivery.objects.create(
        user=user,
        password_fingerprint=password_fingerprint(user) if user is not None else "",
        expires_at=timezone.now() + timedelta(seconds=settings.PASSWORD_RESET_TIMEOUT),
        reset_url=reset_url,
    )


@sensitive_variables("token", "password", "password_confirm")
def confirm_password_reset(*, uuid: UUID, token: str, password: str, password_confirm: str) -> None:
    with transaction.atomic():
        user = User.objects.select_for_update().filter(uuid=uuid, is_active=True).first()
        if (
            user is None
            or not user.has_usable_password()
            or not default_token_generator.check_token(user, token)
        ):
            raise serializers.ValidationError(
                {"token": ["The reset link is invalid or has expired."]}
            )
        validate_new_password(password, password_confirm, user)
        user.set_password(password)
        user.save(update_fields=["password", "updated_at"])
        AuthSession.objects.filter(user=user, revoked_at__isnull=True).update(
            revoked_at=timezone.now()
        )
        record_event("auth.password_reset", user)
