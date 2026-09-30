from typing import Any
from uuid import UUID

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound

from accounts.authorization import locked_actor
from accounts.models import AuthSession, User
from accounts.policy import Permission
from accounts.roles import Role
from audit.services import record_customer_event
from customers.serializers import CustomerUpdateSerializer


@transaction.atomic
def update_customer(*, actor: User, customer_uuid: UUID, data: Any) -> User:
    current = locked_actor(actor, Permission.CUSTOMERS_UPDATE)
    subject = (
        User.objects.select_for_update()
        .filter(uuid=customer_uuid, role=Role.CUSTOMER)
        .first()
    )
    if subject is None:
        raise NotFound()
    serializer = CustomerUpdateSerializer(data=data)
    serializer.is_valid(raise_exception=True)
    values = serializer.validated_data
    changed = [field for field, value in values.items() if getattr(subject, field) != value]
    if not changed:
        return subject
    for field, value in values.items():
        setattr(subject, field, value)
    subject.save(update_fields=[*changed, "updated_at"])
    if "is_active" in changed and not subject.is_active:
        # A deactivated customer's sessions must not remain usable.
        AuthSession.objects.filter(user=subject, revoked_at__isnull=True).update(
            revoked_at=timezone.now()
        )
    record_customer_event(
        action="customers.updated", actor=current, subject_uuid=subject.uuid, fields=changed
    )
    return subject
