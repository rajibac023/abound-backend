from concurrent.futures import ThreadPoolExecutor
from io import BytesIO, StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.test import TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from PIL import Image
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APITestCase

from accounts.models import User
from accounts.roles import Role
from audit.models import AuditEvent
from tests.test_vehicles import ADMIN, PASSWORD, PUBLIC, vehicle_data
from vehicles import media_services
from vehicles.models import ImageFileDeletion, Vehicle, VehicleFeature, VehicleImage

FEATURES = "/api/v1/admin/vehicle-features/"


def photo(*, format="PNG", mode="RGB", size=(32, 20), **options):
    output = BytesIO()
    Image.new(mode, size, "red").save(output, format=format, **options)
    return SimpleUploadedFile("private-original-name.png", output.getvalue(), "image/png")


class VehicleMediaTests(APITestCase):
    def setUp(self) -> None:
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.media_root = Path(directory.name)
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
        self.images = f"{ADMIN}{self.vehicle.uuid}/images/"
        self.assignments = f"{ADMIN}{self.vehicle.uuid}/features/"
        self.public = f"{PUBLIC}{self.vehicle.uuid}/"

    def upload(self, **metadata):
        response = self.client.post(self.images, {"image": photo(), **metadata}, format="multipart")
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()["data"]

    def image_url(self, data):
        return self.images + data["uuid"] + "/"

    def detail(self):
        response = self.client.get(self.public)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["data"]

    def test_upload_and_customer_presentation(self) -> None:
        data = self.upload(image_type="MAIN", alt_text="Front of the SUV")
        self.assertTrue(data["is_primary"])
        self.assertEqual(data["display_order"], 0)
        image = VehicleImage.objects.get(uuid=data["uuid"])
        self.assertTrue(image.image.storage.exists(image.image.name))
        self.assertNotIn("private-original", image.image.name)
        detail = self.detail()
        self.assertEqual(detail["primary_image"], detail["gallery"][0])
        self.assertEqual(detail["primary_image"]["uuid"], data["uuid"])
        self.assertEqual(detail["daily_rate"], "2000.00")
        self.assertEqual(detail["features"], [])
        for field in ("id", "vin", "registration_number", "internal_notes", "created_at", "audit"):
            self.assertNotIn(field, detail)
            self.assertNotIn(field, detail["gallery"][0])
        self.assertNotIn("vehicle-images/", str(detail))
        self.assertTrue(detail["primary_image"]["image"].startswith(PUBLIC))
        self.assertTrue(data["image"].startswith(ADMIN))
        event = AuditEvent.objects.get(action="vehicles.image_uploaded")
        self.assertNotIn("private-original", str(event.changes))
        self.assertNotIn(image.image.name, str(event.changes))

    def test_empty_gallery_has_null_primary(self) -> None:
        detail = self.detail()
        self.assertIsNone(detail["primary_image"])
        self.assertEqual(detail["gallery"], [])

    def test_supported_formats_are_reencoded_and_metadata_removed(self) -> None:
        for format in ("PNG", "JPEG", "WEBP"):
            with self.subTest(format=format):
                exif = Image.Exif()
                exif[270] = "PRIVATE CAMERA METADATA"
                exif[274] = 6
                response = self.client.post(
                    self.images, {"image": photo(format=format, exif=exif)}, format="multipart"
                )
                self.assertEqual(response.status_code, 201, response.content)
                saved = VehicleImage.objects.get(uuid=response.json()["data"]["uuid"])
                with saved.image.open("rb") as file, Image.open(file) as decoded:
                    self.assertEqual(decoded.format, "JPEG")
                    self.assertEqual(decoded.size, (20, 32))
                    self.assertEqual(dict(decoded.getexif()), {})

    @override_settings(VEHICLE_IMAGE_MAX_EDGE=16)
    def test_alpha_and_aspect_ratio_are_preserved_with_resize(self) -> None:
        response = self.client.post(self.images, {"image": photo(mode="RGBA")}, format="multipart")
        self.assertEqual(response.status_code, 201, response.content)
        saved = VehicleImage.objects.get(uuid=response.json()["data"]["uuid"])
        with saved.image.open("rb") as file, Image.open(file) as decoded:
            self.assertEqual(decoded.format, "PNG")
            self.assertEqual(decoded.mode, "RGBA")
            self.assertEqual(decoded.size, (16, 10))

    def test_primary_can_be_transferred_by_upload_or_patch(self) -> None:
        first = self.upload(is_primary=False)
        second = self.upload()
        self.assertTrue(first["is_primary"])
        self.assertFalse(second["is_primary"])
        third = self.upload(is_primary=True)
        self.assertEqual(self.detail()["primary_image"]["uuid"], third["uuid"])
        response = self.client.patch(self.image_url(second), {"is_primary": True})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.detail()["primary_image"]["uuid"], second["uuid"])
        self.assertEqual(self.vehicle.images.filter(is_primary=True).count(), 1)
        self.assertEqual(
            self.client.patch(self.image_url(second), {"is_primary": False}).status_code, 400
        )

    def test_ordering_is_editable_and_ties_are_stable(self) -> None:
        first = self.upload(display_order=10)
        second = self.upload(display_order=1)
        third = self.upload(display_order=1)
        self.assertEqual(
            [item["uuid"] for item in self.detail()["gallery"]],
            [second["uuid"], third["uuid"], first["uuid"]],
        )
        response = self.client.patch(
            self.image_url(first), {"display_order": 0, "alt_text": "Updated", "image_type": "REAR"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.detail()["gallery"][0]["alt_text"], "Updated")
        self.assertEqual(self.detail()["gallery"][0]["image_type"], "REAR")

    def test_deleting_primary_promotes_first_remaining_and_removes_file_after_commit(self) -> None:
        first = self.upload()
        second = self.upload(display_order=3)
        third = self.upload(display_order=1)
        saved = VehicleImage.objects.get(uuid=first["uuid"])
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.delete(self.image_url(first))
            self.assertEqual(response.status_code, 200)
            self.assertTrue(saved.image.storage.exists(saved.image.name))
        self.assertFalse(saved.image.storage.exists(saved.image.name))
        self.assertFalse(ImageFileDeletion.objects.exists())
        self.assertEqual(self.detail()["primary_image"]["uuid"], third["uuid"])
        self.assertEqual(self.client.get(first["image"]).status_code, 404)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.delete(self.image_url(third))
            self.client.delete(self.image_url(second))
        self.assertIsNone(self.detail()["primary_image"])
        self.assertEqual(self.detail()["gallery"], [])

    def test_storage_failure_retains_retry_job(self) -> None:
        data = self.upload()
        saved = VehicleImage.objects.get(uuid=data["uuid"])
        storage = saved.image.storage
        with (
            patch.object(storage, "delete", side_effect=OSError("storage unavailable")),
            self.captureOnCommitCallbacks(execute=True),
        ):
            self.assertEqual(self.client.delete(self.image_url(data)).status_code, 200)
        job = ImageFileDeletion.objects.get()
        self.assertEqual(job.attempts, 1)
        self.assertTrue(storage.exists(saved.image.name))
        ImageFileDeletion.objects.update(available_at=timezone.now())
        call_command("cleanup_vehicle_images", stdout=StringIO())
        self.assertFalse(ImageFileDeletion.objects.exists())
        self.assertFalse(storage.exists(saved.image.name))

    def test_delete_rollback_keeps_original_row_and_file(self) -> None:
        data = self.upload()
        saved = VehicleImage.objects.get(uuid=data["uuid"])
        with self.assertRaises(RuntimeError), transaction.atomic():
            media_services.delete_image(
                actor=self.users[Role.ADMIN], vehicle_uuid=self.vehicle.uuid, image_uuid=saved.uuid
            )
            raise RuntimeError("cancel transaction")
        self.assertTrue(VehicleImage.objects.filter(pk=saved.pk).exists())
        self.assertTrue(saved.image.storage.exists(saved.image.name))
        self.assertFalse(ImageFileDeletion.objects.exists())

    def test_upload_audit_failure_rolls_back_row_primary_and_file(self) -> None:
        first = self.upload()
        with (
            patch(
                "vehicles.media_services.record_media_event",
                side_effect=RuntimeError("audit failed"),
            ),
            self.assertRaises(RuntimeError),
        ):
            media_services.upload_image(
                actor=self.users[Role.ADMIN],
                vehicle_uuid=self.vehicle.uuid,
                data={"image": photo(), "is_primary": True},
            )
        self.assertEqual(self.vehicle.images.count(), 1)
        self.assertEqual(self.detail()["primary_image"]["uuid"], first["uuid"])
        self.assertEqual(len([file for file in self.media_root.rglob("*") if file.is_file()]), 1)

    def test_invalid_files_are_rejected_without_creating_files(self) -> None:
        for contents in (
            b"<svg onload='alert(1)'/>",
            b"%PDF-1.7",
            b"<html>bad</html>",
            b"not an image",
            photo().read()[:35],
            photo(format="GIF").read(),
        ):
            with self.subTest(contents=contents[:20]):
                response = self.client.post(
                    self.images,
                    {"image": SimpleUploadedFile("fake.jpg", contents, "image/jpeg")},
                    format="multipart",
                )
                self.assertEqual(response.status_code, 400, response.content)
                self.assertFalse(response.json()["success"])
                self.assertIn("image", response.json()["errors"])
        self.assertFalse(VehicleImage.objects.exists())
        self.assertFalse(any(self.media_root.rglob("*")))

    def test_animated_images_are_rejected(self) -> None:
        output = BytesIO()
        Image.new("RGB", (10, 10), "red").save(
            output, format="PNG", save_all=True, append_images=[Image.new("RGB", (10, 10), "blue")]
        )
        response = self.client.post(
            self.images,
            {"image": SimpleUploadedFile("animated.png", output.getvalue())},
            format="multipart",
        )
        self.assertEqual(response.status_code, 400)

    @override_settings(VEHICLE_IMAGE_MAX_BYTES=100)
    def test_streamed_upload_size_limit_returns_413(self) -> None:
        response = self.client.post(
            self.images,
            {"image": SimpleUploadedFile("big.jpg", b"x" * 200_000)},
            format="multipart",
        )
        self.assertEqual(response.status_code, 413, response.content)
        self.assertFalse(response.json()["success"])
        self.assertFalse(VehicleImage.objects.exists())

    @override_settings(VEHICLE_IMAGE_MAX_PIXELS=100)
    def test_decoded_pixel_limit_returns_validation_error(self) -> None:
        response = self.client.post(self.images, {"image": photo()}, format="multipart")
        self.assertEqual(response.status_code, 400, response.content)

    def test_metadata_validation_and_mass_assignment(self) -> None:
        for metadata in (
            {"display_order": -1},
            {"display_order": 2147483648},
            {"image_type": "DOCUMENT"},
            {"vehicle": str(uuid4())},
            {"uuid": str(uuid4())},
            {"alt_text": "x" * 256},
            {"is_primary": "sometimes"},
        ):
            response = self.client.post(
                self.images, {"image": photo(), **metadata}, format="multipart"
            )
            self.assertEqual(response.status_code, 400, response.content)
        data = self.upload()
        self.assertEqual(
            self.client.patch(self.image_url(data), {"image": "new.jpg"}).status_code, 400
        )
        self.assertEqual(self.client.post(self.images, {}).status_code, 415)

    def test_public_binary_delivery_is_scoped_and_not_cached(self) -> None:
        data = self.upload()
        url = self.detail()["primary_image"]["image"]
        self.client.force_authenticate(None)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/jpeg")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertTrue(b"".join(response.streaming_content).startswith(b"\xff\xd8"))
        for status in ("UNAVAILABLE", "MAINTENANCE", "INACTIVE"):
            Vehicle.objects.filter(pk=self.vehicle.pk).update(status=status)
            self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_authenticate(self.users[Role.ADMIN])
        response = self.client.get(data["image"])
        self.assertEqual(response.status_code, 200)
        list(response.streaming_content)
        saved = VehicleImage.objects.get(uuid=data["uuid"])
        self.assertEqual(self.client.get("/media/" + saved.image.name).status_code, 404)

    def test_cross_vehicle_image_access_returns_404(self) -> None:
        data = self.upload()
        other = Vehicle.objects.create(**vehicle_data(registration_number="OTHER", vin=None))
        url = f"{ADMIN}{other.uuid}/images/{data['uuid']}/"
        for method in ("get", "patch", "delete"):
            self.assertEqual(getattr(self.client, method)(url).status_code, 404)
        self.assertEqual(self.client.get(url + "file/").status_code, 404)
        self.assertEqual(self.client.get(url.replace(ADMIN, PUBLIC) + "file/").status_code, 404)

    def test_missing_stored_file_returns_safe_404(self) -> None:
        data = self.upload()
        saved = VehicleImage.objects.get(uuid=data["uuid"])
        saved.image.storage.delete(saved.image.name)
        response = self.client.get(data["image"])
        self.assertEqual(response.status_code, 404)
        self.assertNotIn(saved.image.name, response.content.decode())

    def test_all_roles_enforce_media_and_catalog_permissions(self) -> None:
        data = self.upload()
        feature = VehicleFeature.objects.create(name="Custom feature")
        for role in (None, *Role.values):
            with self.subTest(role=role):
                self.client.force_authenticate(self.users[role] if role else None)
                read = role in {Role.ADMIN, Role.SUPER_ADMIN, Role.STAFF}
                write = role in {Role.ADMIN, Role.SUPER_ADMIN}
                denied = 403 if role else 401
                for url in (
                    self.images,
                    self.image_url(data),
                    data["image"],
                    FEATURES,
                    FEATURES + str(feature.uuid) + "/",
                    self.assignments,
                ):
                    for method in ("get", "head", "options"):
                        response = getattr(self.client, method)(url)
                        self.assertEqual(
                            response.status_code, 200 if read else denied, (role, method, url)
                        )
                        if response.streaming:
                            list(response.streaming_content)
                response = self.client.post(self.images, {"image": photo()}, format="multipart")
                self.assertEqual(response.status_code, 201 if write else denied)
                response = self.client.patch(self.image_url(data), {"alt_text": str(role)})
                self.assertEqual(response.status_code, 200 if write else denied)
                response = self.client.put(self.assignments, {"feature_uuids": [str(feature.uuid)]})
                self.assertEqual(response.status_code, 200 if write else denied)
                response = self.client.post(FEATURES, {"name": f"Role {role} feature"})
                self.assertEqual(response.status_code, 201 if write else denied)
                response = self.client.patch(
                    FEATURES + str(feature.uuid) + "/", {"description": str(role)}
                )
                self.assertEqual(response.status_code, 200 if write else denied)
                # Missing UUIDs distinguish authorization from resource lookup without deletion.
                response = self.client.delete(self.images + str(uuid4()) + "/")
                self.assertEqual(response.status_code, 404 if write else denied)
                response = self.client.delete(FEATURES + str(uuid4()) + "/")
                self.assertEqual(response.status_code, 404 if write else denied)

    def test_mutations_recheck_demoted_actor_in_service(self) -> None:
        actor = self.users[Role.ADMIN]
        User.objects.filter(pk=actor.pk).update(role=Role.STAFF, is_staff=False)
        for operation in (
            lambda: media_services.upload_image(
                actor=actor, vehicle_uuid=self.vehicle.uuid, data={"image": photo()}
            ),
            lambda: media_services.update_image(
                actor=actor, vehicle_uuid=self.vehicle.uuid, image_uuid=uuid4(), data={}
            ),
            lambda: media_services.delete_image(
                actor=actor, vehicle_uuid=self.vehicle.uuid, image_uuid=uuid4()
            ),
            lambda: media_services.save_feature(actor=actor, data={"name": "Denied"}),
            lambda: media_services.assign_features(
                actor=actor, vehicle_uuid=self.vehicle.uuid, data={"feature_uuids": []}
            ),
        ):
            with self.assertRaises(PermissionDenied):
                operation()

    def test_feature_catalog_create_update_and_case_insensitive_uniqueness(self) -> None:
        response = self.client.post(
            FEATURES, {"name": "  Heated   Seats  ", "description": "Warm seats"}
        )
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()["data"]
        self.assertEqual(data["name"], "Heated Seats")
        self.assertEqual(self.client.post(FEATURES, {"name": "heated seats"}).status_code, 400)
        response = self.client.patch(FEATURES + data["uuid"] + "/", {"name": "Ventilated Seats"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["name"], "Ventilated Seats")

    def test_feature_assignment_reuses_catalog_and_public_only_has_active_features(self) -> None:
        first = VehicleFeature.objects.create(name="Reusable feature")
        second = VehicleFeature.objects.create(name="Another feature")
        other = Vehicle.objects.create(**vehicle_data(registration_number="SECOND", vin=None))
        for vehicle in (self.vehicle, other):
            response = self.client.put(
                f"{ADMIN}{vehicle.uuid}/features/",
                {"feature_uuids": [str(first.uuid), str(second.uuid)]},
            )
            self.assertEqual(response.status_code, 200)
        self.assertEqual(first.vehicles.count(), 2)
        self.assertEqual(
            [item["name"] for item in self.detail()["features"]], [second.name, first.name]
        )
        self.assertEqual(set(self.detail()["features"][0]), {"uuid", "name", "description"})
        self.assertEqual(self.client.delete(FEATURES + str(first.uuid) + "/").status_code, 200)
        self.assertEqual(len(self.detail()["features"]), 1)
        self.assertEqual(self.vehicle.features.count(), 2)
        assigned = self.client.get(self.assignments).json()["data"]
        self.assertFalse(
            next(item for item in assigned if item["uuid"] == str(first.uuid))["is_active"]
        )
        self.assertEqual(self.client.put(self.assignments, {"feature_uuids": []}).status_code, 200)
        self.assertEqual(self.detail()["features"], [])

    def test_invalid_assignments_are_atomic(self) -> None:
        active = VehicleFeature.objects.create(name="Assigned")
        inactive = VehicleFeature.objects.create(name="Inactive", is_active=False)
        self.vehicle.features.add(active)
        for values in (
            [str(uuid4())],
            [str(active.uuid), str(uuid4())],
            [str(active.uuid)] * 2,
            [str(inactive.uuid)],
            ["not-a-uuid"],
        ):
            response = self.client.put(self.assignments, {"feature_uuids": values})
            self.assertEqual(response.status_code, 400, response.content)
            self.assertEqual(list(self.vehicle.features.all()), [active])
        self.assertEqual(self.client.put(self.assignments, {}).status_code, 400)

    def test_postgres_enforces_one_primary_and_feature_name_uniqueness(self) -> None:
        self.upload()
        with self.assertRaises(IntegrityError), transaction.atomic():
            VehicleImage.objects.create(
                vehicle=self.vehicle, image="vehicle-images/duplicate.jpg", is_primary=True
            )
        VehicleFeature.objects.create(name="Unique Feature")
        with self.assertRaises(IntegrityError), transaction.atomic():
            VehicleFeature.objects.create(name="unique feature")

    def test_catalog_queries_remain_bounded_with_media(self) -> None:
        feature = VehicleFeature.objects.create(name="Query test feature")
        for index in range(30):
            vehicle = Vehicle.objects.create(
                **vehicle_data(registration_number=f"QUERY{index}", vin=None)
            )
            vehicle.features.add(feature)
            VehicleImage.objects.bulk_create(
                [
                    VehicleImage(
                        vehicle=vehicle,
                        image=f"vehicle-images/query-{index}-{image_index}.jpg",
                        is_primary=image_index == 0,
                        display_order=image_index,
                    )
                    for image_index in range(4)
                ]
            )
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(PUBLIC, {"page_size": 30})
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(queries), 4)
        self.assertTrue(all(item["primary_image"] for item in response.json()["data"]["results"]))
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(f"{PUBLIC}{vehicle.uuid}/")
        self.assertEqual(len(response.json()["data"]["gallery"]), 4)
        self.assertEqual(len(response.json()["data"]["features"]), 1)
        self.assertLessEqual(len(queries), 4)


class VehicleMediaConcurrencyTests(TransactionTestCase):
    def test_simultaneous_primary_uploads_serialize_on_vehicle(self) -> None:
        vehicle = Vehicle.objects.create(**vehicle_data())
        actors = [
            User.objects.create_user(
                email=f"concurrent{index}@example.com", password=PASSWORD, role=Role.ADMIN
            )
            for index in range(2)
        ]
        barrier = Barrier(2)

        def upload(actor):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return media_services.upload_image(
                    actor=actor,
                    vehicle_uuid=vehicle.uuid,
                    data={"image": photo(), "is_primary": True},
                ).uuid
            finally:
                close_old_connections()

        with (
            TemporaryDirectory() as root,
            override_settings(MEDIA_ROOT=root),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            results = list(pool.map(upload, actors))
            self.assertEqual(len(set(results)), 2)
            self.assertEqual(vehicle.images.count(), 2)
            self.assertEqual(vehicle.images.filter(is_primary=True).count(), 1)
            self.assertEqual(AuditEvent.objects.filter(action="vehicles.image_uploaded").count(), 2)
            self.assertEqual(sorted(vehicle.images.values_list("display_order", flat=True)), [0, 1])
