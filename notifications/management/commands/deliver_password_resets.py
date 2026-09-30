from threading import Event
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from notifications.reset_worker import run_reset_worker
from notifications.services import deliver_one_password_reset


class Command(BaseCommand):
    help = "Deliver a bounded batch of queued password resets without printing credentials."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--watch", action="store_true", help="Keep polling for new requests.")
        parser.add_argument("--interval", type=int, default=2, help="Seconds between batches.")

    def handle(self, *args: Any, **options: Any) -> None:
        if not settings.AUTH_PASSWORD_RESET_URL:
            raise CommandError("Configure AUTH_PASSWORD_RESET_URL before delivering resets.")
        if options["limit"] < 1:
            raise CommandError("--limit must be positive.")
        if options["interval"] < 1:
            raise CommandError("--interval must be positive.")
        if options["watch"]:
            self.stdout.write("Password reset email delivery is running.")
            stop = Event()
            try:
                run_reset_worker(stop, interval=options["interval"], limit=options["limit"])
            except KeyboardInterrupt:
                stop.set()
            return
        processed = 0
        for _ in range(options["limit"]):
            if not deliver_one_password_reset():
                break
            processed += 1
        self.stdout.write(f"Processed {processed} password reset delivery jobs.")
