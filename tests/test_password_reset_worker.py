from io import StringIO
from threading import Event
from unittest.mock import patch

from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User
from notifications.management.commands.runserver import Command
from notifications.models import PasswordResetDelivery
from notifications.reset_worker import run_reset_worker
from notifications.services import deliver_one_password_reset


class ResetWorkerDeliveryTests(TransactionTestCase):
    def test_worker_delivers_a_queued_request_and_skips_unknown_accounts(self):
        User.objects.create_user(email="recovery@example.test", password="Long-passphrase-2026")
        client = APIClient()
        known = client.post("/api/v1/auth/password/reset/", {"email": "recovery@example.test"})
        unknown = client.post("/api/v1/auth/password/reset/", {"email": "unknown@example.test"})
        self.assertEqual(known.status_code, 202)
        self.assertEqual(known.json(), unknown.json())
        self.assertEqual(len(mail.outbox), 0)
        stop = Event()

        def deliver():
            result = deliver_one_password_reset()
            if not result:
                stop.set()
            return result

        with patch("notifications.reset_worker.deliver_one_password_reset", side_effect=deliver):
            run_reset_worker(stop)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["recovery@example.test"])
        self.assertEqual(PasswordResetDelivery.objects.filter(status="sent").count(), 1)
        self.assertEqual(PasswordResetDelivery.objects.filter(status="skipped").count(), 1)


class ResetWorkerLifecycleTests(SimpleTestCase):
    def test_transient_errors_retry_without_exposing_credentials(self):
        stop = Event()
        attempts = 0

        def deliver():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("private-database-password")
            stop.set()
            return False

        with (
            patch("notifications.reset_worker.deliver_one_password_reset", side_effect=deliver),
            patch("notifications.reset_worker.close_old_connections"),
            patch("notifications.reset_worker.connections.close_all") as close,
            patch.object(stop, "wait"),
            self.assertLogs("notifications.reset_worker", level="WARNING") as captured,
        ):
            run_reset_worker(stop)
        self.assertEqual(attempts, 2)
        self.assertGreaterEqual(close.call_count, 2)
        self.assertNotIn("private-database-password", " ".join(captured.output))

    @override_settings(AUTH_RESET_DEV_WORKER=True)
    def test_local_server_starts_worker_after_binding_and_stops_it_on_exit(self):
        command = Command(stdout=StringIO())

        def serve(*args, **kwargs):
            thread.assert_not_called()
            command.on_bind(8000)
            self.assertFalse(command.mail_stop.is_set())
            thread.return_value.start.assert_called_once()

        with (
            patch("notifications.management.commands.runserver.Thread") as thread,
            patch("notifications.management.commands.runserver.StaticRunserver.on_bind"),
            patch(
                "notifications.management.commands.runserver.StaticRunserver.inner_run",
                side_effect=serve,
            ),
        ):
            command.inner_run()
        self.assertTrue(command.mail_stop.is_set())
        thread.return_value.join.assert_called_once()

    @override_settings(AUTH_RESET_DEV_WORKER=False)
    def test_disabled_local_worker_does_not_start(self):
        command = Command(stdout=StringIO())
        with (
            patch("notifications.management.commands.runserver.Thread") as thread,
            patch("notifications.management.commands.runserver.StaticRunserver.on_bind"),
        ):
            command.on_bind(8000)
        thread.assert_not_called()

    def test_watch_command_uses_worker_and_validates_poll_interval(self):
        with patch(
            "notifications.management.commands.deliver_password_resets.run_reset_worker"
        ) as worker:
            call_command(
                "deliver_password_resets", watch=True, interval=3, limit=25, stdout=StringIO()
            )
        self.assertEqual(worker.call_args.kwargs, {"interval": 3, "limit": 25})
        with self.assertRaises(CommandError):
            call_command("deliver_password_resets", watch=True, interval=0, stdout=StringIO())
