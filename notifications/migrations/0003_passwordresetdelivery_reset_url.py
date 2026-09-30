from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('notifications', '0002_notification_notificationpreference'),
    ]

    operations = [
        migrations.AddField(
            model_name='passwordresetdelivery',
            name='reset_url',
            field=models.TextField(blank=True, default=''),
        ),
    ]
