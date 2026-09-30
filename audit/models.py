from django.conf import settings
from django.db import models


class AuditEvent(models.Model):
    """Security events contain identifiers and bounded changes, never credentials.

    Deliberately append-only: no view or admin surface in this codebase exposes update
    or delete for this model (see audit/views.py, a read-only ListAPIView), and no
    Permission grants write access to it.
    """

    action = models.CharField(max_length=48)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    session_id = models.UUIDField(null=True)
    request_id = models.UUIDField(null=True)
    entity_type = models.CharField(max_length=32, blank=True, db_index=True)
    subject_uuid = models.UUIDField(null=True, db_index=True)
    previous_value = models.JSONField(null=True, blank=True)
    new_value = models.JSONField(null=True, blank=True)
    changes = models.JSONField(default=dict)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
