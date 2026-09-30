from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from accounts.authorization import locked_actor
from accounts.models import User
from accounts.policy import Permission, has_permission
from audit.services import record_vehicle_event
from vehicles.choices import VehicleStatus
from vehicles.models import Vehicle
from vehicles.serializers import VehicleWriteSerializer


def authorized_actor(actor: User, permission: str) -> User:
    return locked_actor(actor, permission)


def persist_vehicle(vehicle: Vehicle) -> None:
    try:
        # Isolate a uniqueness race so callers receive validation errors outside the savepoint.
        with transaction.atomic():
            vehicle.save()
    except IntegrityError as exc:
        others = Vehicle.objects.exclude(pk=vehicle.pk)
        if others.filter(registration_number=vehicle.registration_number).exists():
            raise ValidationError(
                {"registration_number": ["Registration number already exists."]}
            ) from exc
        if vehicle.vin and others.filter(vin=vehicle.vin).exists():
            raise ValidationError({"vin": ["VIN/chassis number already exists."]}) from exc
        raise


@transaction.atomic
def create_vehicle(*, actor: User, data: Any) -> Vehicle:
    current = authorized_actor(actor, Permission.VEHICLES_CREATE)
    serializer = VehicleWriteSerializer(data=data)
    serializer.is_valid(raise_exception=True)
    vehicle = Vehicle(**serializer.validated_data)
    persist_vehicle(vehicle)
    record_vehicle_event(
        action="vehicles.created",
        actor=current,
        vehicle_uuid=vehicle.uuid,
        fields=list(serializer.validated_data),
        previous_status=None,
        status=vehicle.status,
    )
    return vehicle


@transaction.atomic
def update_vehicle(*, actor: User, vehicle_uuid: UUID, data: Any, partial: bool) -> Vehicle:
    current = authorized_actor(actor, Permission.VEHICLES_UPDATE)
    vehicle = Vehicle.objects.select_for_update().filter(uuid=vehicle_uuid).first()
    if vehicle is None:
        raise NotFound()
    serializer = VehicleWriteSerializer(vehicle, data=data, partial=partial)
    serializer.is_valid(raise_exception=True)
    previous_status = vehicle.status
    if (
        serializer.validated_data.get("status") == VehicleStatus.INACTIVE
        and vehicle.status != VehicleStatus.INACTIVE
        and not has_permission(current, Permission.VEHICLES_DELETE)
    ):
        raise PermissionDenied()
    changed = [
        field
        for field, value in serializer.validated_data.items()
        if getattr(vehicle, field) != value
    ]
    if not changed:
        return vehicle
    for field, value in serializer.validated_data.items():
        setattr(vehicle, field, value)
    persist_vehicle(vehicle)
    record_vehicle_event(
        action="vehicles.updated",
        actor=current,
        vehicle_uuid=vehicle.uuid,
        fields=changed,
        previous_status=previous_status,
        status=vehicle.status,
    )
    return vehicle


@transaction.atomic
def deactivate_vehicle(*, actor: User, vehicle_uuid: UUID) -> Vehicle:
    current = authorized_actor(actor, Permission.VEHICLES_DELETE)
    vehicle = Vehicle.objects.select_for_update().filter(uuid=vehicle_uuid).first()
    if vehicle is None:
        raise NotFound()
    if vehicle.status != VehicleStatus.INACTIVE:
        previous_status = vehicle.status
        vehicle.status = VehicleStatus.INACTIVE
        vehicle.save(update_fields=["status", "updated_at"])
        record_vehicle_event(
            action="vehicles.deactivated",
            actor=current,
            vehicle_uuid=vehicle.uuid,
            fields=["status"],
            previous_status=previous_status,
            status=vehicle.status,
        )
    return vehicle
