from collections.abc import Callable
from contextvars import ContextVar
from uuid import uuid4

from django.http import HttpRequest, HttpResponse

request_id_context: ContextVar[str] = ContextVar("request_id", default="-")
# Server peer address only, never a client-supplied X-Forwarded-For - same trust
# boundary as accounts.throttles.AuthIPThrottle, reused here for audit records.
client_ip_context: ContextVar[str] = ContextVar("client_ip", default="")
user_agent_context: ContextVar[str] = ContextVar("user_agent", default="")


class RequestIDMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        # Generate IDs locally rather than trusting arbitrary client-supplied log values.
        request_id = str(uuid4())
        request.META["ABOUND_REQUEST_ID"] = request_id
        token = request_id_context.set(request_id)
        ip_token = client_ip_context.set(request.META.get("REMOTE_ADDR", ""))
        agent_token = user_agent_context.set(request.META.get("HTTP_USER_AGENT", "")[:255])
        try:
            response = self.get_response(request)
            response["X-Request-ID"] = request_id
            if request.path.startswith(
                (
                    "/api/v1/auth/", "/api/v1/admin/", "/api/v1/vehicles/", "/api/v1/pricing/",
                    "/api/v1/bookings/", "/api/v1/availability/",
                )
            ):
                response["Cache-Control"] = "no-store"
                response["Pragma"] = "no-cache"
            return response
        finally:
            request_id_context.reset(token)
            client_ip_context.reset(ip_token)
            user_agent_context.reset(agent_token)
