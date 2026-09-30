from contextlib import contextmanager
from datetime import timedelta
from uuid import uuid4

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


@contextmanager
def legacy_schema():
    """Exercise the real upgrade in an isolated schema inside the disposable test DB."""
    schema = connection.ops.quote_name("auth_upgrade_" + uuid4().hex)
    with connection.cursor() as cursor:
        cursor.execute("SHOW search_path")
        previous_path = cursor.fetchone()[0]
        cursor.execute(f"CREATE SCHEMA {schema}")
        cursor.execute(f"SET search_path TO {schema}")
    try:
        executor = MigrationExecutor(connection)
        target = [("accounts", "0001_initial")]
        executor.migrate(target)
        yield executor.loader.project_state(target).apps.get_model("accounts", "User")
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('search_path', %s, false)", [previous_path])
            cursor.execute(f"DROP SCHEMA {schema} CASCADE")


class EmailIdentityMigrationTests(TransactionTestCase):
    def test_upgrade_preserves_identity_password_and_creation_date(self) -> None:
        with legacy_schema() as legacy_user:
            first = legacy_user.objects.create(
                username="legacy-one",
                email=" First@Example.com ",
                password="existing-encoded-hash",
                first_name="First",
                is_staff=True,
                is_superuser=True,
            )
            second = legacy_user.objects.create(username="legacy-two", email="second@example.com")
            executor = MigrationExecutor(connection)
            target = [("accounts", "0002_email_identity_and_sessions")]
            executor.migrate(target)
            user_model = executor.loader.project_state(target).apps.get_model("accounts", "User")
            migrated = user_model.objects.get(pk=first.pk)
            self.assertEqual(migrated.email, "first@example.com")
            self.assertEqual(migrated.password, first.password)
            self.assertEqual(migrated.created_at, first.date_joined)
            self.assertEqual(migrated.first_name, first.first_name)
            self.assertEqual(migrated.role, "admin")
            self.assertNotEqual(migrated.uuid, user_model.objects.get(pk=second.pk).uuid)

    def test_colliding_emails_abort_upgrade_without_changing_accounts(self) -> None:
        with legacy_schema() as legacy_user:
            legacy_user.objects.create(username="one", email="Same@example.com")
            legacy_user.objects.create(username="two", email="same@example.com")
            with self.assertRaisesMessage(RuntimeError, "unique normalized emails"):
                MigrationExecutor(connection).migrate(
                    [("accounts", "0002_email_identity_and_sessions")]
                )
            self.assertEqual(legacy_user.objects.count(), 2)
            self.assertTrue(
                legacy_user.objects.filter(username="one", email="Same@example.com").exists()
            )

    def test_missing_email_aborts_without_fabricating_identity(self) -> None:
        with legacy_schema() as legacy_user:
            user = legacy_user.objects.create(username="missing-email", email="")
            with self.assertRaisesMessage(RuntimeError, "valid emails"):
                MigrationExecutor(connection).migrate(
                    [("accounts", "0002_email_identity_and_sessions")]
                )
            self.assertEqual(legacy_user.objects.get(pk=user.pk).email, "")


class PlatformRoleMigrationTests(TransactionTestCase):
    def test_upgrade_maps_existing_roles_and_superusers_without_changing_identity(self) -> None:
        with legacy_schema():
            executor = MigrationExecutor(connection)
            previous = [("accounts", "0002_email_identity_and_sessions")]
            executor.migrate(previous)
            apps = executor.loader.project_state(previous).apps
            old_user = apps.get_model("accounts", "User")
            old_session = apps.get_model("accounts", "AuthSession")
            cases = [
                ("customer", False, False, "CUSTOMER"),
                ("staff", True, False, "STAFF"),
                ("admin", True, False, "ADMIN"),
                ("admin", True, True, "SUPER_ADMIN"),
            ]
            records = []
            for index, (role, staff, superuser, expected) in enumerate(cases):
                user = old_user.objects.create(
                    email=f"legacy-{index}@example.com",
                    role=role,
                    is_staff=staff,
                    is_superuser=superuser,
                    password="preserved-password-hash",
                )
                session = old_session.objects.create(
                    user=user,
                    client="mobile",
                    refresh_digest="digest",
                    password_fingerprint="fingerprint",
                    expires_at=timezone.now() + timedelta(days=1),
                )
                records.append((user, session, expected))
            executor = MigrationExecutor(connection)
            target = [("accounts", "0003_platform_roles")]
            executor.migrate(target)
            apps = executor.loader.project_state(target).apps
            for previous_user, previous_session, role in records:
                current = apps.get_model("accounts", "User").objects.get(pk=previous_user.pk)
                self.assertEqual(current.uuid, previous_user.uuid)
                self.assertEqual(current.email, previous_user.email)
                self.assertEqual(current.password, previous_user.password)
                self.assertEqual(current.created_at, previous_user.created_at)
                self.assertEqual(current.role, role)
                self.assertEqual(current.is_staff, role in {"ADMIN", "SUPER_ADMIN"})
                self.assertEqual(current.is_superuser, role == "SUPER_ADMIN")
                session = apps.get_model("accounts", "AuthSession").objects.get(
                    pk=previous_session.pk
                )
                self.assertIsNotNone(session.revoked_at)
            # Role changes support rollback without rewriting the email identity migration.
            MigrationExecutor(connection).migrate(previous)
            for previous_user, _, _ in records:
                restored = old_user.objects.get(pk=previous_user.pk)
                self.assertEqual(restored.role, previous_user.role)
                self.assertEqual(restored.is_superuser, previous_user.is_superuser)
