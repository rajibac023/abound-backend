from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import UploadedFile
from django.core.files.uploadhandler import FileUploadHandler
from rest_framework.exceptions import APIException, ValidationError

from vehicles.image_processing import normalize_image

PDF_SIGNATURE = b"%PDF-"


class DocumentUploadTooLarge(APIException):
    status_code = 413
    default_detail = "The document exceeds the configured upload size limit."
    default_code = "document_too_large"


class BoundedDocumentUploadHandler(FileUploadHandler):
    """Stop oversized file data while streaming, including forged content lengths."""

    def receive_data_chunk(self, raw_data: bytes, start: int) -> bytes:
        self.total_received = getattr(self, "total_received", 0) + len(raw_data)
        if self.total_received > settings.VEHICLE_DOCUMENT_MAX_BYTES:
            raise DocumentUploadTooLarge()
        return raw_data

    def file_complete(self, file_size: int) -> None:
        return None


def normalize_document(upload: UploadedFile) -> ContentFile:
    if upload.size is None or upload.size > settings.VEHICLE_DOCUMENT_MAX_BYTES:
        raise DocumentUploadTooLarge()
    upload.seek(0)
    header = upload.read(5)
    upload.seek(0)
    if header == PDF_SIGNATURE:
        # PDFs cannot be safely re-encoded without a PDF-processing dependency; the file is
        # stored as-is and served with a strict CSP and no inline rendering (see media views).
        return ContentFile(upload.read(), name="document.pdf")
    error = "Upload a valid PDF, JPEG, or PNG document within the upload size limit."
    try:
        normalized = normalize_image(upload)
    except ValidationError as exc:
        raise ValidationError(error) from exc
    extension = "png" if normalized.name.endswith(".png") else "jpg"
    return ContentFile(normalized.read(), name=f"document.{extension}")
