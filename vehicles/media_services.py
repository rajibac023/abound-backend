from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from accounts.models import User
from accounts.policy import Permission
from audit.services import record_media_event
from vehicles.file_cleanup import discard_failed_upload, queue_image_cleanup
from vehicles.media_serializers import (
    FeatureAssignmentSerializer,
    FeatureWriteSerializer,
    ImageMetadataSerializer,
    ImageUploadSerializer,
)
from vehicles.models import Vehicle, VehicleFeature, VehicleImage
from vehicles.services import authorized_actor


def locked_vehicle(vehicle_uuid: UUID) -> Vehicle:
    vehicle = Vehicle.objects.select_for_update().filter(uuid=vehicle_uuid).first()
    if vehicle is None:
        raise NotFound()
    return vehicle


def vehicle_image(vehicle: Vehicle, image_uuid: UUID) -> VehicleImage:
    image = vehicle.images.filter(uuid=image_uuid).first()
    if image is None:
        raise NotFound()
    return image


def make_primary(vehicle: Vehicle, image: VehicleImage) -> None:
    # Caller holds the parent lock. Clear first to satisfy the partial unique index.
    vehicle.images.filter(is_primary=True).exclude(pk=image.pk).update(
        is_primary=False, updated_at=timezone.now()
    )
    image.is_primary = True


def upload_image(*, actor: User, vehicle_uuid: UUID, data: Any) -> VehicleImage:
    stored_name = None
    try:
        # A real commit boundary prevents a caller's later rollback from orphaning the file.
        with transaction.atomic(durable=True):
            current = authorized_actor(actor, Permission.VEHICLES_UPDATE)
            vehicle = locked_vehicle(vehicle_uuid)
            serializer = ImageUploadSerializer(data=data)
            serializer.is_valid(raise_exception=True)
            values = serializer.validated_data.copy()
            contents = values.pop("image")
            if "display_order" not in values:
                last = vehicle.images.aggregate(last=Max("display_order"))["last"]
                values["display_order"] = 0 if last is None else last + 1
                if values["display_order"] > 2147483647:
                    raise ValidationError(
                        {"display_order": ["Specify an order within the supported range."]}
                    )
            image = VehicleImage(vehicle=vehicle, **values)
            if image.is_primary or not vehicle.images.exists():
                make_primary(vehicle, image)
            image.image.save(contents.name, contents, save=False)
            stored_name = image.image.name
            image.save()
            record_media_event(
                action="vehicles.image_uploaded",
                actor=current,
                subject_uuid=image.uuid,
                vehicle_uuid=vehicle.uuid,
                fields=list(serializer.validated_data),
            )
        return image
    except Exception:
        if stored_name:
            discard_failed_upload(stored_name)
        raise


@transaction.atomic
def update_image(*, actor: User, vehicle_uuid: UUID, image_uuid: UUID, data: Any) -> VehicleImage:
    current = authorized_actor(actor, Permission.VEHICLES_UPDATE)
    vehicle = locked_vehicle(vehicle_uuid)
    image = vehicle_image(vehicle, image_uuid)
    serializer = ImageMetadataSerializer(data=data)
    serializer.is_valid(raise_exception=True)
    values = serializer.validated_data
    if image.is_primary and values.get("is_primary") is False:
        raise ValidationError({"is_primary": ["Select another image as primary instead."]})
    changed = [field for field, value in values.items() if getattr(image, field) != value]
    if not changed:
        return image
    if values.get("is_primary"):
        make_primary(vehicle, image)
    for field, value in values.items():
        setattr(image, field, value)
    image.save()
    record_media_event(
        action="vehicles.image_updated",
        actor=current,
        subject_uuid=image.uuid,
        vehicle_uuid=vehicle.uuid,
        fields=changed,
    )
    return image


@transaction.atomic
def delete_image(*, actor: User, vehicle_uuid: UUID, image_uuid: UUID) -> None:
    current = authorized_actor(actor, Permission.VEHICLES_UPDATE)
    vehicle = locked_vehicle(vehicle_uuid)
    image = vehicle_image(vehicle, image_uuid)
    name, was_primary = image.image.name, image.is_primary
    image.delete()
    if was_primary:
        replacement = vehicle.images.order_by("display_order", "id").first()
        if replacement is not None:
            replacement.is_primary = True
            replacement.save(update_fields=["is_primary", "updated_at"])
    record_media_event(
        action="vehicles.image_deleted",
        actor=current,
        subject_uuid=image_uuid,
        vehicle_uuid=vehicle.uuid,
        fields=["image", "is_primary"],
    )
    queue_image_cleanup(name)


@transaction.atomic
def save_feature(*, actor: User, data: Any, feature_uuid: UUID | None = None) -> VehicleFeature:
    current = authorized_actor(actor, Permission.VEHICLES_UPDATE)
    feature = None
    if feature_uuid is not None:
        feature = VehicleFeature.objects.select_for_update().filter(uuid=feature_uuid).first()
        if feature is None:
            raise NotFound()
    serializer = FeatureWriteSerializer(feature, data=data, partial=feature is not None)
    serializer.is_valid(raise_exception=True)
    creating = feature is None
    feature = feature or VehicleFeature()
    for field, value in serializer.validated_data.items():
        setattr(feature, field, value)
    try:
        with transaction.atomic():
            feature.save()
    except IntegrityError as exc:
        if VehicleFeature.objects.filter(name__iexact=feature.name).exclude(pk=feature.pk).exists():
            raise ValidationError({"name": ["A feature with this name already exists."]}) from exc
        raise
    record_media_event(
        action="vehicles.feature_created" if creating else "vehicles.feature_updated",
        actor=current,
        subject_uuid=feature.uuid,
        fields=list(serializer.validated_data),
    )
    return feature


@transaction.atomic
def assign_features(*, actor: User, vehicle_uuid: UUID, data: Any) -> Vehicle:
    current = authorized_actor(actor, Permission.VEHICLES_UPDATE)
    vehicle = locked_vehicle(vehicle_uuid)
    serializer = FeatureAssignmentSerializer(data=data)
    serializer.is_valid(raise_exception=True)
    requested = serializer.validated_data["feature_uuids"]
    features = list(
        VehicleFeature.objects.select_for_update()
        .filter(uuid__in=requested, is_active=True)
        .order_by("id")
    )
    if len(features) != len(requested):
        raise ValidationError({"feature_uuids": ["Choose existing active features."]})
    if set(vehicle.features.values_list("pk", flat=True)) != {feature.pk for feature in features}:
        vehicle.features.set(features)
        record_media_event(
            action="vehicles.features_assigned",
            actor=current,
            subject_uuid=vehicle.uuid,
            vehicle_uuid=vehicle.uuid,
            fields=["features"],
        )
    return vehicle
