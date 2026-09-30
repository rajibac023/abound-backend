import warnings
from io import BytesIO

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import UploadedFile
from django.core.files.uploadhandler import FileUploadHandler
from PIL import Image, ImageOps, UnidentifiedImageError
from rest_framework.exceptions import APIException, ValidationError


class ImageUploadTooLarge(APIException):
    status_code = 413
    default_detail = "The image exceeds the configured upload size limit."
    default_code = "image_too_large"


class BoundedImageUploadHandler(FileUploadHandler):
    """Stop oversized file data while streaming, including forged content lengths."""

    def receive_data_chunk(self, raw_data: bytes, start: int) -> bytes:
        self.total_received = getattr(self, "total_received", 0) + len(raw_data)
        if self.total_received > settings.VEHICLE_IMAGE_MAX_BYTES:
            raise ImageUploadTooLarge()
        return raw_data

    def file_complete(self, file_size: int) -> None:
        return None


def normalize_image(upload: UploadedFile) -> ContentFile:
    if upload.size is None or upload.size > settings.VEHICLE_IMAGE_MAX_BYTES:
        raise ImageUploadTooLarge()
    error = "Upload a valid, single-frame JPEG, PNG, or WebP image within the image limits."
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            upload.seek(0)
            with Image.open(upload) as candidate:
                if (
                    candidate.format not in {"JPEG", "PNG", "WEBP"}
                    or getattr(candidate, "n_frames", 1) != 1
                ):
                    raise ValidationError(error)
                if candidate.width * candidate.height > settings.VEHICLE_IMAGE_MAX_PIXELS:
                    raise ValidationError(error)
                candidate.verify()
            upload.seek(0)
            with Image.open(upload) as decoded:
                decoded.load()  # Reject truncation; verify() alone is insufficient for JPEG.
                oriented = ImageOps.exif_transpose(decoded)
                oriented.thumbnail(
                    (settings.VEHICLE_IMAGE_MAX_EDGE, settings.VEHICLE_IMAGE_MAX_EDGE)
                )
                alpha = "A" in oriented.getbands() or "transparency" in oriented.info
                converted = oriented.convert("RGBA" if alpha else "RGB")
                # Fresh pixels discard EXIF/GPS, comments, profiles, filenames, and trailing data.
                clean = Image.new(converted.mode, converted.size)
                clean.paste(converted)
                output = BytesIO()
                clean.save(
                    output, format="PNG" if alpha else "JPEG", **({} if alpha else {"quality": 85})
                )
                contents = output.getvalue()
                if len(contents) > settings.VEHICLE_IMAGE_MAX_BYTES:
                    raise ImageUploadTooLarge()
                return ContentFile(contents, name="normalized.png" if alpha else "normalized.jpg")
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        SyntaxError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise ValidationError(error) from exc
