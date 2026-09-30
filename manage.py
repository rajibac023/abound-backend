#!/usr/bin/env python
"""Django's command-line entry point (local settings by default)."""

import os
import sys


def main() -> None:
    if "test" in sys.argv:
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.test")
    else:
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
