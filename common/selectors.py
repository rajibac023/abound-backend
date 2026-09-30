"""Read-only infrastructure queries, independent of HTTP response formatting."""

import logging

from django.db import DatabaseError, connection

logger = logging.getLogger(__name__)


def database_is_available() -> bool:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            return cursor.fetchone() == (1,)
    except DatabaseError:
        logger.warning("Database readiness check failed")
        return False
