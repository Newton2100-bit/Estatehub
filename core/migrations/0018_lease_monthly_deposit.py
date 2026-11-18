from decimal import Decimal

from django.db import migrations, models


def create_initial_deposits(apps, schema_editor):
    Lease = apps.get_model("core", "Lease")
    Payment = apps.get_model("core", "Payment")
    db_alias = schema_editor.connection.alias

    for lease in Lease.objects.using(db_alias).all().iterator():
        lease.deposit_amount = lease.rent
        lease.save(using=db_alias, update_fields=["deposit_amount"])
        if lease.status != "Active":
            continue
        if Payment.objects.using(db_alias).filter(lease_id=lease.pk, kind="Security deposit").exists():
            continue
        unit = lease.unit
        Payment.objects.using(db_alias).create(
            entity=f"{lease.property.title} · Unit {unit.unit_number if unit else '1'} · Security deposit",
            kind="Security deposit",
            amount=lease.deposit_amount or Decimal("0"),
            status="Due",
            user_id=lease.tenant_id,
            lease_id=lease.pk,
            dedupe_key=f"deposit:{lease.pk}",
        )


class Migration(migrations.Migration):
    dependencies = [("core", "0017_leasechangerequest")]

    operations = [
        migrations.AddField(
            model_name="lease",
            name="deposit_amount",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=10),
        ),
        migrations.RunPython(create_initial_deposits, migrations.RunPython.noop),
    ]
