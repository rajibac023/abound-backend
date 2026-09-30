from typing import Any

from rest_framework.renderers import JSONRenderer

from common.envelopes import ERROR_MESSAGE, error_payload, success_payload


class EnvelopeJSONRenderer(JSONRenderer):
    """Apply the envelope once at the HTTP boundary, including DRF errors."""

    def render(
        self,
        data: Any,
        accepted_media_type: str | None = None,
        renderer_context: dict[str, Any] | None = None,
    ) -> bytes:
        context = renderer_context or {}
        response = context.get("response")
        status_code = response.status_code if response is not None else 200
        if status_code in {204, 304}:
            return b""
        if status_code >= 400:
            if isinstance(data, dict) and set(data) == {"detail"}:
                payload = error_payload(message=str(data["detail"]))
            else:
                errors = data if isinstance(data, dict) else {"detail": data}
                payload = error_payload(ERROR_MESSAGE, errors)
        else:
            payload = success_payload(data)
        return super().render(payload, accepted_media_type, renderer_context)
