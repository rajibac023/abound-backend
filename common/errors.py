"""JSON responses for errors raised outside DRF's request lifecycle."""

from django.http import HttpRequest, JsonResponse

from common.envelopes import ERROR_MESSAGE, error_payload


def bad_request(request: HttpRequest, exception: Exception) -> JsonResponse:
    return JsonResponse(error_payload("Invalid request."), status=400)


def permission_denied(request: HttpRequest, exception: Exception) -> JsonResponse:
    return JsonResponse(
        error_payload("You do not have permission to perform this action."), status=403
    )


def not_found(request: HttpRequest, exception: Exception) -> JsonResponse:
    return JsonResponse(error_payload("The requested resource was not found."), status=404)


def server_error(request: HttpRequest) -> JsonResponse:
    return JsonResponse(error_payload(ERROR_MESSAGE), status=500)


def csrf_failure(request: HttpRequest, reason: str = "") -> JsonResponse:
    return JsonResponse(error_payload("CSRF verification failed."), status=403)
