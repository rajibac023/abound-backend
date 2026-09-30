from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from django.db.models import (
    Count,
    DateField,
    DecimalField,
    DurationField,
    ExpressionWrapper,
    F,
    Q,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce, Greatest, Least

from accounts.models import User
from accounts.roles import Role
from bookings.choices import BLOCKING_STATUSES, BookingStatus
from bookings.models import Booking
from maintenance.choices import MaintenanceStatus
from maintenance.models import MaintenanceRecord
from vehicles.choices import VehicleStatus
from vehicles.models import Vehicle

# Bookings that represent money actually owed or collected, as opposed to a request
# that was never approved (PENDING) or never happened (CANCELLED/REJECTED).
REVENUE_STATUSES = (BookingStatus.CONFIRMED, BookingStatus.ACTIVE, BookingStatus.COMPLETED)
CANCELLATION_STATUSES = (BookingStatus.CANCELLED, BookingStatus.REJECTED)


def bookings_report(*, start_date: date, end_date: date) -> dict[str, Any]:
    """Booking volume by status, for bookings picked up within the range."""
    queryset = Booking.objects.filter(pickup_date__range=(start_date, end_date))
    by_status = {
        row["status"]: row["count"]
        for row in queryset.values("status").annotate(count=Count("id"))
    }
    return {
        "start_date": start_date,
        "end_date": end_date,
        "total_bookings": sum(by_status.values()),
        "by_status": by_status,
    }


def revenue_report(*, start_date: date, end_date: date) -> dict[str, Any]:
    """Booked revenue (never a live sum of all money ever quoted) by currency, for
    bookings picked up within the range that were actually confirmed onward."""
    queryset = Booking.objects.filter(
        pickup_date__range=(start_date, end_date), status__in=REVENUE_STATUSES
    )
    by_currency = list(
        queryset.values("currency")
        .annotate(total=Sum("total_amount"), booking_count=Count("id"))
        .order_by("currency")
    )
    # Match the rest of this API's convention (every money field a decimal-precise
    # string, e.g. BookingSerializer.total_amount) rather than a float from the raw
    # aggregate, which would silently lose precision on the way to JSON.
    for row in by_currency:
        row["total"] = str(row["total"])
    return {
        "start_date": start_date,
        "end_date": end_date,
        "by_currency": by_currency,
    }


def vehicle_utilization_report(*, start_date: date, end_date: date) -> dict[str, Any]:
    """For every active-fleet vehicle, the share of days in the range it was booked
    (PENDING/CONFIRMED/ACTIVE - the same statuses that block availability). A single
    annotated query against Vehicle, so idle vehicles show up at 0% rather than being
    silently absent, and no booking row is ever pulled into Python."""
    period_days = (end_date - start_date).days + 1
    exclusive_end = end_date + timedelta(days=1)
    clipped_days = ExpressionWrapper(
        Least(F("bookings__return_date"), Value(exclusive_end, output_field=DateField()))
        - Greatest(F("bookings__pickup_date"), Value(start_date, output_field=DateField())),
        output_field=DurationField(),
    )
    overlap = Q(
        bookings__status__in=BLOCKING_STATUSES,
        bookings__pickup_date__lte=end_date,
        bookings__return_date__gt=start_date,
    )
    vehicles = (
        Vehicle.objects.exclude(status=VehicleStatus.INACTIVE)
        .annotate(booked_days=Coalesce(Sum(clipped_days, filter=overlap), Value(timedelta(0))))
        .values("uuid", "vehicle_name", "registration_number", "booked_days")
        .order_by("-booked_days")
    )
    rows = []
    for row in vehicles:
        booked = row["booked_days"].days
        rows.append(
            {
                "vehicle_uuid": row["uuid"],
                "vehicle_name": row["vehicle_name"],
                "registration_number": row["registration_number"],
                "booked_days": booked,
                "utilization_pct": round(booked / period_days * 100, 1) if period_days else 0.0,
            }
        )
    return {"start_date": start_date, "end_date": end_date, "vehicles": rows}


def vehicle_availability_report() -> dict[str, Any]:
    """Current fleet status breakdown - a point-in-time snapshot, not date-ranged."""
    by_status = {
        row["status"]: row["count"]
        for row in Vehicle.objects.values("status").annotate(count=Count("id"))
    }
    return {"total_vehicles": sum(by_status.values()), "by_status": by_status}


def maintenance_report(*, start_date: date, end_date: date) -> dict[str, Any]:
    """Maintenance volume and cost by type, for work starting within the range."""
    queryset = MaintenanceRecord.objects.filter(start_date__range=(start_date, end_date))
    by_type = list(
        queryset.values("maintenance_type")
        .annotate(
            count=Count("id"),
            total_cost=Coalesce(
                Sum("cost"), Value(Decimal("0.00")), output_field=DecimalField()
            ),
        )
        .order_by("maintenance_type")
    )
    for row in by_type:
        row["total_cost"] = str(row["total_cost"])
    by_status = {
        row["status"]: row["count"]
        for row in queryset.values("status").annotate(count=Count("id"))
    }
    return {
        "start_date": start_date,
        "end_date": end_date,
        "by_type": by_type,
        "by_status": by_status,
        "scheduled_or_in_progress": queryset.filter(
            status__in=(MaintenanceStatus.SCHEDULED, MaintenanceStatus.IN_PROGRESS)
        ).count(),
    }


def customers_report(*, start_date: date, end_date: date) -> dict[str, Any]:
    """New signups in the range, plus the top customers by booking volume across the
    same range - bounded to 10 rows, never the full customer table."""
    new_customers = User.objects.filter(
        role=Role.CUSTOMER, created_at__date__range=(start_date, end_date)
    ).count()
    top_customers = list(
        Booking.objects.filter(pickup_date__range=(start_date, end_date))
        .values("customer__uuid", "customer__email")
        .annotate(
            booking_count=Count("id"),
            total_spent=Coalesce(
                Sum("total_amount"), Value(Decimal("0.00")), output_field=DecimalField()
            ),
        )
        .order_by("-booking_count")[:10]
    )
    for row in top_customers:
        row["total_spent"] = str(row["total_spent"])
    return {
        "start_date": start_date,
        "end_date": end_date,
        "new_customers": new_customers,
        "top_customers": top_customers,
    }


def cancellations_report(*, start_date: date, end_date: date) -> dict[str, Any]:
    """Cancellation/rejection volume and rate, for bookings picked up within the range."""
    queryset = Booking.objects.filter(pickup_date__range=(start_date, end_date))
    total = queryset.count()
    cancelled = queryset.filter(status__in=CANCELLATION_STATUSES).count()
    by_status = {
        row["status"]: row["count"]
        for row in queryset.filter(status__in=CANCELLATION_STATUSES)
        .values("status")
        .annotate(count=Count("id"))
    }
    return {
        "start_date": start_date,
        "end_date": end_date,
        "total_bookings": total,
        "cancelled_or_rejected": cancelled,
        "cancellation_rate_pct": round(cancelled / total * 100, 1) if total else 0.0,
        "by_status": by_status,
    }
