from django.db import migrations


def seed_features(apps, schema_editor):
    Feature = apps.get_model("vehicles", "VehicleFeature")
    for name in (
        "Air Conditioning", "Bluetooth", "USB", "GPS", "Rear Camera", "Parking Sensors",
        "Cruise Control", "Sunroof", "Leather Seats", "Child Seat", "Android Auto",
        "Apple CarPlay", "ABS", "Airbags", "Power Windows", "Central Locking",
    ):
        Feature.objects.using(schema_editor.connection.alias).get_or_create(name=name)


class Migration(migrations.Migration):
    dependencies = [("vehicles", "0002_imagefiledeletion_vehiclefeature_vehicle_features_and_more")]
    # Preserve catalog edits and assignments if the reference-data migration is reversed.
    operations = [migrations.RunPython(seed_features, reverse_code=migrations.RunPython.noop)]
