import uuid

from django.conf import settings
from django.db import models


class AvailabilityBlock(models.Model):
    """An explicit admin-defined block, independent of bookings and maintenance."""

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    vehicle = models.ForeignKey(
        "vehicles.Vehicle", on_delete=models.PROTECT, related_name="availability_blocks"
    )
    reason = models.CharField(max_length=200)
    start_date = models.DateField()
    end_date = models.DateField(
        help_text="Inclusive; the vehicle returns to service the day after end_date."
    )
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="availability_blocks_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-start_date", "-id"]
        indexes = [
            models.Index(
                fields=["vehicle", "is_active", "start_date", "end_date"],
                name="availability_block_period_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_date__gte=models.F("start_date")),
                name="availability_block_dates_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.vehicle_id} {self.start_date}-{self.end_date}"
