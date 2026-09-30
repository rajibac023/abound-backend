import uuid
from decimal import Decimal
from typing import Any

from django.core.validators import MinValueValidator, RegexValidator
from django.db import models
from django.db.models.functions import Lower, Trim, Upper
from django.utils import timezone

from vehicles.choices import (
    DriveType,
    FuelType,
    MileageUnit,
    Transmission,
    VehicleDocumentType,
    VehicleImageType,
    VehicleStatus,
    VehicleType,
)
from vehicles.validators import (
    MIN_MANUFACTURING_YEAR,
    normalize_identifier,
    validate_manufacturing_year,
)

RATE_FIELDS = (
    "daily_rate",
    "weekly_rate",
    "monthly_rate",
    "security_deposit",
    "extra_kilometer_rate",
    "driver_daily_rate",
)
MAX_RATE = Decimal("9999999999.99")  # Decimal(12, 2) storage precision, not a rental price.
MAX_TECHNICAL_VALUE = Decimal("99999999.99")


class Vehicle(models.Model):
    Status = VehicleStatus
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    features = models.ManyToManyField("VehicleFeature", related_name="vehicles", blank=True)
    vehicle_name = models.CharField(max_length=160)
    brand = models.CharField(max_length=100)
    model = models.CharField(max_length=100)
    variant = models.CharField(max_length=100, blank=True)
    vehicle_type = models.CharField(max_length=16, choices=VehicleType.choices, db_index=True)
    manufacturing_year = models.PositiveSmallIntegerField(validators=[validate_manufacturing_year])
    registration_number = models.CharField(max_length=64, unique=True)
    # A chassis number need not be a modern 17-character VIN. NULL means not recorded.
    vin = models.CharField(
        max_length=64,
        unique=True,
        blank=True,
        null=True,
        validators=[
            RegexValidator(r"^[A-Z0-9]+$", "Use letters and digits for VIN/chassis number.")
        ],
    )
    color = models.CharField(max_length=64, blank=True)
    short_description = models.CharField(max_length=280, blank=True)
    description = models.TextField(max_length=10000, blank=True)
    internal_notes = models.TextField(max_length=10000, blank=True)
    featured = models.BooleanField(default=False, db_index=True)
    status = models.CharField(
        max_length=16, choices=VehicleStatus.choices, default=VehicleStatus.INACTIVE, db_index=True
    )

    engine_type = models.CharField(max_length=100, blank=True)
    engine_capacity_cc = models.PositiveIntegerField(null=True, blank=True)
    engine_power = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        help_text="Kilowatts (kW).",
    )
    torque = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        help_text="Newton-metres (Nm).",
    )
    fuel_type = models.CharField(max_length=16, choices=FuelType.choices, blank=True)
    transmission = models.CharField(max_length=16, choices=Transmission.choices, blank=True)
    drive_type = models.CharField(max_length=8, choices=DriveType.choices, blank=True)
    number_of_gears = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1)]
    )
    mileage = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)]
    )
    mileage_unit = models.CharField(max_length=16, choices=MileageUnit.choices, blank=True)

    seat_count = models.PositiveSmallIntegerField(validators=[MinValueValidator(1)])
    passenger_capacity = models.PositiveSmallIntegerField(validators=[MinValueValidator(1)])
    door_count = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1)]
    )
    luggage_capacity = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="Number of standard bags."
    )
    boot_capacity = models.PositiveIntegerField(null=True, blank=True, help_text="Litres.")

    currency = models.CharField(max_length=3, default="NPR", choices=[("NPR", "Nepalese rupee")])
    daily_rate = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(0)]
    )
    weekly_rate = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)]
    )
    monthly_rate = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)]
    )
    security_deposit = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)]
    )
    included_kilometers = models.PositiveIntegerField(
        null=True, blank=True, help_text="Included kilometres per rental day, when configured."
    )
    extra_kilometer_rate = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)]
    )
    driver_available = models.BooleanField(default=False)
    driver_daily_rate = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)]
    )
    fuel_included = models.BooleanField(default=False)
    insurance_included = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["status", "-created_at", "-id"], name="vehicle_catalog_order_idx"),
            models.Index(fields=["status", "daily_rate", "id"], name="vehicle_catalog_rate_idx"),
        ]
        constraints = (
            [
                models.CheckConstraint(
                    condition=models.Q(status__in=VehicleStatus.values), name="vehicle_status_valid"
                ),
                models.CheckConstraint(
                    condition=models.Q(vehicle_type__in=VehicleType.values),
                    name="vehicle_type_valid",
                ),
                models.CheckConstraint(
                    condition=models.Q(currency="NPR"), name="vehicle_currency_valid"
                ),
                models.CheckConstraint(
                    condition=models.Q(registration_number=Upper("registration_number"))
                    & ~models.Q(registration_number__regex=r"[\s-]")
                    & ~models.Q(registration_number=""),
                    name="vehicle_registration_canonical",
                ),
                models.CheckConstraint(
                    condition=models.Q(vin__isnull=True) | models.Q(vin__regex=r"^[A-Z0-9]+$"),
                    name="vehicle_vin_canonical",
                ),
                models.CheckConstraint(
                    condition=models.Q(manufacturing_year__gte=MIN_MANUFACTURING_YEAR),
                    name="vehicle_manufacturing_year_min",
                ),
                models.CheckConstraint(
                    condition=models.Q(seat_count__gte=1)
                    & models.Q(passenger_capacity__gte=1)
                    & models.Q(passenger_capacity__lte=models.F("seat_count")),
                    name="vehicle_capacity_valid",
                ),
                models.CheckConstraint(
                    condition=models.Q(door_count__isnull=True) | models.Q(door_count__gte=1),
                    name="vehicle_doors_positive",
                ),
                models.CheckConstraint(
                    condition=models.Q(number_of_gears__isnull=True)
                    | models.Q(number_of_gears__gte=1),
                    name="vehicle_gears_positive",
                ),
                models.CheckConstraint(
                    condition=models.Q(mileage__isnull=True) | ~models.Q(mileage_unit=""),
                    name="vehicle_mileage_has_unit",
                ),
            ]
            + [
                models.CheckConstraint(
                    condition=models.Q(**{f"{field}__isnull": True})
                    | (models.Q(**{f"{field}__gte": 0}) & models.Q(**{f"{field}__lte": MAX_RATE})),
                    name=f"vehicle_{field}_valid",
                )
                for field in RATE_FIELDS
            ]
            + [
                models.CheckConstraint(
                    condition=models.Q(**{f"{field}__isnull": True})
                    | (
                        models.Q(**{f"{field}__gte": 0})
                        & models.Q(**{f"{field}__lte": MAX_TECHNICAL_VALUE})
                    ),
                    name=f"vehicle_{field}_valid",
                )
                for field in ("engine_power", "torque", "mileage")
            ]
            + [
                models.CheckConstraint(
                    condition=models.Q(**{f"{field}__in": ["", *values]}),
                    name=f"vehicle_{field}_valid",
                )
                for field, values in (
                    ("fuel_type", FuelType.values),
                    ("transmission", Transmission.values),
                    ("drive_type", DriveType.values),
                    ("mileage_unit", MileageUnit.values),
                )
            ]
        )

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.registration_number = normalize_identifier(self.registration_number)
        self.vin = normalize_identifier(self.vin) if self.vin else None
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.vehicle_name


class VehicleFeature(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=500, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint(Lower("name"), name="vehicle_feature_name_unique"),
            models.CheckConstraint(
                condition=models.Q(name=Trim("name")) & ~models.Q(name=""),
                name="vehicle_feature_name_valid",
            ),
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.name = " ".join(self.name.split())
        super().save(*args, **kwargs)


def vehicle_image_path(instance: "VehicleImage", filename: str) -> str:
    extension = filename.rsplit(".", 1)[-1]
    if extension not in {"jpg", "png"}:
        raise ValueError("Only normalized image files may be stored.")
    return f"vehicle-images/{instance.vehicle.uuid}/{uuid.uuid4().hex}.{extension}"


class VehicleImage(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    vehicle = models.ForeignKey(Vehicle, related_name="images", on_delete=models.CASCADE)
    image = models.ImageField(upload_to=vehicle_image_path, max_length=255, unique=True)
    image_type = models.CharField(
        max_length=16, choices=VehicleImageType.choices, default=VehicleImageType.OTHER
    )
    alt_text = models.CharField(max_length=255, blank=True)
    is_primary = models.BooleanField(default=False)
    display_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["display_order", "id"]
        indexes = [
            models.Index(fields=["vehicle", "display_order", "id"], name="vehicle_image_order_idx")
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["vehicle"],
                condition=models.Q(is_primary=True),
                name="vehicle_one_primary_image",
            ),
            models.CheckConstraint(
                condition=models.Q(image_type__in=VehicleImageType.values),
                name="vehicle_image_type_valid",
            ),
            models.CheckConstraint(
                condition=~models.Q(image=""), name="vehicle_image_file_required"
            ),
        ]


class ImageFileDeletion(models.Model):
    """Retry physical file removal after the image-row deletion has committed."""

    name = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    available_at = models.DateTimeField(default=timezone.now, db_index=True)
    attempts = models.PositiveIntegerField(default=0)


def vehicle_document_path(instance: "VehicleDocument", filename: str) -> str:
    extension = filename.rsplit(".", 1)[-1]
    if extension not in {"pdf", "jpg", "png"}:
        raise ValueError("Only normalized document files may be stored.")
    return f"vehicle-documents/{instance.vehicle.uuid}/{uuid.uuid4().hex}.{extension}"


class VehicleDocument(models.Model):
    """Private, admin-only compliance records. Never exposed through a customer API."""

    Type = VehicleDocumentType

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    vehicle = models.ForeignKey(Vehicle, related_name="documents", on_delete=models.CASCADE)
    document_type = models.CharField(
        max_length=16, choices=VehicleDocumentType.choices, default=VehicleDocumentType.OTHER
    )
    file = models.FileField(upload_to=vehicle_document_path, max_length=255, unique=True)
    document_number = models.CharField(max_length=100, blank=True)
    issue_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    notes = models.TextField(max_length=10000, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(
                fields=["vehicle", "document_type"], name="vehicle_document_type_idx"
            )
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(document_type__in=VehicleDocumentType.values),
                name="vehicle_document_type_valid",
            ),
            models.CheckConstraint(
                condition=~models.Q(file=""), name="vehicle_document_file_required"
            ),
            models.CheckConstraint(
                condition=models.Q(issue_date__isnull=True)
                | models.Q(expiry_date__isnull=True)
                | models.Q(expiry_date__gte=models.F("issue_date")),
                name="vehicle_document_dates_valid",
            ),
        ]


class DocumentFileDeletion(models.Model):
    """Retry physical file removal after the document-row deletion has committed."""

    name = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    available_at = models.DateTimeField(default=timezone.now, db_index=True)
    attempts = models.PositiveIntegerField(default=0)
