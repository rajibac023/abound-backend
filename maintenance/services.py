from typing import Any
from uuid import UUID

from django.db import transaction
from rest_framework.exceptions import NotFound

from accounts.authorization import locked_actor
from accounts.models import User
from accounts.policy import Permission
from audit.services import record_maintenance_event
from availability.services import assert_no_reserved_booking, lock_vehicle_schedules
from maintenance.choices import BLOCKING_STATUSES, MaintenanceStatus
from maintenance.models import MaintenanceRecord
from maintenance.serializers import MaintenanceWriteSerializer


@transaction.atomic
def create_maintenance_record(*, actor: User, data: Any) -> MaintenanceRecord:
    current = locked_actor(actor, Permission.MAINTENANCE_CREATE)
    serializer = MaintenanceWriteSerializer(data=data)
    serializer.is_valid(raise_exception=True)
    values = serializer.validated_data
    lock_vehicle_schedules(values["vehicle"].pk)
    if values.get("status", MaintenanceStatus.SCHEDULED) in BLOCKING_STATUSES:
        assert_no_reserved_booking(
            vehicle=values["vehicle"], start_date=values["start_date"], end_date=values["end_date"]
        )
    record = MaintenanceRecord.objects.create(created_by=current, **serializer.validated_data)
    record_maintenance_event(
        action="maintenance.created",
        actor=current,
        subject_uuid=record.uuid,
        vehicle_uuid=record.vehicle.uuid,
        fields=list(serializer.validated_data),
    )
    return record


@transaction.atomic
def update_maintenance_record(*, actor: User, record_uuid: UUID, data: Any) -> MaintenanceRecord:
    current = locked_actor(actor, Permission.MAINTENANCE_UPDATE)
    record = MaintenanceRecord.objects.select_for_update().filter(uuid=record_uuid).first()
    if record is None:
        raise NotFound()
    serializer = MaintenanceWriteSerializer(record, data=data, partial=True)
    serializer.is_valid(raise_exception=True)
    values = serializer.validated_data
    vehicle = values.get("vehicle", record.vehicle)
    lock_vehicle_schedules(record.vehicle_id, vehicle.pk)
    if values.get("status", record.status) in BLOCKING_STATUSES:
        assert_no_reserved_booking(
            vehicle=vehicle,
            start_date=values.get("start_date", record.start_date),
            end_date=values.get("end_date", record.end_date),
        )
    changed = [
        field
        for field, value in serializer.validated_data.items()
        if getattr(record, field) != value
    ]
    if changed:
        for field, value in serializer.validated_data.items():
            setattr(record, field, value)
        record.save(update_fields=[*changed, "updated_at"])
        record_maintenance_event(
            action="maintenance.updated",
            actor=current,
            subject_uuid=record.uuid,
            vehicle_uuid=record.vehicle.uuid,
            fields=changed,
        )
    return record
