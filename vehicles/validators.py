import re

from django.core.exceptions import ValidationError
from django.utils import timezone

MIN_MANUFACTURING_YEAR = 1886


def normalize_identifier(value: str) -> str:
    """Store one plate/chassis identity despite case, whitespace, or hyphen formatting."""
    return re.sub(r"[\s-]+", "", value).upper()


def validate_manufacturing_year(value: int) -> None:
    if not MIN_MANUFACTURING_YEAR <= value <= timezone.localdate().year:
        raise ValidationError("Enter a manufacturing year between 1886 and the current year.")
