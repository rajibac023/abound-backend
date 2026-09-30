from typing import Any

from django.core.management.base import BaseCommand, CommandError

from vehicles.file_cleanup import cleanup_image_file


class Command(BaseCommand):
    help = "Retry queued vehicle image file deletions after their database rows were removed."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args: Any, **options: Any) -> None:
        if options["limit"] < 1:
            raise CommandError("--limit must be positive.")
        processed = 0
        while processed < options["limit"] and cleanup_image_file():
            processed += 1
        self.stdout.write(f"Processed {processed} queued image deletions.")
