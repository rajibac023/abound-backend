import uuid

from django.core.validators import MinValueValidator
from django.db import models

from pricing.engine import MAX_RATE


class RentalDiscount(models.Model):
    class Kind(models.TextChoices):
        FIXED = "FIXED"
        PERCENTAGE = "PERCENTAGE"

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    label = models.CharField(max_length=100)
    vehicle = models.ForeignKey(
        "vehicles.Vehicle",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="rental_discounts",
    )
    kind = models.CharField(max_length=16, choices=Kind.choices)
    value = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    currency = models.CharField(max_length=3, default="NPR", choices=[("NPR", "Nepalese rupee")])
    minimum_days = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    starts_on = models.DateField(null=True, blank=True)
    ends_on = models.DateField(null=True, blank=True, help_text="Exclusive rental-period boundary.")
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["is_active", "vehicle"], name="pricing_discount_scope_idx")]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(kind__in=["FIXED", "PERCENTAGE"]),
                name="pricing_discount_kind_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(value__gte=0, value__lte=MAX_RATE),
                name="pricing_discount_value_valid",
            ),
            models.CheckConstraint(
                condition=~models.Q(kind="PERCENTAGE") | models.Q(value__lte=100),
                name="pricing_discount_percent_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(currency="NPR"), name="pricing_discount_currency_valid"
            ),
            models.CheckConstraint(
                condition=models.Q(minimum_days__gte=1), name="pricing_discount_days_valid"
            ),
            models.CheckConstraint(
                condition=models.Q(starts_on__isnull=True)
                | models.Q(ends_on__isnull=True)
                | models.Q(ends_on__gt=models.F("starts_on")),
                name="pricing_discount_dates_valid",
            ),
        ]
