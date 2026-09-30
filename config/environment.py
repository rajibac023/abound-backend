"""Environment parsing shared by Django settings modules."""

import os
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import environ
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
env = environ.Env()

# A file is a local convenience. Deployed processes use injected environment values.
if os.environ.get("DJANGO_SETTINGS_MODULE") in {"config.settings.local", "config.settings.test"}:
    environ.Env.read_env(BASE_DIR / ".env", overwrite=False)


def required_secret(name: str) -> str:
    value: str = env(name)
    if (
        len(value) < 50
        or len(set(value)) < 5
        or value.startswith(("change-me", "django-insecure-"))
    ):
        raise ImproperlyConfigured(
            f"{name} must be a unique, random secret of at least 50 characters."
        )
    return value


def positive_integer(name: str, default: int) -> int:
    value = env.int(name, default=default)
    if value < 1:
        raise ImproperlyConfigured(f"{name} must be a positive integer.")
    return value


def timezone_name() -> str:
    value: str = env("DJANGO_TIME_ZONE", default="Asia/Kathmandu")
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise ImproperlyConfigured("DJANGO_TIME_ZONE must be a valid IANA timezone.") from exc
    return value
