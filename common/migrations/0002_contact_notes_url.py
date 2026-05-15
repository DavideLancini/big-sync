from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("common", "0001_contact_cache"),
    ]

    operations = [
        migrations.AddField(
            model_name="contact",
            name="notes_url",
            field=models.URLField(blank=True, max_length=500),
        ),
    ]
