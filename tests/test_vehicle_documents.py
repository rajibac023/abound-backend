from io import BytesIO, StringIO
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone
from PIL import Image
from rest_framework.test import APITestCase

from accounts.models import User
from accounts.roles import Role
from audit.models import AuditEvent
from tests.test_vehicles import ADMIN, PASSWORD, PUBLIC, vehicle_data
from vehicles import document_services
from vehicles.models import DocumentFileDeletion, Vehicle, VehicleDocument


def pdf_file(size: int = 256, name: str = "private-registration.pdf") -> SimpleUploadedFile:
    body = b"%PDF-1.4\n" + b"0" * max(0, size - 9)
    return SimpleUploadedFile(name, body, "application/pdf")


def photo_file(name: str = "private-insurance.png") -> SimpleUploadedFile:
    output = BytesIO()
    Image.new("RGB", (16, 16), "blue").save(output, format="PNG")
    return SimpleUploadedFile(name, output.getvalue(), "image/png")


class VehicleDocumentTests(APITestCase):
    def setUp(self) -> None:
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        settings = override_settings(MEDIA_ROOT=directory.name)
        settings.enable()
        self.addCleanup(settings.disable)
        self.users = {
            role: User.objects.create_user(
                email=f"{role}@example.com", password=PASSWORD, role=role
            )
            for role in Role.values
        }
        self.client.force_authenticate(self.users[Role.ADMIN])
        self.vehicle = Vehicle.objects.create(**vehicle_data())
        self.documents = f"{ADMIN}{self.vehicle.uuid}/documents/"
        self.public_detail = f"{PUBLIC}{self.vehicle.uuid}/"

    def upload(self, file=None, **metadata):
        response = self.client.post(
            self.documents, {"file": file or pdf_file(), **metadata}, format="multipart"
        )
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()["data"]

    def document_url(self, data):
        return self.documents + data["uuid"] + "/"

    def test_upload_registration_document_with_metadata(self) -> None:
        data = self.upload(
            document_type="REGISTRATION",
            document_number="BA-1-234-5678",
            issue_date="2024-01-15",
            expiry_date="2029-01-14",
            notes="Renewed at the district office",
        )
        self.assertEqual(data["document_type"], "REGISTRATION")
        self.assertEqual(data["document_number"], "BA-1-234-5678")
        document = VehicleDocument.objects.get(uuid=data["uuid"])
        self.assertTrue(document.file.storage.exists(document.file.name))
        self.assertNotIn("private-registration", document.file.name)
        self.assertTrue(data["file"].startswith(ADMIN))
        event = AuditEvent.objects.get(action="vehicles.document_uploaded")
        self.assertNotIn("private-registration", str(event.changes))

    def test_image_document_is_accepted_and_re_encoded(self) -> None:
        data = self.upload(file=photo_file(), document_type="INSURANCE")
        document = VehicleDocument.objects.get(uuid=data["uuid"])
        self.assertTrue(document.file.name.endswith((".png", ".jpg")))

    def test_documents_never_appear_in_customer_apis(self) -> None:
        self.upload(document_type="TAX")
        detail = self.client.get(self.public_detail).json()["data"]
        self.assertNotIn("documents", detail)
        self.assertNotIn("document_number", str(detail))
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(self.documents).status_code, 401)

    def test_invalid_file_type_and_size_are_rejected(self) -> None:
        garbage = SimpleUploadedFile("note.txt", b"not a real document", "text/plain")
        response = self.client.post(self.documents, {"file": garbage}, format="multipart")
        self.assertEqual(response.status_code, 400, response.content)
        with override_settings(VEHICLE_DOCUMENT_MAX_BYTES=100):
            response = self.client.post(
                self.documents, {"file": pdf_file(size=500)}, format="multipart"
            )
            self.assertEqual(response.status_code, 413)

    def test_expiry_before_issue_is_rejected(self) -> None:
        response = self.client.post(
            self.documents,
            {"file": pdf_file(), "issue_date": "2028-01-01", "expiry_date": "2027-01-01"},
            format="multipart",
        )
        self.assertEqual(response.status_code, 400)

    def test_unknown_document_type_is_rejected(self) -> None:
        response = self.client.post(
            self.documents, {"file": pdf_file(), "document_type": "UNKNOWN"}, format="multipart"
        )
        self.assertEqual(response.status_code, 400)

    def test_update_metadata_and_delete_queues_file_cleanup(self) -> None:
        data = self.upload(document_type="EMISSION")
        saved = VehicleDocument.objects.get(uuid=data["uuid"])
        response = self.client.patch(self.document_url(data), {"document_number": "EMS-2024-01"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["data"]["document_number"], "EMS-2024-01")
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.delete(self.document_url(data))
            self.assertEqual(response.status_code, 200)
        self.assertFalse(VehicleDocument.objects.filter(pk=saved.pk).exists())
        self.assertFalse(saved.file.storage.exists(saved.file.name))
        self.assertFalse(DocumentFileDeletion.objects.exists())

    def test_storage_failure_on_delete_retains_retry_job(self) -> None:
        data = self.upload()
        saved = VehicleDocument.objects.get(uuid=data["uuid"])
        storage = saved.file.storage
        with (
            patch.object(storage, "delete", side_effect=OSError("storage unavailable")),
            self.captureOnCommitCallbacks(execute=True),
        ):
            self.assertEqual(self.client.delete(self.document_url(data)).status_code, 200)
        job = DocumentFileDeletion.objects.get()
        self.assertEqual(job.attempts, 1)
        self.assertTrue(storage.exists(saved.file.name))
        DocumentFileDeletion.objects.update(available_at=timezone.now())
        call_command("cleanup_vehicle_documents", stdout=StringIO())
        self.assertFalse(DocumentFileDeletion.objects.exists())
        self.assertFalse(storage.exists(saved.file.name))

    def test_upload_rollback_leaves_no_row_or_file(self) -> None:
        with (
            patch(
                "vehicles.document_services.record_document_event",
                side_effect=RuntimeError("audit failed"),
            ),
            self.assertRaises(RuntimeError),
        ):
            document_services.upload_document(
                actor=self.users[Role.ADMIN],
                vehicle_uuid=self.vehicle.uuid,
                data={"file": pdf_file()},
            )
        self.assertFalse(VehicleDocument.objects.exists())

    def test_staff_can_view_but_not_upload_or_delete(self) -> None:
        data = self.upload(document_type="OTHER")
        self.client.force_authenticate(self.users[Role.STAFF])
        self.assertEqual(self.client.get(self.documents).status_code, 200)
        self.assertEqual(
            self.client.post(self.documents, {"file": pdf_file()}, format="multipart").status_code,
            403,
        )
        self.assertEqual(self.client.delete(self.document_url(data)).status_code, 403)

    def test_customer_is_denied(self) -> None:
        self.client.force_authenticate(self.users[Role.CUSTOMER])
        self.assertEqual(self.client.get(self.documents).status_code, 403)

    def test_file_is_served_only_through_admin_route_with_safe_headers(self) -> None:
        data = self.upload()
        response = self.client.get(data["file"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertIn("sandbox", response["Content-Security-Policy"])
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(data["file"]).status_code, 401)
