import math
from datetime import UTC, datetime

from django.conf import settings
from django.db import connection
from django.utils import timezone
from django.utils.crypto import salted_hmac
from rest_framework.exceptions import Throttled
from rest_framework.request import Request
from rest_framework.throttling import BaseThrottle
from rest_framework.views import APIView

from accounts.models import AuthRateLimit


def consume_limit(scope: str, identity: str) -> None:
    """One PostgreSQL upsert enforces a fixed-window limit across all workers."""
    maximum, seconds = settings.AUTH_RATE_LIMITS[scope]
    now = timezone.now().timestamp()
    window = int(now // seconds)
    expires = datetime.fromtimestamp((window + 1) * seconds, UTC)
    key = salted_hmac(
        "accounts.rate_limit", f"{scope}:{window}:{identity}", algorithm="sha256"
    ).hexdigest()
    table = connection.ops.quote_name(AuthRateLimit._meta.db_table)
    with connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO {table} (key, count, expires_at) VALUES (%s, 1, %s) "
            f"ON CONFLICT (key) DO UPDATE SET count = LEAST({table}.count + 1, %s) "
            "RETURNING count",
            [key, expires, maximum + 1],
        )
        count = cursor.fetchone()[0]
    if count > maximum:
        raise Throttled(wait=max(1, math.ceil(expires.timestamp() - now)))


class AuthIPThrottle(BaseThrottle):
    def allow_request(self, request: Request, view: APIView) -> bool:
        # Trust the server peer address only, never a client-supplied forwarded header.
        consume_limit(f"{view.throttle_scope}_ip", request.META.get("REMOTE_ADDR", "unknown"))
        return True
