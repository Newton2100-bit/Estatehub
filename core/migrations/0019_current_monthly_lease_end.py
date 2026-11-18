import calendar
from datetime import date, timedelta

from django.db import migrations
from django.utils import timezone


def add_months(value, months):
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, min(value.day, calendar.monthrange(year, month)[1]))


def sync_active_lease_ends(apps, schema_editor):
    Lease = apps.get_model("core", "Lease")
    LeaseChangeRequest = apps.get_model("core", "LeaseChangeRequest")
    db_alias = schema_editor.connection.alias
    today = timezone.localdate()

    for lease in Lease.objects.using(db_alias).filter(status="Active").iterator():
        if LeaseChangeRequest.objects.using(db_alias).filter(
            lease_id=lease.pk, request_type="move_out", status="Approved"
        ).exists():
            continue

        elapsed = max(0, (today.year - lease.start_date.year) * 12 + today.month - lease.start_date.month)
        if add_months(lease.start_date, elapsed) > today:
            elapsed = max(0, elapsed - 1)
        lease.end_date = add_months(lease.start_date, elapsed + 1) - timedelta(days=1)
        lease.save(using=db_alias, update_fields=["end_date"])


class Migration(migrations.Migration):
    dependencies = [("core", "0018_lease_monthly_deposit")]

    operations = [migrations.RunPython(sync_active_lease_ends, migrations.RunPython.noop)]
