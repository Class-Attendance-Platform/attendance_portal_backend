from django.db import migrations

def mark_existing_users_verified(apps, schema_editor):
    User = apps.get_model('users', 'User')
    User.objects.filter(is_verified=False).update(is_verified=True)

class Migration(migrations.Migration):
    dependencies = [
        ("users", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(mark_existing_users_verified, migrations.RunPython.noop),
    ]
