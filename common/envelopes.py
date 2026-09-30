from typing import Any

SUCCESS_MESSAGE = "Operation completed successfully."
ERROR_MESSAGE = "Unable to complete the operation."


def success_payload(data: Any, message: str = SUCCESS_MESSAGE) -> dict[str, Any]:
    return {"success": True, "data": data, "message": message}


def error_payload(message: str = ERROR_MESSAGE, errors: Any = None) -> dict[str, Any]:
    return {"success": False, "message": message, "errors": {} if errors is None else errors}
