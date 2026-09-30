import uuid

import django.db.models.deletion
import django.db.models.functions.text
import django.utils.timezone
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import migrations, models

import accounts.managers


def prepare_identities(apps, schema_editor):
    user_model = apps.get_model("accounts", "User")
    users = list(user_model.objects.using(schema_editor.connection.alias).all())
    identities = set()
    for user in users:
        identity = user.email.strip().lower()
        try:
            validate_email(identity)
        except ValidationError as exc:
            raise RuntimeError(
                "Email migration requires valid emails on all existing accounts. "
                "Correct the records before retrying; no identity is fabricated."
            ) from exc
        if identity in identities:
            raise RuntimeError("Email migration requires unique normalized emails on existing accounts.")
        identities.add(identity)
    for user in users:
        user.email = user.email.strip().lower()
        user.uuid = uuid.uuid4()
        user.role = "admin" if user.is_superuser else "staff" if user.is_staff else "customer"
        user.save(using=schema_editor.connection.alias, update_fields=["email", "uuid", "role"])


class Migration(migrations.Migration):
    dependencies = [("accounts", "0001_initial")]

    operations = [
        migrations.AddField("user", "uuid", models.UUIDField(null=True, editable=False)),
        migrations.AddField("user", "phone", models.CharField(blank=True, max_length=32)),
        migrations.AddField(
            "user", "role",
            models.CharField(choices=[("customer", "Customer"), ("staff", "Staff"), ("admin", "Administrator")], default="customer", max_length=16),
        ),
        migrations.RenameField("user", "date_joined", "created_at"),
        migrations.AlterField("user", "created_at", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
        migrations.AddField("user", "updated_at", models.DateTimeField(auto_now=True)),
        # Deliberately irreversible: usernames are retired only after email preflight succeeds.
        migrations.RunPython(prepare_identities),
        migrations.AlterField("user", "uuid", models.UUIDField(default=uuid.uuid4, unique=True, editable=False)),
        migrations.AlterField("user", "email", models.EmailField(max_length=254, unique=True)),
        migrations.RemoveField("user", "username"),
        migrations.AlterModelManagers("user", [("objects", accounts.managers.UserManager())]),
        migrations.AlterModelOptions("user", {}),
        migrations.AddConstraint(
            "user", models.CheckConstraint(
                condition=models.Q(email=django.db.models.functions.text.Lower(django.db.models.functions.text.Trim("email"))) & ~models.Q(email=""),
                name="accounts_user_email_canonical",
            ),
        ),
        migrations.AddConstraint("user", models.CheckConstraint(condition=models.Q(role__in=["customer", "staff", "admin"]), name="accounts_user_role_valid")),
        migrations.CreateModel(
            name="AuthSession",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("client", models.CharField(max_length=8, choices=[("mobile", "Mobile"), ("web", "Web")])),
                ("refresh_digest", models.CharField(max_length=64)),
                ("password_fingerprint", models.CharField(max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("revoked_at", models.DateTimeField(null=True, blank=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="AuthRateLimit",
            fields=[
                ("key", models.CharField(primary_key=True, max_length=64, serialize=False)),
                ("count", models.PositiveIntegerField(default=0)),
                ("expires_at", models.DateTimeField(db_index=True)),
            ],
        ),
    ]
