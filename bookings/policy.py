from accounts.models import User
from accounts.policy import is_active_user
from accounts.roles import Role
from bookings.choices import TRANSITIONS, BookingStatus
from bookings.models import Booking


def customer_can_cancel(*, booking: Booking, actor: User | None) -> bool:
    """The same self-service policy drives the API capability and the mutation."""
    return bool(
        is_active_user(actor)
        and actor is not None
        and actor.role == Role.CUSTOMER
        and booking.customer_id == actor.pk
        and BookingStatus.CANCELLED in TRANSITIONS.get(booking.status, frozenset())
    )
