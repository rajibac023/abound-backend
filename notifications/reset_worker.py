"""Password reset queue polling shared by local runserver and the dedicated worker."""

import logging
from threading import Event

from django.db import close_old_connections, connections

from notifications.services import deliver_one_password_reset

logger = logging.getLogger(__name__)


def run_reset_worker(stop: Event, *, interval: float = 2, limit: int = 100) -> None:
    try:
        while not stop.is_set():
            try:
                close_old_connections()
                for _ in range(limit):
                    if stop.is_set() or not deliver_one_password_reset():
                        break
            except Exception:
                # Database and transport errors can contain credentials. Keep logs generic.
                logger.warning("Password reset worker unavailable; will retry")
            finally:
                connections.close_all()
            stop.wait(interval)
    finally:
        connections.close_all()
