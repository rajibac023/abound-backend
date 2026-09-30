import re
from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from accounts.authorization import locked_actor
from accounts.models import User
from accounts.policy import Permission, has_permission
from accounts.roles import Role
from audit.services import record_booking_event
from availability.services import assert_period_available
from bookings.choices import TRANSITIONS, BookingStatus
from bookings.models import Booking
from bookings.policy import customer_can_cancel
from bookings.reference import next_booking_reference
from bookings.serializers import BookingInputSerializer
from notifications.choices import NotificationEvent
from notifications.services import notify
from pricing import services as pricing
from vehicles.choices import VehicleStatus
from vehicles.models import Vehicle

# Every status a transition can land on has exactly one corresponding customer-facing
# notification event; PENDING has none because it is only ever the initial state, never
# a transition target.
STATUS_NOTIFICATION_EVENTS: dict[str, str] = {
    BookingStatus.CONFIRMED: NotificationEvent.BOOKING_CONFIRMED,
    BookingStatus.REJECTED: NotificationEvent.BOOKING_REJECTED,
    BookingStatus.CANCELLED: NotificationEvent.BOOKING_CANCELLED,
    BookingStatus.ACTIVE: NotificationEvent.BOOKING_ACTIVATED,
    BookingStatus.COMPLETED: NotificationEvent.BOOKING_COMPLETED,
}


def _booking_notification_context(booking: Booking) -> dict[str, Any]:
    return {
        "first_name": booking.customer.first_name,
        "booking_reference": booking.booking_reference,
        "vehicle_name": booking.vehicle.vehicle_name,
        "pickup_date": str(booking.pickup_date),
        "return_date": str(booking.return_date),
        "total_amount": str(booking.total_amount),
        "currency": booking.currency,
    }


def idempotent_booking(
    *, customer: User, key: str | None, values: dict[str, Any]
) -> Booking | None:
    if key is None:
        return None
    if not isinstance(key, str) or re.fullmatch(r"[\x21-\x7e]{1,255}", key) is None:
        raise ValidationError(
            {"idempotency_key": ["Use 1 to 255 visible ASCII characters without spaces."]}
        )
    booking = (
        Booking.objects.select_related("vehicle")
        .filter(customer=customer, idempotency_key=key)
        .first()
    )
    if booking is None:
        return None
    if (
        booking.vehicle.uuid != values["vehicle"]
        or booking.pickup_date != values["pickup_date"]
        or booking.return_date != values["return_date"]
    ):
        raise ValidationError(
            {"idempotency_key": ["This key was already used for a different booking request."]}
        )
    return booking


@transaction.atomic
def create_booking(*, actor: User, data: Any, idempotency_key: str | None = None) -> Booking:
    """A priced PENDING booking. Confirmation and later transitions are separate actions."""
    current = locked_actor(actor)
    serializer = BookingInputSerializer(data=data)
    serializer.is_valid(raise_exception=True)
    values = serializer.validated_data
    if current.role == Role.CUSTOMER:
        if values.get("customer", current.uuid) != current.uuid:
            raise PermissionDenied()
        customer = current
    else:
        if not has_permission(current, Permission.BOOKINGS_CREATE):
            raise PermissionDenied()
        customer = User.objects.filter(
            uuid=values.get("customer"), role=Role.CUSTOMER, is_active=True
        ).first()
        if customer is None:
            raise ValidationError({"customer": ["Choose an active customer."]})
    # Permit a safe replay even after the vehicle has been retired or its rates changed.
    replay = idempotent_booking(customer=customer, key=idempotency_key, values=values)
    if replay is not None:
        return replay
    vehicle = (
        Vehicle.objects.select_for_update()
        .filter(uuid=values["vehicle"], status=VehicleStatus.AVAILABLE)
        .first()
    )
    if vehicle is None:
        raise NotFound()
    if idempotency_key:
        # Checked only after the vehicle lock: a concurrent identical request may still be
        # mid-flight, and blocking here on the same lock guarantees we see its committed result.
        replay = idempotent_booking(customer=customer, key=idempotency_key, values=values)
        if replay is not None:
            return replay
    if values["pickup_date"] < timezone.localdate():
        raise ValidationError({"pickup_date": ["New bookings cannot start in the past."]})
    # Never trust a client's availability check; the vehicle lock above serializes this.
    assert_period_available(
        vehicle=vehicle, pickup_date=values["pickup_date"], return_date=values["return_date"]
    )
    # Never accept a client quote, amount, discount, or unsaved vehicle rates.
    quote = pricing.price_vehicle(
        vehicle=vehicle, pickup_date=values["pickup_date"], return_date=values["return_date"]
    )
    reference = next_booking_reference()
    try:
        with transaction.atomic():
            booking = Booking.objects.create(
                booking_reference=reference,
                vehicle=vehicle,
                customer=customer,
                created_by=current,
                pickup_date=quote.pickup_date,
                return_date=quote.return_date,
                rental_days=quote.rental_days,
                daily_rate_snapshot=quote.rates.daily,
                weekly_rate_snapshot=quote.rates.weekly,
                monthly_rate_snapshot=quote.rates.monthly,
                subtotal=quote.subtotal,
                discount=quote.discount,
                total_amount=quote.total,
                currency=quote.rates.currency,
                pricing_version=quote.policy_version,
                pricing_breakdown=[line.as_dict() for line in quote.lines],
                discount_breakdown=quote.discount_breakdown(),
                priced_at=timezone.now(),
                customer_note=values.get("customer_note", ""),
                idempotency_key=idempotency_key,
            )
    except IntegrityError:
        if idempotency_key:
            replay = idempotent_booking(customer=customer, key=idempotency_key, values=values)
            if replay is not None:
                return replay
        raise
    record_booking_event(
        action="bookings.created",
        actor=current,
        subject_uuid=booking.uuid,
        fields=["vehicle", "customer", "pickup_date", "return_date", "price_snapshot"],
        status_before=None,
        status_after=booking.status,
    )
    notify(
        event=NotificationEvent.BOOKING_CREATED,
        recipient=customer,
        context=_booking_notification_context(booking),
        related_type="booking",
        related_uuid=booking.uuid,
    )
    return booking


@transaction.atomic
def transition_booking(
    *,
    actor: User,
    booking_uuid: UUID,
    target_status: str,
    customer_note: str | None = None,
    admin_note: str | None = None,
) -> Booking:
    current = locked_actor(actor)
    booking = Booking.objects.select_for_update().filter(uuid=booking_uuid).first()
    if booking is None:
        raise NotFound()
    if target_status not in TRANSITIONS.get(booking.status, frozenset()):
        raise ValidationError(
            {"status": [f"Cannot move a {booking.status} booking to {target_status}."]}
        )
    is_self_service_cancel = target_status == BookingStatus.CANCELLED and customer_can_cancel(
        booking=booking, actor=current
    )
    if not is_self_service_cancel:
        required = (
            Permission.BOOKINGS_CANCEL
            if target_status == BookingStatus.CANCELLED
            else Permission.BOOKINGS_UPDATE
        )
        if not has_permission(current, required):
            raise PermissionDenied()
    previous_status = booking.status
    booking.status = target_status
    update_fields = ["status", "updated_at"]
    if customer_note is not None:
        booking.customer_note = customer_note
        update_fields.append("customer_note")
    if admin_note is not None:
        booking.admin_note = admin_note
        update_fields.append("admin_note")
    booking.save(update_fields=update_fields)
    record_booking_event(
        action="bookings.status_changed",
        actor=current,
        subject_uuid=booking.uuid,
        fields=["status"],
        status_before=previous_status,
        status_after=booking.status,
    )
    event = STATUS_NOTIFICATION_EVENTS.get(target_status)
    if event:
        notify(
            event=event,
            recipient=booking.customer,
            context=_booking_notification_context(booking),
            related_type="booking",
            related_uuid=booking.uuid,
        )
    return booking


@transaction.atomic
def update_admin_note(*, actor: User, booking_uuid: UUID, admin_note: str) -> Booking:
    """Operational annotation only; never changes status or customer-visible fields."""
    current = locked_actor(actor, Permission.BOOKINGS_UPDATE)
    booking = Booking.objects.select_for_update().filter(uuid=booking_uuid).first()
    if booking is None:
        raise NotFound()
    if booking.admin_note != admin_note:
        booking.admin_note = admin_note
        booking.save(update_fields=["admin_note", "updated_at"])
        record_booking_event(
            action="bookings.note_updated",
            actor=current,
            subject_uuid=booking.uuid,
            fields=["admin_note"],
            status_before=booking.status,
            status_after=booking.status,
        )
    return booking
