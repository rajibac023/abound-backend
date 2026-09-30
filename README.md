# Backend foundation, authentication, authorization, fleet, and media

## Requirements

- Python 3.12.
- PostgreSQL 14 or newer; development Compose and CI target PostgreSQL 17.
- PostgreSQL credentials for a development database. Tests need a disposable role
  permitted to create a separate test database. Never run tests against production.

Dependencies are declared in `requirements/base.in` and `requirements/dev.in`,
then resolved to exact versions in their corresponding `.txt` files. `pyproject.toml`
configures tools; it is not a second dependency manifest.

## Initial setup

From the repository root:

```sh
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements/dev.txt
cp backend/.env.example backend/.env
```

Skip the copy if `backend/.env` already exists. Update both signing secrets with
independent random values and set a local PostgreSQL password. To generate each secret:

```sh
backend/.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(64))'
```

Real `.env` files are ignored. Placeholder secrets deliberately fail configuration
validation. Local and test settings read `backend/.env` without overriding existing
environment variables. Production reads injected environment variables only.

Start a PostgreSQL development database with Docker Compose, if Docker is available:

```sh
docker compose --env-file backend/.env -f infrastructure/compose.yaml up -d --wait
```

Alternatively, point the PostgreSQL environment variables at a native development
instance and create the configured database and role. Compose binds its database
port to loopback only. Its volume is persistent; changing `.env` does not reset
credentials inside an already initialized database.

Then run:

```sh
cd backend
.venv/bin/python manage.py migrate
.venv/bin/python manage.py check
.venv/bin/python manage.py runserver
```

To test the mobile app on a physical phone, bind to all interfaces instead:
`.venv/bin/python manage.py runserver 0.0.0.0:8000`. Plain `runserver` listens on
`127.0.0.1` only, so the phone gets "Unable to reach ABound Holidays". The computer's
LAN IP must also appear in `DJANGO_ALLOWED_HOSTS` and in the mobile app's
`EXPO_PUBLIC_API_URL`.

Local `runserver` also polls the password reset queue every two seconds, after the
server binds successfully. Configure SMTP in `.env` for real email delivery. If a
separate worker is already running, set `AUTH_RESET_DEV_WORKER=False`. Production
must supervise `.venv/bin/python manage.py deliver_password_resets --watch` (or
schedule the existing bounded command every minute).

The default email link opens `http://localhost:5173/reset-password`, so run the web
frontend with `cd frontend && npm run dev` from the repository root as well. Set
`AUTH_PASSWORD_RESET_URL` to the deployed frontend's `/reset-password` URL outside
local development; localhost links only work on the machine hosting the frontend.

## API

All application routes use `/api/v1/` and canonical trailing slashes. Missing
slashes return 404 rather than redirecting a POST request.

| Endpoint | Behavior |
| --- | --- |
| `GET /api/v1/health/` | Readiness: executes `SELECT 1`; returns 200 or 503 |
| `GET /api/v1/health/live/` | Process liveness: no database access |

Both health endpoints are public, ignore Authorization headers, and set
`Cache-Control: no-store`. Readiness returns no hostnames, credentials, database
names, or dependency versions. It checks connectivity, not migration freshness.

```json
{
  "success": true,
  "data": {"status": "ok", "database": "ok"},
  "message": "Operation completed successfully."
}
```

A database outage produces HTTP 503:

```json
{
  "success": false,
  "message": "Unable to complete the operation.",
  "errors": {"database": ["Unavailable."]}
}
```

DRF responses are enveloped by `EnvelopeJSONRenderer`, once, at the HTTP boundary.
Views return plain data in `Response`; do not manually add a second envelope.
Validation failures retain field errors. Authentication, permission, routing,
method, negotiation, and server failures retain appropriate HTTP status codes.
Framework error handlers also produce JSON when `DEBUG=False`. Debug mode can
intentionally expose Django's development error pages; never enable it in production.

Successful vehicle image file requests return binary images. HTTP 204/304, HEAD,
redirects, and CORS preflight follow HTTP semantics and do not
promise a JSON response body. Responses carry a generated `X-Request-ID` for log
correlation. Clients cannot supply arbitrary identifiers into server logs.

Collections use `data: {count, next, previous, results}` with 25 results by default
and at most 100 via `page_size`. Namespace versioning currently permits only `v1`.

Session-validated JWT authentication and `ApplicationPermission` are the API defaults.
Active users are required; administrative routes additionally require declared
domain grants. Public routes explicitly opt into `AllowAny`.
Email registration/login, refresh, logout, current-user, password change, and password
reset endpoints are under `/api/v1/auth/`. See [the authentication guide](../docs/authentication.md)
for payloads, browser CSRF, immediate revocation, reset delivery, and rate limits.
The [authorization guide](../docs/authorization.md) covers all four roles, permissions,
account role/activation management, restricted audit reads, and migration behavior.
The [vehicle guide](../docs/vehicles.md) documents fleet CRUD, the customer catalog,
stored rental terms, visibility, filtering, sorting, and deactivation. Pricing
calculations and booking/maintenance allocation workflows remain future work.
The [media and feature guide](../docs/vehicle-media.md) documents image uploads,
primary selection, customer galleries, reusable feature assignment, and file cleanup.

## Configuration

| Area | Configuration |
| --- | --- |
| Settings | `config.settings.local`, `.test`, `.production` |
| Database | `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT` |
| Database timeout/TLS | `POSTGRES_CONNECT_TIMEOUT`, `POSTGRES_SSLMODE`, production `POSTGRES_SSLROOTCERT` |
| Test database | `POSTGRES_TEST_DB`, beginning with `test_` and distinct from the application database |
| Secrets | `DJANGO_SECRET_KEY`, `JWT_SIGNING_KEY`; independent secrets in production |
| Hosts/origins | `DJANGO_ALLOWED_HOSTS`, `CORS_ALLOWED_ORIGINS`, `CSRF_TRUSTED_ORIGINS` |
| Time | `USE_TZ=True`; UTC database timestamps; display timezone defaults to `Asia/Kathmandu` |
| Logs | JSON on stderr, request ID correlation, `LOG_LEVEL` |
| Files | Collected static: `backend/staticfiles/`; private media: `DJANGO_MEDIA_ROOT` (default `backend/media/`) |
| Images | `VEHICLE_IMAGE_MAX_BYTES`, `VEHICLE_IMAGE_MAX_PIXELS`, `VEHICLE_IMAGE_MAX_EDGE` |

Application signing keys and server credentials must stay out of frontend bundles.
CORS is an explicit browser-origin allowlist and is not authorization. Browser
refresh cookies are HttpOnly, Secure in production, and require CSRF validation.
Mobile clients receive token pairs explicitly and must store refresh tokens securely.

Static and media directories are distinct and ignored. Run `collectstatic` during
deployment and serve public static assets through the deployment server. Media is
not exposed through a catch-all Django URL; private documents require authorized
access rather than being placed under a publicly served media root.
Vehicle images are served through visibility-scoped API routes. Persist media across
deployments and schedule `manage.py cleanup_vehicle_images --limit 100` every minute
to retry failed file deletions; see the media guide for backup and storage boundaries.

## Verification

From `backend/`, with the configured development PostgreSQL server running:

```sh
.venv/bin/python -m pip check
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
.venv/bin/python manage.py check
.venv/bin/python manage.py makemigrations --check --dry-run
.venv/bin/python manage.py test --settings=config.settings.test --noinput
```

Apply formatting with `.venv/bin/ruff format .`. Mypy checks project code and
annotated functions, allowing untyped third-party libraries; it does not claim
complete static verification of Django's dynamically generated ORM types.

Tests exercise real PostgreSQL connectivity and migrations, healthy/unhealthy
readiness, database-independent liveness, API envelopes and status codes, JWT user
authentication, permission defaults, pagination, CORS, request IDs, and production
configuration rejection. Authentication tests additionally cover email uniqueness,
password policy, logout, rotation/replay, concurrent refresh, browser CSRF, and reset
delivery. Migration tests upgrade legacy accounts in isolated PostgreSQL schemas.
Authorization tests cover all 19 permission keys for all four roles, hidden account
records, forged grants, access-change revocation, and concurrent administrator changes.
Vehicle tests cover CRUD, validation, unique identity races, public field/row visibility,
all role permissions, database indexes/constraints, and pagination over 1,005 vehicles.
Media tests cover safe image decoding, primary selection under concurrency, gallery
ordering, post-commit deletion/retries, feature assignment, and public serialization.
Test-only routes live in `tests/urls.py` and are never
included in the application URL configuration.

For production checks, inject the variables described in `.env.production.example`:

```sh
DJANGO_SETTINGS_MODULE=config.settings.production .venv/bin/python manage.py check --deploy --fail-level WARNING
DJANGO_SETTINGS_MODULE=config.settings.production .venv/bin/python manage.py collectstatic --noinput
```

`check --deploy` is a settings check, not proof of network/TLS/database connectivity.
Validate deployment connectivity separately in its actual environment.
Only the HSTS subdomain/preload recommendations (`security.W005`, `security.W021`)
are explicitly excepted; the rationale is in the operations guide.

## Updating dependencies

From the repository root, edit `.in` files and regenerate locks:

```sh
backend/.venv/bin/pip-compile --strip-extras --no-emit-index-url --no-emit-trusted-host --output-file=backend/requirements/base.txt backend/requirements/base.in
backend/.venv/bin/pip-compile --strip-extras --no-emit-index-url --no-emit-trusted-host --output-file=backend/requirements/dev.txt backend/requirements/dev.in
backend/.venv/bin/python -m pip install -r backend/requirements/dev.txt
```

The development lock is constrained by the production lock. Review lock changes
and rerun verification before deploying dependency upgrades.
