from unittest.mock import patch

from django.db import OperationalError, connection
from django.test import SimpleTestCase
from django.urls import reverse
from rest_framework.test import APITestCase

from common.envelopes import ERROR_MESSAGE, SUCCESS_MESSAGE
from common.selectors import database_is_available


class HealthTests(APITestCase):
    def test_readiness_uses_postgresql(self) -> None:
        self.assertEqual(connection.vendor, "postgresql")
        with self.assertNumQueries(1):
            response = self.client.get(reverse("v1:health"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "success": True,
                "data": {"status": "ok", "database": "ok"},
                "message": SUCCESS_MESSAGE,
            },
        )
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_database_round_trip(self) -> None:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database(), 1")
            database, value = cursor.fetchone()
        self.assertEqual(database, connection.settings_dict["NAME"])
        self.assertEqual(value, 1)

    def test_database_outage_returns_503_without_connection_details(self) -> None:
        with (
            patch(
                "common.selectors.connection.cursor", side_effect=OperationalError("secret-host")
            ),
            self.assertLogs("common.selectors", level="WARNING"),
        ):
            response = self.client.get(reverse("v1:health"))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"success": False, "message": ERROR_MESSAGE, "errors": {"database": ["Unavailable."]}},
        )
        self.assertNotIn("secret-host", response.content.decode())
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_unexpected_probe_result_is_unhealthy(self) -> None:
        with patch("common.selectors.connection.cursor") as cursor:
            cursor.return_value.__enter__.return_value.fetchone.return_value = (0,)
            self.assertFalse(database_is_available())


class LivenessTests(SimpleTestCase):
    def test_liveness_does_not_access_database(self) -> None:
        response = self.client.get(reverse("v1:liveness"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"], {"status": "ok"})

    def test_health_is_public_even_with_invalid_authorization_header(self) -> None:
        response = self.client.get(reverse("v1:liveness"), HTTP_AUTHORIZATION="Bearer invalid")
        self.assertEqual(response.status_code, 200)

    def test_only_v1_is_routed(self) -> None:
        response = self.client.get("/api/v2/health/")
        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json()["success"])

    def test_head_has_no_body(self) -> None:
        response = self.client.head(reverse("v1:liveness"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"")

    def test_unsupported_method_returns_json_405(self) -> None:
        response = self.client.post(reverse("v1:liveness"))
        self.assertEqual(response.status_code, 405)
        self.assertFalse(response.json()["success"])
        self.assertIn("GET", response["Allow"])
