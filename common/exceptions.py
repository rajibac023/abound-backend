import logging
from typing import Any

from rest_framework.response import Response
from rest_framework.views import exception_handler, set_rollback

from common.envelopes import ERROR_MESSAGE

logger = logging.getLogger(__name__)


def api_exception_handler(exc: Exception, context: dict[str, Any]) -> Response:
    response = exception_handler(exc, context)
    if response is not None:
        return response
    set_rollback()
    request = context.get("request")
    if request is not None and request.path.startswith("/api/v1/auth/"):
        # Authentication errors may embed credentials in exception text or source lines.
        logger.error("Unhandled authentication exception (%s)", type(exc).__name__)
    else:
        logger.error("Unhandled API exception", exc_info=(type(exc), exc, exc.__traceback__))
    return Response({"detail": ERROR_MESSAGE}, status=500)
