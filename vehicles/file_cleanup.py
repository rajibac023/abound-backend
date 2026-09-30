import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from vehicles.models import DocumentFileDeletion, ImageFileDeletion, VehicleDocument, VehicleImage

logger = logging.getLogger(__name__)


def cleanup_image_file(job_id: int | None = None) -> bool:
    with transaction.atomic():
        jobs = ImageFileDeletion.objects.select_for_update(skip_locked=True).filter(
            available_at__lte=timezone.now()
        )
        if job_id is not None:
            jobs = jobs.filter(pk=job_id)
        job = jobs.order_by("pk").first()
        if job is None:
            return False
        try:
            if not job.name.startswith("vehicle-images/"):
                raise ValueError("Invalid image storage namespace.")
            if not VehicleImage.objects.filter(image=job.name).exists():
                VehicleImage._meta.get_field("image").storage.delete(job.name)
        except Exception:
            job.attempts += 1
            job.available_at = timezone.now() + timedelta(seconds=60)
            job.save(update_fields=["attempts", "available_at"])
            logger.warning("Vehicle image cleanup failed; queued for retry")
        else:
            job.delete()
    return True


def attempt_image_cleanup(job_id: int) -> None:
    try:
        cleanup_image_file(job_id)
    except Exception:
        logger.warning("Vehicle image cleanup deferred; queued job retained")


def queue_image_cleanup(name: str) -> None:
    job, _ = ImageFileDeletion.objects.get_or_create(name=name)
    transaction.on_commit(lambda: attempt_image_cleanup(job.pk))


def discard_failed_upload(name: str) -> None:
    """The image transaction failed; remove its unreferenced file or persist a retry."""
    try:
        VehicleImage._meta.get_field("image").storage.delete(name)
    except Exception:
        logger.warning("Failed upload cleanup deferred")
        queue_image_cleanup(name)


def cleanup_document_file(job_id: int | None = None) -> bool:
    with transaction.atomic():
        jobs = DocumentFileDeletion.objects.select_for_update(skip_locked=True).filter(
            available_at__lte=timezone.now()
        )
        if job_id is not None:
            jobs = jobs.filter(pk=job_id)
        job = jobs.order_by("pk").first()
        if job is None:
            return False
        try:
            if not job.name.startswith("vehicle-documents/"):
                raise ValueError("Invalid document storage namespace.")
            if not VehicleDocument.objects.filter(file=job.name).exists():
                VehicleDocument._meta.get_field("file").storage.delete(job.name)
        except Exception:
            job.attempts += 1
            job.available_at = timezone.now() + timedelta(seconds=60)
            job.save(update_fields=["attempts", "available_at"])
            logger.warning("Vehicle document cleanup failed; queued for retry")
        else:
            job.delete()
    return True


def attempt_document_cleanup(job_id: int) -> None:
    try:
        cleanup_document_file(job_id)
    except Exception:
        logger.warning("Vehicle document cleanup deferred; queued job retained")


def queue_document_cleanup(name: str) -> None:
    job, _ = DocumentFileDeletion.objects.get_or_create(name=name)
    transaction.on_commit(lambda: attempt_document_cleanup(job.pk))


def discard_failed_document_upload(name: str) -> None:
    """The document transaction failed; remove its unreferenced file or persist a retry."""
    try:
        VehicleDocument._meta.get_field("file").storage.delete(name)
    except Exception:
        logger.warning("Failed document upload cleanup deferred")
        queue_document_cleanup(name)
