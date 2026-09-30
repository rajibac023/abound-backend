from django.db import models


class MaintenanceType(models.TextChoices):
    ROUTINE_SERVICE = "ROUTINE_SERVICE"
    REPAIR = "REPAIR"
    TIRE_REPLACEMENT = "TIRE_REPLACEMENT"
    INSPECTION = "INSPECTION"
    CLEANING = "CLEANING"
    ACCIDENT_REPAIR = "ACCIDENT_REPAIR"
    OTHER = "OTHER"


class MaintenanceStatus(models.TextChoices):
    SCHEDULED = "SCHEDULED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


# Only these statuses make a vehicle unavailable for the maintenance period.
BLOCKING_STATUSES = (MaintenanceStatus.SCHEDULED, MaintenanceStatus.IN_PROGRESS)
