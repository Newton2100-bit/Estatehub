from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0014_payment_booking")]

    operations = [
        migrations.AddField(
            model_name="payment",
            name="dedupe_key",
            field=models.CharField(blank=True, max_length=100, null=True, unique=True),
        ),
    ]
