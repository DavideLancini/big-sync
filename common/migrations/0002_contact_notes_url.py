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
        migrations.CreateModel(
            name="WriteLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("type", models.CharField(choices=[("contact", "Contact"), ("event", "Event"), ("task", "Task")], db_index=True, max_length=20)),
                ("title", models.CharField(max_length=255)),
                ("detail", models.CharField(blank=True, max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
