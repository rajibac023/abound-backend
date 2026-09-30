from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from notifications.services import deliver_one_notification


class Command(BaseCommand):
    help = "Deliver a bounded batch of queued notifications across all channels."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args: Any, **options: Any) -> None:
        if options["limit"] < 1:
            raise CommandError("--limit must be positive.")
        processed = 0
        for _ in range(options["limit"]):
            if not deliver_one_notification():
                break
            processed += 1
        self.stdout.write(f"Processed {processed} notification delivery jobs.")
