from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("common", "0003_writelog"),
    ]

    operations = [
        migrations.CreateModel(
            name="ActiveSession",
            fields=[
                ("id", models.IntegerField(primary_key=True, default=1, editable=False)),
                ("session_key", models.CharField(max_length=64, blank=True, default="")),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
    ]
