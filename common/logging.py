import json
import logging
import re
from datetime import UTC, datetime

from common.middleware import request_id_context


class RequestIDFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        request = getattr(record, "request", None)
        # Django can log a response after middleware has reset the context variable.
        record.request_id = (
            getattr(request, "META", {}).get("ABOUND_REQUEST_ID", "-")
            if request is not None
            else request_id_context.get()
        )
        return True


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        request = getattr(record, "request", None)
        auth_request = getattr(request, "path", "").startswith("/api/v1/auth/")
        message = record.getMessage()
        # Development access logs contain the request target; strip any auth query values.
        message = re.sub(r'(/api/v1/auth/[^?\s"]*)\?[^\s"]*', r"\1?[redacted]", message)
        if auth_request:
            message = f"Authentication response: HTTP {getattr(record, 'status_code', 'error')}"
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": message,
            "request_id": getattr(record, "request_id", "-"),
        }
        if record.exc_info and not auth_request:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)
