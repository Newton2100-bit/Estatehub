from django.db import migrations, models
import django.core.validators


class Migration(migrations.Migration):
    dependencies = [("core", "0015_payment_dedupe_key")]

    operations = [
        migrations.AddField(
            model_name="lease",
            name="rent_due_day",
            field=models.PositiveSmallIntegerField(default=1, validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(31)]),
        ),
    ]
