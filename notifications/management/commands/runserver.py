from threading import Event, Thread

from django.conf import settings
from django.contrib.staticfiles.management.commands.runserver import Command as StaticRunserver

from notifications.reset_worker import run_reset_worker


class Command(StaticRunserver):
    """Local settings select this command so reset requests cannot sit in an idle queue."""

    def inner_run(self, *args, **options):
        self.mail_stop = Event()
        self.mail_thread: Thread | None = None
        try:
            return super().inner_run(*args, **options)
        finally:
            self.mail_stop.set()
            if self.mail_thread is not None:
                self.mail_thread.join(timeout=settings.EMAIL_TIMEOUT + 2)

    def on_bind(self, server_port):
        super().on_bind(server_port)
        # on_bind runs after checks and successful binding, only in the reloader child.
        if getattr(settings, "AUTH_RESET_DEV_WORKER", False) and settings.AUTH_PASSWORD_RESET_URL:
            self.mail_thread = Thread(
                target=run_reset_worker,
                args=(self.mail_stop,),
                name="password-reset-delivery",
                daemon=True,
            )
            self.mail_thread.start()
            self.stdout.write("Password reset email delivery is running (polls every 2 seconds).")
