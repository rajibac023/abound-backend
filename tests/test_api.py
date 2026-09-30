import json
from uuid import UUID

from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APITestCase

from accounts.services import login
from common.envelopes import ERROR_MESSAGE, SUCCESS_MESSAGE
from common.logging import JSONFormatter, RequestIDFilter
from common.middleware import request_id_context


@override_settings(ROOT_URLCONF="tests.urls")
class APIContractTests(APITestCase):
    def test_success_preserves_201_status(self) -> None:
        response = self.client.post("/api/v1/echo/", {"name": "Abound"})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            response.json(),
            {"success": True, "data": {"name": "Abound"}, "message": SUCCESS_MESSAGE},
        )

    def test_validation_error_preserves_field_errors(self) -> None:
        response = self.client.post("/api/v1/echo/", {})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(),
            {
                "success": False,
                "message": ERROR_MESSAGE,
                "errors": {"name": ["This field is required."]},
            },
        )

    def test_malformed_json_has_error_envelope(self) -> None:
        response = self.client.post("/api/v1/echo/", data="{", content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(set(response.json()), {"success", "message", "errors"})

    def test_unsupported_media_type_preserves_415(self) -> None:
        response = self.client.post("/api/v1/echo/", data="name=Abound", content_type="text/plain")
        self.assertEqual(response.status_code, 415)
        self.assertFalse(response.json()["success"])

    def test_unsupported_accept_header_preserves_406(self) -> None:
        response = self.client.get("/api/v1/echo/", HTTP_ACCEPT="text/html")
        self.assertEqual(response.status_code, 406)
        self.assertFalse(response.json()["success"])

    def test_default_permission_requires_authentication(self) -> None:
        response = self.client.get("/api/v1/private/")
        self.assertEqual(response.status_code, 401)
        self.assertFalse(response.json()["success"])
        self.assertIn("Bearer", response["WWW-Authenticate"])

    def test_jwt_authenticates_custom_user(self) -> None:
        get_user_model().objects.create_user(email="customer@example.com", password="test-pass")
        _, tokens = login(email="customer@example.com", password="test-pass", client="mobile")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        response = self.client.get("/api/v1/private/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"], {"email": "customer@example.com"})

    def test_customer_cannot_access_staff_route(self) -> None:
        get_user_model().objects.create_user(email="customer@example.com", password="test-pass")
        _, tokens = login(email="customer@example.com", password="test-pass", client="mobile")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        response = self.client.get("/api/v1/staff/")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.json()["success"])

    def test_inactive_user_cannot_use_existing_token(self) -> None:
        user = get_user_model().objects.create_user(
            email="customer@example.com", password="test-pass"
        )
        _, tokens = login(email="customer@example.com", password="test-pass", client="mobile")
        user.is_active = False
        user.save(update_fields=["is_active"])
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        self.assertEqual(self.client.get("/api/v1/private/").status_code, 401)

    def test_not_found_errors_inside_and_outside_drf(self) -> None:
        for path in ["/api/v1/missing/", "/api/v1/unknown/"]:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 404)
                self.assertFalse(response.json()["success"])
                self.assertEqual(response.json()["errors"], {})

    def test_unexpected_drf_error_is_sanitized(self) -> None:
        with self.assertLogs("common.exceptions", level="ERROR"):
            response = self.client.get("/api/v1/failure/")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            response.json(), {"success": False, "message": ERROR_MESSAGE, "errors": {}}
        )
        self.assertNotIn("private-database-password", response.content.decode())

    def test_framework_500_is_sanitized(self) -> None:
        self.client.raise_request_exception = False
        with self.assertLogs("django.request", level="ERROR"):
            response = self.client.get("/api/v1/django-failure/")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["message"], ERROR_MESSAGE)
        self.assertNotIn("private-framework-error", response.content.decode())

    def test_framework_403_is_sanitized(self) -> None:
        response = self.client.get("/api/v1/django-forbidden/")
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("private-permission-detail", response.content.decode())

    def test_retry_after_header_survives_error_formatting(self) -> None:
        response = self.client.get("/api/v1/limited/")
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Retry-After"], "30")
        self.assertFalse(response.json()["success"])

    def test_204_has_no_body(self) -> None:
        response = self.client.delete("/api/v1/echo/")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b"")

    def test_namespace_sets_request_version(self) -> None:
        response = self.client.get("/api/v1/echo/")
        self.assertEqual(response.json()["data"]["version"], "v1")

    def test_pagination_metadata_is_inside_data(self) -> None:
        response = self.client.get("/api/v1/collection/")
        data = response.json()["data"]
        self.assertEqual(data["count"], 150)
        self.assertEqual(len(data["results"]), 25)
        self.assertIsNotNone(data["next"])

    def test_page_size_is_bounded(self) -> None:
        response = self.client.get("/api/v1/collection/?page_size=10000")
        self.assertEqual(len(response.json()["data"]["results"]), 100)

    def test_request_ids_are_unique_and_context_is_cleared(self) -> None:
        first = self.client.get("/api/v1/echo/", HTTP_X_REQUEST_ID="untrusted-value")
        second = self.client.get("/api/v1/unknown/")
        UUID(first["X-Request-ID"])
        UUID(second["X-Request-ID"])
        self.assertNotEqual(first["X-Request-ID"], second["X-Request-ID"])
        self.assertEqual(request_id_context.get(), "-")

    def test_error_log_keeps_request_id_after_middleware_returns(self) -> None:
        with self.assertLogs("django.request", level="WARNING") as captured:
            response = self.client.get("/api/v1/unknown/")
        self.assertEqual(request_id_context.get(), "-")
        record = captured.records[0]
        RequestIDFilter().filter(record)
        payload = json.loads(JSONFormatter().format(record))
        self.assertEqual(payload["request_id"], response["X-Request-ID"])

    @override_settings(CORS_ALLOWED_ORIGINS=["https://admin.example.com"])
    def test_cors_allows_only_configured_origin(self) -> None:
        allowed = self.client.get("/api/v1/echo/", HTTP_ORIGIN="https://admin.example.com")
        denied = self.client.get("/api/v1/echo/", HTTP_ORIGIN="https://untrusted.example.com")
        self.assertEqual(allowed["Access-Control-Allow-Origin"], "https://admin.example.com")
        self.assertNotIn("Access-Control-Allow-Origin", denied)

    @override_settings(CORS_ALLOWED_ORIGINS=["https://admin.example.com"])
    def test_cors_preflight_allows_authorization_header(self) -> None:
        response = self.client.options(
            "/api/v1/echo/",
            HTTP_ORIGIN="https://admin.example.com",
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
            HTTP_ACCESS_CONTROL_REQUEST_HEADERS="authorization,content-type",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("authorization", response["Access-Control-Allow-Headers"].lower())
