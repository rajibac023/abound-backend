from uuid import UUID

from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from accounts.models import AuthSession, User
from accounts.policy import assignable_roles, can_manage_user
from accounts.roles import Role, django_privilege_flags
from audit.services import record_access_change

# PostgreSQL advisory lock namespace "ABOU", operation 3. Access administration is
# infrequent: serialize it so concurrent demotions cannot authorize stale actors.
ACCESS_LOCK_NAMESPACE = 0x41424F55
ACCESS_LOCK_OPERATION = 3


def update_user_access(
    *, actor: User, subject_uuid: UUID, role: str | None = None, is_active: bool | None = None
) -> User:
    if role is None and is_active is None:
        raise ValidationError({"non_field_errors": ["Supply role or is_active."]})
    if role is not None and role not in Role.values:
        raise ValidationError({"role": ["Select a supported role."]})
    if is_active is not None and not isinstance(is_active, bool):
        raise ValidationError({"is_active": ["Use a boolean value."]})

    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(%s, %s)",
                [ACCESS_LOCK_NAMESPACE, ACCESS_LOCK_OPERATION],
            )
        users = list(
            User.objects.select_for_update()
            .filter(Q(pk=actor.pk) | Q(uuid=subject_uuid))
            .order_by("pk")
        )
        current_actor = next((user for user in users if user.pk == actor.pk), None)
        subject = next((user for user in users if user.uuid == subject_uuid), None)
        if current_actor is None or not assignable_roles(current_actor):
            raise PermissionDenied()
        if subject is None:
            raise NotFound()
        if not can_manage_user(current_actor, subject):
            raise PermissionDenied()
        next_role = subject.role if role is None else role
        next_active = subject.is_active if is_active is None else is_active
        if next_role not in assignable_roles(current_actor):
            raise PermissionDenied()
        if (
            subject.role == Role.SUPER_ADMIN
            and subject.is_active
            and (next_role != Role.SUPER_ADMIN or not next_active)
            and not User.objects.filter(role=Role.SUPER_ADMIN, is_active=True)
            .exclude(pk=subject.pk)
            .exists()
        ):
            raise ValidationError({"role": ["At least one active SUPER_ADMIN must remain."]})
        if next_role == subject.role and next_active == subject.is_active:
            return subject

        previous_role, previous_is_active = subject.role, subject.is_active
        subject.role, subject.is_active = next_role, next_active
        subject.is_staff, subject.is_superuser = django_privilege_flags(next_role)
        subject.save(update_fields=["role", "is_active", "is_staff", "is_superuser", "updated_at"])
        AuthSession.objects.filter(user=subject, revoked_at__isnull=True).update(
            revoked_at=timezone.now()
        )
        record_access_change(
            actor=current_actor,
            subject=subject,
            previous_role=previous_role,
            previous_is_active=previous_is_active,
        )
        return subject
