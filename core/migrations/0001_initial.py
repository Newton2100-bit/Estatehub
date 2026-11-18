from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    initial = True
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(name='Profile', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('role', models.CharField(choices=[('tenant','Tenant'),('landlord','Landlord'),('worker','Worker'),('management','Management'),('admin','Admin')], default='tenant', max_length=20)),
            ('phone', models.CharField(blank=True, max_length=40)), ('bio', models.TextField(blank=True)), ('avatar', models.CharField(blank=True, max_length=255)),
            ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
        ]),
        migrations.CreateModel(name='Property', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('title', models.CharField(max_length=160)), ('location', models.CharField(max_length=180)), ('city', models.CharField(default='Seattle', max_length=80)),
            ('property_type', models.CharField(default='Apartment', max_length=60)), ('rent', models.DecimalField(decimal_places=2, max_digits=12)),
            ('bedrooms', models.PositiveIntegerField(default=2)), ('bathrooms', models.DecimalField(decimal_places=1, default=2, max_digits=4)), ('sqft', models.PositiveIntegerField(default=1200)),
            ('parking', models.PositiveIntegerField(default=1)), ('status', models.CharField(choices=[('Available','Available'),('Pending','Pending'),('Approved','Approved'),('Inspection','Inspection'),('Sold','Sold')], default='Available', max_length=30)),
            ('description', models.TextField(blank=True)), ('amenities', models.CharField(blank=True, max_length=500)), ('created_at', models.DateTimeField(auto_now_add=True)),
            ('owner', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='properties', to=settings.AUTH_USER_MODEL)),
        ]),
        migrations.CreateModel(name='MaintenanceRequest', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')), ('title', models.CharField(max_length=160)), ('property_name', models.CharField(max_length=160)),
            ('priority', models.CharField(choices=[('Urgent','Urgent'),('High','High'),('Normal','Normal')], default='Normal', max_length=30)), ('status', models.CharField(choices=[('New','New'),('In Progress','In Progress'),('Resolved','Resolved')], default='New', max_length=30)),
            ('category', models.CharField(default='Other', max_length=60)), ('description', models.TextField(blank=True)), ('created_at', models.DateTimeField(auto_now_add=True)),
            ('requester', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='maintenance_requests', to=settings.AUTH_USER_MODEL)),
        ]),
        migrations.CreateModel(name='Job', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')), ('title', models.CharField(max_length=160)), ('location', models.CharField(max_length=180)), ('trade', models.CharField(max_length=80)),
            ('payout', models.DecimalField(decimal_places=2, max_digits=10)), ('urgent', models.BooleanField(default=False)), ('description', models.TextField(blank=True)), ('status', models.CharField(default='Open', max_length=30)), ('created_at', models.DateTimeField(auto_now_add=True)),
            ('worker', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='jobs', to=settings.AUTH_USER_MODEL)),
        ]),
        migrations.CreateModel(name='Payment', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')), ('entity', models.CharField(max_length=160)), ('kind', models.CharField(max_length=80)), ('amount', models.DecimalField(decimal_places=2, max_digits=12)), ('status', models.CharField(default='Completed', max_length=30)), ('created_at', models.DateTimeField(auto_now_add=True)),
            ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='payments', to=settings.AUTH_USER_MODEL)),
        ]),
        migrations.CreateModel(name='Lease', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')), ('start_date', models.DateField()), ('end_date', models.DateField()), ('rent', models.DecimalField(decimal_places=2, max_digits=10)), ('status', models.CharField(default='Active', max_length=30)), ('document_name', models.CharField(default='Original Lease Agreement.pdf', max_length=180)),
            ('property', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='leases', to='core.property')), ('tenant', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='leases', to=settings.AUTH_USER_MODEL)),
        ]),
        migrations.CreateModel(name='Booking', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')), ('date', models.DateTimeField()), ('status', models.CharField(default='Tour Scheduled', max_length=30)),
            ('property', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='core.property')), ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
        ]),
        migrations.CreateModel(name='Favorite', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')), ('property', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='core.property')), ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
        ], options={'unique_together': {('property','tenant')}}),
        migrations.CreateModel(name='Message', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')), ('subject', models.CharField(max_length=160)), ('body', models.TextField()), ('read', models.BooleanField(default=False)), ('created_at', models.DateTimeField(auto_now_add=True)),
            ('recipient', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='received_messages', to=settings.AUTH_USER_MODEL)), ('sender', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sent_messages', to=settings.AUTH_USER_MODEL)),
        ]),
        migrations.CreateModel(name='Notification', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')), ('text', models.CharField(max_length=255)), ('read', models.BooleanField(default=False)), ('created_at', models.DateTimeField(auto_now_add=True)), ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
        ]),
    ]
