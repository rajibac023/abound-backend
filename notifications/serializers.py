from rest_framework import serializers

from notifications.choices import NotificationChannel


class NotificationPreferenceSerializer(serializers.Serializer):
    channel = serializers.ChoiceField(choices=NotificationChannel.choices)
    enabled = serializers.BooleanField()
