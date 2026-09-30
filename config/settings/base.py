"""Shared settings; PostgreSQL is required in every environment."""

from datetime import timedelta
from pathlib import Path

from config.environment import BASE_DIR, env, positive_integer, timezone_name

DEBUG = False
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",
    "accounts.apps.AccountsConfig",
    "vehicles.apps.VehiclesConfig",
    "pricing.apps.PricingConfig",
    "availability.apps.AvailabilityConfig",
    "bookings.apps.BookingsConfig",
    "maintenance.apps.MaintenanceConfig",
    "customers.apps.CustomersConfig",
    "notifications.apps.NotificationsConfig",
    "audit.apps.AuditConfig",
    "reports.apps.ReportsConfig",
    "common.apps.CommonConfig",
]

MIDDLEWARE = [
    "common.middleware.RequestIDMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB"),
        "USER": env("POSTGRES_USER"),
        "PASSWORD": env("POSTGRES_PASSWORD"),
        "HOST": env("POSTGRES_HOST"),
        "PORT": positive_integer("POSTGRES_PORT", default=5432),
        "CONN_MAX_AGE": 0,
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {
            "connect_timeout": positive_integer("POSTGRES_CONNECT_TIMEOUT", default=5),
            "sslmode": env("POSTGRES_SSLMODE", default="prefer"),
        },
        "TEST": {"NAME": env("POSTGRES_TEST_DB", default="test_abound")},
    },
}

AUTH_USER_MODEL = "accounts.User"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = timezone_name()
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = Path(env("DJANGO_MEDIA_ROOT", default=str(BASE_DIR / "media")))
FILE_UPLOAD_PERMISSIONS = 0o640
FILE_UPLOAD_DIRECTORY_PERMISSIONS = 0o750
DATA_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024
VEHICLE_IMAGE_MAX_BYTES = positive_integer("VEHICLE_IMAGE_MAX_BYTES", default=8 * 1024 * 1024)
VEHICLE_IMAGE_MAX_PIXELS = positive_integer("VEHICLE_IMAGE_MAX_PIXELS", default=20_000_000)
VEHICLE_IMAGE_MAX_EDGE = positive_integer("VEHICLE_IMAGE_MAX_EDGE", default=2048)
VEHICLE_DOCUMENT_MAX_BYTES = positive_integer(
    "VEHICLE_DOCUMENT_MAX_BYTES", default=15 * 1024 * 1024
)
PRICING_MAX_RENTAL_DAYS = positive_integer("PRICING_MAX_RENTAL_DAYS", default=3660)

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "accounts.authentication.SessionJWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["accounts.permissions.ApplicationPermission"],
    "DEFAULT_RENDERER_CLASSES": ["common.renderers.EnvelopeJSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "EXCEPTION_HANDLER": "common.exceptions.api_exception_handler",
    "DEFAULT_VERSIONING_CLASS": "rest_framework.versioning.NamespaceVersioning",
    "DEFAULT_VERSION": "v1",
    "ALLOWED_VERSIONS": ["v1"],
    "DEFAULT_PAGINATION_CLASS": "common.pagination.StandardPagination",
    "PAGE_SIZE": 25,
    "COERCE_DECIMAL_TO_STRING": True,
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(
        minutes=positive_integer("JWT_ACCESS_MINUTES", default=5),
    ),
    "REFRESH_TOKEN_LIFETIME": timedelta(
        days=positive_integer("JWT_REFRESH_DAYS", default=1),
    ),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": False,
    "ALGORITHM": "HS256",
    "ISSUER": "abound-holidays",
    "AUDIENCE": "abound-api",
    "USER_ID_FIELD": "uuid",
}

AUTH_SESSION_DAYS = positive_integer("AUTH_SESSION_DAYS", default=7)
AUTH_REFRESH_COOKIE_NAME = "abound_refresh"
AUTH_COOKIE_SECURE = True
PASSWORD_RESET_TIMEOUT = positive_integer("PASSWORD_RESET_TIMEOUT", default=3600)
AUTH_PASSWORD_RESET_URL = env("AUTH_PASSWORD_RESET_URL", default="")
AUTH_PASSWORD_RESET_URL_MOBILE = env("AUTH_PASSWORD_RESET_URL_MOBILE", default="")
AUTH_BACKEND_BASE_URL = env("AUTH_BACKEND_BASE_URL", default="http://localhost:8000")
AUTH_RESET_RETRY_SECONDS = positive_integer("AUTH_RESET_RETRY_SECONDS", default=60)
AUTH_RESET_MAX_ATTEMPTS = positive_integer("AUTH_RESET_MAX_ATTEMPTS", default=3)
AUTH_RECORD_RETENTION_DAYS = positive_integer("AUTH_RECORD_RETENTION_DAYS", default=30)
AUTH_REGISTRATION_CODE_SECONDS = positive_integer("AUTH_REGISTRATION_CODE_SECONDS", default=600)
AUTH_REGISTRATION_RESEND_SECONDS = positive_integer("AUTH_REGISTRATION_RESEND_SECONDS", default=60)
AUTH_REGISTRATION_MAX_ATTEMPTS = positive_integer("AUTH_REGISTRATION_MAX_ATTEMPTS", default=5)
EMAIL_TIMEOUT = 10

NOTIFICATION_RETRY_SECONDS = positive_integer("NOTIFICATION_RETRY_SECONDS", default=60)
NOTIFICATION_MAX_ATTEMPTS = positive_integer("NOTIFICATION_MAX_ATTEMPTS", default=3)
BOOKING_REMINDER_DAYS_BEFORE = positive_integer("BOOKING_REMINDER_DAYS_BEFORE", default=1)
MAINTENANCE_REMINDER_DAYS_BEFORE = positive_integer("MAINTENANCE_REMINDER_DAYS_BEFORE", default=2)

# Requests per fixed window; overrides are shared by all API workers through PostgreSQL.
AUTH_RATE_LIMITS = {
    scope: (
        positive_integer(f"AUTH_RATE_{scope.upper()}_COUNT", default=count),
        positive_integer(f"AUTH_RATE_{scope.upper()}_SECONDS", default=seconds),
    )
    for scope, count, seconds in [
        ("csrf_ip", 60, 60),
        ("register_ip", 10, 3600),
        ("register_code_ip", 20, 3600),
        ("register_code_identity", 5, 3600),
        ("login_ip", 30, 300),
        ("login_identity", 10, 300),
        ("refresh_ip", 60, 60),
        ("account_ip", 120, 60),
        ("password_change_identity", 5, 300),
        ("reset_ip", 10, 3600),
        ("reset_identity", 3, 3600),
        ("reset_confirm_ip", 10, 300),
        ("vehicle_catalog_ip", 120, 60),
        ("pricing_quote_ip", 60, 60),
        ("availability_check_ip", 60, 60),
        ("bookings_ip", 120, 60),
    ]
}

CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])
CORS_ALLOW_CREDENTIALS = True
CORS_URLS_REGEX = r"^/api/v1/.*$"
CORS_EXPOSE_HEADERS = ["X-Request-ID", "Retry-After"]
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])
CSRF_FAILURE_VIEW = "common.errors.csrf_failure"
APPEND_SLASH = False

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"request_id": {"()": "common.logging.RequestIDFilter"}},
    "formatters": {"json": {"()": "common.logging.JSONFormatter"}},
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
            "filters": ["request_id"],
        },
    },
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", default="INFO")},
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "django.server": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
