from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("common", "0002_contact_notes_url"),
    ]

    operations = [
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
