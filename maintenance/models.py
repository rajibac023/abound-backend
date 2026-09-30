import uuid
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

from maintenance.choices import MaintenanceStatus, MaintenanceType

MAX_COST = Decimal("9999999999.99")


class MaintenanceRecord(models.Model):
    Type = MaintenanceType
    Status = MaintenanceStatus

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    vehicle = models.ForeignKey(
        "vehicles.Vehicle", on_delete=models.PROTECT, related_name="maintenance_records"
    )
    maintenance_type = models.CharField(max_length=32, choices=MaintenanceType.choices)
    description = models.TextField(max_length=10000, blank=True)
    start_date = models.DateField()
    end_date = models.DateField(
        help_text="Inclusive; the vehicle returns to service the day after end_date."
    )
    odometer = models.PositiveIntegerField(null=True, blank=True)
    cost = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)]
    )
    service_provider = models.CharField(max_length=200, blank=True)
    status = models.CharField(
        max_length=16, choices=MaintenanceStatus.choices, default=MaintenanceStatus.SCHEDULED
    )
    notes = models.TextField(max_length=10000, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="maintenance_records_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-start_date", "-id"]
        indexes = [
            models.Index(
                fields=["vehicle", "status", "start_date", "end_date"],
                name="maintenance_vehicle_period_idx",
            ),
            # Serves fleet-wide date-range queries (reports, reminder queuing) that
            # don't filter by vehicle, so the composite index above isn't usable.
            models.Index(fields=["start_date"], name="maintenance_start_date_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=MaintenanceStatus.values),
                name="maintenance_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(maintenance_type__in=MaintenanceType.values),
                name="maintenance_type_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(end_date__gte=models.F("start_date")),
                name="maintenance_dates_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(odometer__isnull=True) | models.Q(odometer__gte=0),
                name="maintenance_odometer_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(cost__isnull=True)
                | (models.Q(cost__gte=0) & models.Q(cost__lte=MAX_COST)),
                name="maintenance_cost_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.vehicle_id} {self.maintenance_type} {self.start_date}"
