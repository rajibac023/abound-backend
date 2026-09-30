from typing import Any

from notifications.choices import NotificationEvent

# Plain-text templates, matching the existing password-reset email (no HTML templates
# are used anywhere else in this codebase). Each entry renders (subject, body) from the
# event's context dict; a missing key is a caller bug, so it fails loudly rather than
# silently sending a blank field.
_TEMPLATES: dict[str, tuple[str, str]] = {
    NotificationEvent.ACCOUNT_REGISTERED: (
        "Welcome to Abound Holidays",
        "Hi {first_name},\n\nYour account has been created. You can now browse the "
        "fleet and book a vehicle.",
    ),
    NotificationEvent.BOOKING_CREATED: (
        "Booking {booking_reference} received",
        "Hi {first_name},\n\nWe received your booking {booking_reference} for the "
        "{vehicle_name} from {pickup_date} to {return_date}. Total: {currency} "
        "{total_amount}.\n\nWe'll confirm it shortly.",
    ),
    NotificationEvent.BOOKING_CONFIRMED: (
        "Booking {booking_reference} confirmed",
        "Hi {first_name},\n\nYour booking {booking_reference} for the {vehicle_name} "
        "is confirmed for {pickup_date} to {return_date}.",
    ),
    NotificationEvent.BOOKING_REJECTED: (
        "Booking {booking_reference} could not be confirmed",
        "Hi {first_name},\n\nWe're sorry, but your booking {booking_reference} for the "
        "{vehicle_name} could not be confirmed.",
    ),
    NotificationEvent.BOOKING_CANCELLED: (
        "Booking {booking_reference} cancelled",
        "Hi {first_name},\n\nYour booking {booking_reference} for the {vehicle_name} "
        "has been cancelled.",
    ),
    NotificationEvent.BOOKING_ACTIVATED: (
        "Booking {booking_reference} is now active",
        "Hi {first_name},\n\nYour rental of the {vehicle_name} ({booking_reference}) "
        "has started. Enjoy your trip.",
    ),
    NotificationEvent.BOOKING_COMPLETED: (
        "Booking {booking_reference} completed",
        "Hi {first_name},\n\nYour rental of the {vehicle_name} ({booking_reference}) "
        "is complete. Thanks for booking with Abound Holidays.",
    ),
    NotificationEvent.BOOKING_REMINDER: (
        "Upcoming pickup: {vehicle_name}",
        "Hi {first_name},\n\nA reminder that your booking {booking_reference} for the "
        "{vehicle_name} begins on {pickup_date}.",
    ),
    NotificationEvent.MAINTENANCE_REMINDER: (
        "Upcoming maintenance: {vehicle_name}",
        "{maintenance_type} for {vehicle_name} ({registration_number}) is scheduled to "
        "start on {start_date}.",
    ),
}


def render_message(event: str, context: dict[str, Any]) -> tuple[str, str]:
    subject, body = _TEMPLATES[event]
    return subject.format(**context), body.format(**context)
