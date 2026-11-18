from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("core", "0013_payment_daraja_fields")]

    operations = [
        migrations.AddField(
            model_name="payment",
            name="booking",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="payments", to="core.booking"),
        ),
    ]
