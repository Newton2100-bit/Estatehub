from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0012_property_county")]

    operations = [
        migrations.AddField("payment", "payer_phone", models.CharField(blank=True, max_length=20)),
        migrations.AddField("payment", "checkout_request_id", models.CharField(blank=True, max_length=100, null=True, unique=True)),
        migrations.AddField("payment", "merchant_request_id", models.CharField(blank=True, max_length=100)),
        migrations.AddField("payment", "provider_conversation_id", models.CharField(blank=True, db_index=True, max_length=100)),
        migrations.AddField("payment", "provider_receipt", models.CharField(blank=True, max_length=40)),
        migrations.AddField("payment", "provider_result_code", models.CharField(blank=True, max_length=20)),
        migrations.AddField("payment", "provider_result_description", models.CharField(blank=True, max_length=255)),
        migrations.AddField("payment", "provider_initiated_at", models.DateTimeField(blank=True, null=True)),
        migrations.AddField("payment", "billing_period", models.DateField(blank=True, db_index=True, null=True)),
    ]
