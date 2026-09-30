from typing import Any
from uuid import UUID

from accounts.models import User
from audit.models import AuditEvent
from common.middleware import client_ip_context, request_id_context, user_agent_context


def _request_meta() -> dict[str, Any]:
    request_id = request_id_context.get()
    return {
        "request_id": None if request_id == "-" else request_id,
        "ip_address": client_ip_context.get() or None,
        "user_agent": user_agent_context.get(),
    }


def record_event(action: str, user: User | None = None, session_id: UUID | None = None) -> None:
    AuditEvent.objects.create(
        action=action,
        actor=user,
        session_id=session_id,
        entity_type="user",
        subject_uuid=user.uuid if user else None,
        **_request_meta(),
    )


def record_access_change(
    *, actor: User, subject: User, previous_role: str, previous_is_active: bool
) -> None:
    AuditEvent.objects.create(
        action="accounts.access_changed",
        actor=actor,
        entity_type="user",
        subject_uuid=subject.uuid,
        previous_value={"role": previous_role, "is_active": previous_is_active},
        new_value={"role": subject.role, "is_active": subject.is_active},
        changes={
            "role": {"before": previous_role, "after": subject.role},
            "is_active": {"before": previous_is_active, "after": subject.is_active},
        },
        **_request_meta(),
    )


def record_vehicle_event(
    *,
    action: str,
    actor: User,
    vehicle_uuid: UUID,
    fields: list[str],
    previous_status: str | None,
    status: str,
) -> None:
    AuditEvent.objects.create(
        action=action,
        actor=actor,
        entity_type="vehicle",
        subject_uuid=vehicle_uuid,
        previous_value={"status": previous_status} if previous_status != status else None,
        new_value={"status": status} if previous_status != status else None,
        # Record field names, never the content of internal notes or identifiers.
        changes={"fields": sorted(fields), "status": {"before": previous_status, "after": status}},
        **_request_meta(),
    )


def record_media_event(
    *,
    action: str,
    actor: User,
    subject_uuid: UUID,
    fields: list[str],
    vehicle_uuid: UUID | None = None,
) -> None:
    AuditEvent.objects.create(
        action=action,
        actor=actor,
        entity_type="vehicle_media",
        subject_uuid=subject_uuid,
        changes={
            "fields": sorted(fields),
            "vehicle_uuid": str(vehicle_uuid) if vehicle_uuid else None,
        },
        **_request_meta(),
    )


def record_pricing_event(
    *,
    action: str,
    actor: User,
    subject_uuid: UUID,
    fields: list[str],
    previous_value: dict[str, Any] | None = None,
    new_value: dict[str, Any] | None = None,
) -> None:
    AuditEvent.objects.create(
        action=action,
        actor=actor,
        entity_type="pricing",
        subject_uuid=subject_uuid,
        previous_value=previous_value,
        new_value=new_value,
        changes={"fields": sorted(fields)},
        **_request_meta(),
    )


def record_maintenance_event(
    *, action: str, actor: User, subject_uuid: UUID, vehicle_uuid: UUID, fields: list[str]
) -> None:
    AuditEvent.objects.create(
        action=action,
        actor=actor,
        entity_type="maintenance_record",
        subject_uuid=subject_uuid,
        changes={"fields": sorted(fields), "vehicle_uuid": str(vehicle_uuid)},
        **_request_meta(),
    )


def record_availability_event(
    *, action: str, actor: User, subject_uuid: UUID, vehicle_uuid: UUID, fields: list[str]
) -> None:
    AuditEvent.objects.create(
        action=action,
        actor=actor,
        entity_type="availability_block",
        subject_uuid=subject_uuid,
        changes={"fields": sorted(fields), "vehicle_uuid": str(vehicle_uuid)},
        **_request_meta(),
    )


def record_profile_event(*, actor: User, fields: list[str]) -> None:
    AuditEvent.objects.create(
        action="accounts.profile_updated",
        actor=actor,
        entity_type="user",
        subject_uuid=actor.uuid,
        changes={"fields": sorted(fields)},
        **_request_meta(),
    )


def record_customer_event(
    *, action: str, actor: User, subject_uuid: UUID, fields: list[str]
) -> None:
    AuditEvent.objects.create(
        action=action,
        actor=actor,
        entity_type="customer",
        subject_uuid=subject_uuid,
        changes={"fields": sorted(fields)},
        **_request_meta(),
    )


def record_booking_event(
    *,
    action: str,
    actor: User,
    subject_uuid: UUID,
    fields: list[str],
    status_before: str | None,
    status_after: str,
) -> None:
    AuditEvent.objects.create(
        action=action,
        actor=actor,
        entity_type="booking",
        subject_uuid=subject_uuid,
        previous_value={"status": status_before} if status_before != status_after else None,
        new_value={"status": status_after} if status_before != status_after else None,
        changes={
            "fields": sorted(fields),
            "status": {"before": status_before, "after": status_after},
        },
        **_request_meta(),
    )


def record_document_event(
    *, action: str, actor: User, subject_uuid: UUID, vehicle_uuid: UUID, fields: list[str]
) -> None:
    AuditEvent.objects.create(
        action=action,
        actor=actor,
        entity_type="vehicle_document",
        subject_uuid=subject_uuid,
        # Record field names only; document numbers and notes are never audited verbatim.
        changes={"fields": sorted(fields), "vehicle_uuid": str(vehicle_uuid)},
        **_request_meta(),
    )
