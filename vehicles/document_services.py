from typing import Any
from uuid import UUID

from django.db import transaction
from rest_framework.exceptions import NotFound

from accounts.models import User
from accounts.policy import Permission
from audit.services import record_document_event
from vehicles.document_serializers import DocumentMetadataSerializer, DocumentUploadSerializer
from vehicles.file_cleanup import discard_failed_document_upload, queue_document_cleanup
from vehicles.media_services import locked_vehicle
from vehicles.models import Vehicle, VehicleDocument
from vehicles.services import authorized_actor


def vehicle_document(vehicle: Vehicle, document_uuid: UUID) -> VehicleDocument:
    document = vehicle.documents.filter(uuid=document_uuid).first()
    if document is None:
        raise NotFound()
    return document


def upload_document(*, actor: User, vehicle_uuid: UUID, data: Any) -> VehicleDocument:
    stored_name = None
    try:
        # A real commit boundary prevents a caller's later rollback from orphaning the file.
        with transaction.atomic(durable=True):
            current = authorized_actor(actor, Permission.VEHICLES_UPDATE)
            vehicle = locked_vehicle(vehicle_uuid)
            serializer = DocumentUploadSerializer(data=data)
            serializer.is_valid(raise_exception=True)
            values = serializer.validated_data.copy()
            contents = values.pop("file")
            document = VehicleDocument(vehicle=vehicle, **values)
            document.file.save(contents.name, contents, save=False)
            stored_name = document.file.name
            document.save()
            record_document_event(
                action="vehicles.document_uploaded",
                actor=current,
                subject_uuid=document.uuid,
                vehicle_uuid=vehicle.uuid,
                fields=list(serializer.validated_data),
            )
        return document
    except Exception:
        if stored_name:
            discard_failed_document_upload(stored_name)
        raise


@transaction.atomic
def update_document(
    *, actor: User, vehicle_uuid: UUID, document_uuid: UUID, data: Any
) -> VehicleDocument:
    current = authorized_actor(actor, Permission.VEHICLES_UPDATE)
    vehicle = locked_vehicle(vehicle_uuid)
    document = vehicle_document(vehicle, document_uuid)
    serializer = DocumentMetadataSerializer(data=data)
    serializer.is_valid(raise_exception=True)
    values = serializer.validated_data
    changed = [field for field, value in values.items() if getattr(document, field) != value]
    if not changed:
        return document
    for field, value in values.items():
        setattr(document, field, value)
    document.save()
    record_document_event(
        action="vehicles.document_updated",
        actor=current,
        subject_uuid=document.uuid,
        vehicle_uuid=vehicle.uuid,
        fields=changed,
    )
    return document


@transaction.atomic
def delete_document(*, actor: User, vehicle_uuid: UUID, document_uuid: UUID) -> None:
    current = authorized_actor(actor, Permission.VEHICLES_UPDATE)
    vehicle = locked_vehicle(vehicle_uuid)
    document = vehicle_document(vehicle, document_uuid)
    name = document.file.name
    document.delete()
    record_document_event(
        action="vehicles.document_deleted",
        actor=current,
        subject_uuid=document_uuid,
        vehicle_uuid=vehicle.uuid,
        fields=["file"],
    )
    queue_document_cleanup(name)
