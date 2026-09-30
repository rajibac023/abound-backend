import uuid

from django.conf import settings
from django.db import models

from bookings.choices import BookingStatus
from pricing.engine import MAX_AMOUNT, MAX_RATE


class Booking(models.Model):
    Status = BookingStatus

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    booking_reference = models.CharField(max_length=20, unique=True, editable=False)
    vehicle = models.ForeignKey(
        "vehicles.Vehicle", on_delete=models.PROTECT, related_name="bookings"
    )
    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="rental_bookings"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_bookings"
    )
    status = models.CharField(
        max_length=16, choices=BookingStatus.choices, default=BookingStatus.PENDING
    )
    pickup_date = models.DateField()
    return_date = models.DateField()
    rental_days = models.PositiveIntegerField()
    daily_rate_snapshot = models.DecimalField(max_digits=12, decimal_places=2)
    weekly_rate_snapshot = models.DecimalField(max_digits=12, decimal_places=2, null=True)
    monthly_rate_snapshot = models.DecimalField(max_digits=12, decimal_places=2, null=True)
    subtotal = models.DecimalField(max_digits=18, decimal_places=2)
    discount = models.DecimalField(max_digits=18, decimal_places=2)
    total_amount = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.CharField(max_length=3)
    pricing_version = models.CharField(max_length=64)
    pricing_breakdown = models.JSONField()
    discount_breakdown = models.JSONField()
    priced_at = models.DateTimeField()
    customer_note = models.TextField(max_length=2000, blank=True)
    admin_note = models.TextField(max_length=2000, blank=True)
    # A client-supplied replay key; NULL for callers that don't opt in to idempotent retries.
    idempotency_key = models.CharField(max_length=255, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(
                fields=["vehicle", "pickup_date", "return_date"], name="booking_vehicle_dates_idx"
            ),
            models.Index(fields=["customer", "-created_at"], name="booking_customer_created_idx"),
            models.Index(fields=["status"], name="booking_status_idx"),
            # Serves the reporting endpoints (bookings/revenue/cancellations), which
            # always filter by pickup_date range plus status, with no vehicle or
            # customer in the query.
            models.Index(fields=["pickup_date", "status"], name="booking_pickup_status_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=BookingStatus.values), name="booking_status_valid"
            ),
            models.CheckConstraint(
                condition=models.Q(booking_reference__regex=r"^ABH-\d{4}-\d{6}$"),
                name="booking_reference_format_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(return_date__gt=models.F("pickup_date")),
                name="booking_dates_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(rental_days__gte=1), name="booking_days_valid"
            ),
            models.CheckConstraint(
                condition=models.Q(currency="NPR"), name="booking_currency_valid"
            ),
            models.CheckConstraint(
                condition=models.Q(discount__lte=models.F("subtotal")),
                name="booking_discount_bounded",
            ),
            models.CheckConstraint(
                condition=models.Q(total_amount=models.F("subtotal") - models.F("discount")),
                name="booking_total_balanced",
            ),
            models.UniqueConstraint(
                fields=["customer", "idempotency_key"],
                condition=models.Q(idempotency_key__isnull=False),
                name="booking_idempotency_unique",
            ),
            *[
                models.CheckConstraint(
                    condition=models.Q(**{f"{field}__gte": 0, f"{field}__lte": MAX_AMOUNT}),
                    name=f"booking_{field}_valid",
                )
                for field in ("subtotal", "discount", "total_amount")
            ],
            *[
                models.CheckConstraint(
                    condition=models.Q(**{f"{field}__isnull": True})
                    | models.Q(**{f"{field}__gte": 0, f"{field}__lte": MAX_RATE}),
                    name=f"booking_{field}_valid",
                )
                for field in (
                    "daily_rate_snapshot",
                    "weekly_rate_snapshot",
                    "monthly_rate_snapshot",
                )
            ],
        ]

    def __str__(self) -> str:
        return self.booking_reference


class BookingSequence(models.Model):
    """One row per calendar year; its row lock serializes reference-number issuance."""

    year = models.PositiveIntegerField(unique=True)
    last_value = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(last_value__gte=0), name="booking_sequence_value_valid"
            )
        ]
