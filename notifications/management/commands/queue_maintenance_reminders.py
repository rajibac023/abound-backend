from datetime import timedelta
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from accounts.models import User
from accounts.roles import Role
from maintenance.choices import MaintenanceStatus
from maintenance.models import MaintenanceRecord
from notifications.choices import NotificationEvent
from notifications.services import notify

# Roles that can see the maintenance schedule; kept in sync with MAINTENANCE_VIEW in
# accounts.policy.ROLE_PERMISSIONS (staff, admin and super-admin all hold it).
ADMIN_ROLES = (Role.STAFF, Role.ADMIN, Role.SUPER_ADMIN)


class Command(BaseCommand):
    help = "Queue a reminder to every administrator for maintenance scheduled to start in N days."

    def handle(self, *args: Any, **options: Any) -> None:
        target_date = timezone.localdate() + timedelta(
            days=settings.MAINTENANCE_REMINDER_DAYS_BEFORE
        )
        records = MaintenanceRecord.objects.select_related("vehicle").filter(
            status=MaintenanceStatus.SCHEDULED, start_date=target_date
        )
        recipients = list(User.objects.filter(role__in=ADMIN_ROLES, is_active=True))
        queued = 0
        for record in records:
            for recipient in recipients:
                with transaction.atomic():
                    notify(
                        event=NotificationEvent.MAINTENANCE_REMINDER,
                        recipient=recipient,
                        context={
                            "vehicle_name": record.vehicle.vehicle_name,
                            "registration_number": record.vehicle.registration_number,
                            "maintenance_type": record.get_maintenance_type_display(),
                            "start_date": str(record.start_date),
                        },
                        related_type="maintenance_record",
                        related_uuid=record.uuid,
                        occurrence=str(target_date),
                    )
                queued += 1
        self.stdout.write(f"Queued {queued} maintenance reminder(s) for {target_date}.")
