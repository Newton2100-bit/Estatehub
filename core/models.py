from django.db import models
from django.contrib.auth.models import User
from django.core.validators import MaxValueValidator, MinValueValidator
from .locations import KENYA_COUNTIES

class Profile(models.Model):
    ROLE_CHOICES=[("tenant","Tenant"),("landlord","Landlord"),("worker","Worker"),("management","Caretaker"),("admin","Admin")]
    user=models.OneToOneField(User,on_delete=models.CASCADE)
    role=models.CharField(max_length=20,choices=ROLE_CHOICES,default="tenant")
    approved=models.BooleanField(default=True)
    approved_at=models.DateTimeField(null=True,blank=True)
    rejected_at=models.DateTimeField(null=True,blank=True)
    approved_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL,related_name="approved_profiles")
    created_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL,related_name="created_caretaker_profiles")
    must_change_password=models.BooleanField(default=False)
    phone=models.CharField(max_length=40,blank=True)
    bio=models.TextField(blank=True)
    avatar=models.CharField(max_length=255,blank=True)
    def __str__(self): return f"{self.user.username} ({self.role})"

class Property(models.Model):
    STATUS=[("Pending","Pending review"),("Available","Available"),("Rented","Rented"),("Rejected","Rejected"),("Inspection","Inspection"),("Sold","Sold")]
    RENTAL_MODE_CHOICES=[("long_term","Apartment / long-term rental"),("short_stay","Short-stay / Airbnb")]
    title=models.CharField(max_length=160)
    location=models.CharField(max_length=180)
    county=models.CharField(max_length=40,choices=[(county, county) for county in KENYA_COUNTIES],default="",blank=True)
    city=models.CharField(max_length=80,default="Seattle")
    property_type=models.CharField(max_length=60,default="Apartment")
    rental_mode=models.CharField(max_length=20,choices=RENTAL_MODE_CHOICES,default="long_term")
    rent=models.DecimalField(max_digits=12,decimal_places=2)
    bedrooms=models.PositiveIntegerField(default=2)
    bathrooms=models.DecimalField(max_digits=4,decimal_places=1,default=2)
    sqft=models.PositiveIntegerField(default=1200)
    parking=models.PositiveIntegerField(default=1)
    status=models.CharField(max_length=30,choices=STATUS,default="Available")
    description=models.TextField(blank=True)
    amenities=models.CharField(max_length=500,blank=True)
    owner=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL,related_name="properties")
    caretaker=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL,related_name="managed_properties",limit_choices_to={"profile__role":"management"})
    created_at=models.DateTimeField(auto_now_add=True)
    def __str__(self): return self.title
    @property
    def available_unit_count(self):
        return sum(unit.status == "Available" for floor in self.floors.all() for unit in floor.units.all())
    @property
    def starting_rent(self):
        rents=[unit.rent for floor in self.floors.all() for unit in floor.units.all() if unit.status == "Available"]
        return min(rents) if rents else self.rent

class Floor(models.Model):
    property=models.ForeignKey(Property,on_delete=models.CASCADE,related_name="floors")
    number=models.PositiveIntegerField()
    label=models.CharField(max_length=80,blank=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=["property","number"],name="unique_floor_per_property")]
        ordering=["number"]
    def __str__(self): return self.label or f"Floor {self.number} — {self.property.title}"

class RentalUnit(models.Model):
    STATUS=[("Pending","Pending review"),("Available","Available"),("Rented","Rented"),("Unavailable","Unavailable")]
    floor=models.ForeignKey(Floor,on_delete=models.CASCADE,related_name="units")
    unit_number=models.CharField(max_length=40)
    rent=models.DecimalField(max_digits=12,decimal_places=2)
    bedrooms=models.PositiveIntegerField(default=2)
    bathrooms=models.DecimalField(max_digits=4,decimal_places=1,default=2)
    sqft=models.PositiveIntegerField(default=1200)
    parking=models.PositiveIntegerField(default=0)
    status=models.CharField(max_length=30,choices=STATUS,default="Available")
    description=models.TextField(blank=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=["floor","unit_number"],name="unique_unit_per_floor")]
        ordering=["floor__number","unit_number"]
    def __str__(self): return f"{self.floor.property.title} · {self.floor.label or 'Floor ' + str(self.floor.number)} · Unit {self.unit_number}"
    @property
    def property(self): return self.floor.property

class PropertyImage(models.Model):
    property=models.ForeignKey(Property,on_delete=models.CASCADE,related_name="images")
    image=models.FileField(upload_to="property_photos/%Y/%m/")
    caption=models.CharField(max_length=160,blank=True)
    is_demo=models.BooleanField(default=False)
    created_at=models.DateTimeField(auto_now_add=True)
    def __str__(self): return self.caption or f"Photo for {self.property}"

class MaintenanceRequest(models.Model):
    PRIORITY=[("Urgent","Urgent"),("High","High"),("Normal","Normal")]
    STATUS=[("New","New"),("In Progress","In Progress"),("Resolved","Resolved")]
    title=models.CharField(max_length=160)
    property_name=models.CharField(max_length=160)
    priority=models.CharField(max_length=30,choices=PRIORITY,default="Normal")
    status=models.CharField(max_length=30,choices=STATUS,default="New")
    category=models.CharField(max_length=60,default="Other")
    description=models.TextField(blank=True)
    property=models.ForeignKey("Property",null=True,blank=True,on_delete=models.CASCADE,related_name="maintenance_requests")
    lease=models.ForeignKey("Lease",null=True,blank=True,on_delete=models.SET_NULL,related_name="maintenance_requests")
    unit=models.ForeignKey("RentalUnit",null=True,blank=True,on_delete=models.SET_NULL,related_name="maintenance_requests")
    requester=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL,related_name="maintenance_requests")
    created_at=models.DateTimeField(auto_now_add=True)

class Job(models.Model):
    title=models.CharField(max_length=160)
    location=models.CharField(max_length=180)
    trade=models.CharField(max_length=80)
    payout=models.DecimalField(max_digits=10,decimal_places=2)
    urgent=models.BooleanField(default=False)
    description=models.TextField(blank=True)
    STATUS=[("Open","Open"),("Assigned","Assigned"),("In Progress","In Progress"),("Completed","Completed"),("Cancelled","Cancelled")]
    status=models.CharField(max_length=30,choices=STATUS,default="Open")
    worker=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL,related_name="jobs")
    property=models.ForeignKey("Property",null=True,blank=True,on_delete=models.CASCADE,related_name="jobs")
    unit=models.ForeignKey("RentalUnit",null=True,blank=True,on_delete=models.SET_NULL,related_name="jobs")
    request=models.OneToOneField(MaintenanceRequest,null=True,blank=True,on_delete=models.SET_NULL,related_name="job")
    posted_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL,related_name="posted_jobs")
    created_at=models.DateTimeField(auto_now_add=True)

class WorkerJobRequest(models.Model):
    STATUS=[("Pending","Awaiting review"),("Approved","Approved"),("Rejected","Rejected")]
    job=models.ForeignKey(Job,on_delete=models.CASCADE,related_name="worker_requests")
    worker=models.ForeignKey(User,on_delete=models.CASCADE,related_name="job_requests")
    status=models.CharField(max_length=20,choices=STATUS,default="Pending")
    created_at=models.DateTimeField(auto_now_add=True)
    reviewed_at=models.DateTimeField(null=True,blank=True)
    reviewed_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL,related_name="reviewed_worker_job_requests")
    class Meta:
        ordering=["created_at"]
        constraints=[models.UniqueConstraint(fields=["job","worker"],condition=models.Q(status="Pending"),name="unique_pending_worker_job_request")]
    def __str__(self): return f"{self.worker.username} requests {self.job.title} ({self.status})"

class Payment(models.Model):
    entity=models.CharField(max_length=160)
    kind=models.CharField(max_length=80)
    amount=models.DecimalField(max_digits=12,decimal_places=2)
    status=models.CharField(max_length=30,default="Completed")
    user=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL,related_name="payments")
    lease=models.ForeignKey("Lease",null=True,blank=True,on_delete=models.SET_NULL,related_name="payments")
    booking=models.ForeignKey("Booking",null=True,blank=True,on_delete=models.SET_NULL,related_name="payments")
    job=models.OneToOneField("Job",null=True,blank=True,on_delete=models.SET_NULL,related_name="payout_record")
    paid_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL,related_name="processed_worker_payouts")
    paid_at=models.DateTimeField(null=True,blank=True)
    payment_method=models.CharField(max_length=40,blank=True)
    payment_reference=models.CharField(max_length=100,blank=True)
    payer_phone=models.CharField(max_length=20,blank=True)
    checkout_request_id=models.CharField(max_length=100,null=True,blank=True,unique=True)
    merchant_request_id=models.CharField(max_length=100,blank=True)
    provider_conversation_id=models.CharField(max_length=100,blank=True,db_index=True)
    provider_receipt=models.CharField(max_length=40,blank=True)
    provider_result_code=models.CharField(max_length=20,blank=True)
    provider_result_description=models.CharField(max_length=255,blank=True)
    provider_initiated_at=models.DateTimeField(null=True,blank=True)
    billing_period=models.DateField(null=True,blank=True,db_index=True)
    dedupe_key=models.CharField(max_length=100,null=True,blank=True,unique=True)
    created_at=models.DateTimeField(auto_now_add=True)

class RentalApplication(models.Model):
    STATUS=[("Pending","Pending"),("Approved","Approved"),("Rejected","Rejected")]
    property=models.ForeignKey(Property,on_delete=models.CASCADE,related_name="applications")
    tenant=models.ForeignKey(User,on_delete=models.CASCADE,related_name="rental_applications")
    unit=models.ForeignKey("RentalUnit",null=True,blank=True,on_delete=models.SET_NULL,related_name="applications")
    status=models.CharField(max_length=20,choices=STATUS,default="Pending")
    message=models.TextField(blank=True)
    created_at=models.DateTimeField(auto_now_add=True)
    reviewed_at=models.DateTimeField(null=True,blank=True)
    class Meta:
        constraints=[
            models.UniqueConstraint(fields=["property","tenant"],condition=models.Q(unit__isnull=True),name="unique_legacy_rental_application"),
            models.UniqueConstraint(fields=["unit","tenant"],condition=models.Q(unit__isnull=False),name="unique_unit_rental_application"),
        ]

class Lease(models.Model):
    property=models.ForeignKey(Property,on_delete=models.CASCADE,related_name="leases")
    tenant=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL,related_name="leases")
    unit=models.ForeignKey("RentalUnit",null=True,blank=True,on_delete=models.SET_NULL,related_name="leases")
    start_date=models.DateField()
    end_date=models.DateField()
    rent=models.DecimalField(max_digits=10,decimal_places=2)
    deposit_amount=models.DecimalField(max_digits=10,decimal_places=2,default=0)
    rent_due_day=models.PositiveSmallIntegerField(default=1,validators=[MinValueValidator(1),MaxValueValidator(31)])
    status=models.CharField(max_length=30,default="Active")
    document_name=models.CharField(max_length=180,default="Original Lease Agreement.pdf")
    application=models.OneToOneField(RentalApplication,null=True,blank=True,on_delete=models.SET_NULL,related_name="lease")

class LeaseChangeRequest(models.Model):
    TYPE_CHOICES=[("renewal","Renewal"),("move_out","Move-out")]
    STATUS_CHOICES=[("Pending","Pending"),("Approved","Approved"),("Declined","Declined")]
    lease=models.ForeignKey(Lease,on_delete=models.CASCADE,related_name="change_requests")
    request_type=models.CharField(max_length=20,choices=TYPE_CHOICES)
    proposed_end_date=models.DateField()
    proposed_rent=models.DecimalField(max_digits=10,decimal_places=2,null=True,blank=True)
    message=models.TextField(blank=True)
    status=models.CharField(max_length=20,choices=STATUS_CHOICES,default="Pending")
    landlord_response=models.TextField(blank=True)
    created_at=models.DateTimeField(auto_now_add=True)
    reviewed_at=models.DateTimeField(null=True,blank=True)
    reviewed_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL,related_name="reviewed_lease_change_requests")
    class Meta:
        ordering=["-created_at"]
        constraints=[models.UniqueConstraint(fields=["lease"],condition=models.Q(status="Pending"),name="unique_pending_lease_change_request")]
    def __str__(self): return f"{self.get_request_type_display()} request for lease {self.lease_id} ({self.status})"

class Booking(models.Model):
    STATUS_CHOICES=[("Pending","Awaiting host review"),("Confirmed","Confirmed"),("Checked in","Checked in"),("Checked out","Checked out"),("Declined","Declined"),("Cancelled","Cancelled")]
    property=models.ForeignKey(Property,on_delete=models.CASCADE)
    tenant=models.ForeignKey(User,on_delete=models.CASCADE)
    date=models.DateTimeField()
    unit=models.ForeignKey("RentalUnit",null=True,blank=True,on_delete=models.SET_NULL,related_name="bookings")
    check_in=models.DateField(null=True,blank=True)
    check_out=models.DateField(null=True,blank=True)
    guests=models.PositiveSmallIntegerField(default=1)
    nightly_rate=models.DecimalField(max_digits=12,decimal_places=2,null=True,blank=True)
    total_price=models.DecimalField(max_digits=12,decimal_places=2,null=True,blank=True)
    status=models.CharField(max_length=30,choices=STATUS_CHOICES,default="Pending")

class Favorite(models.Model):
    property=models.ForeignKey(Property,on_delete=models.CASCADE)
    tenant=models.ForeignKey(User,on_delete=models.CASCADE)
    class Meta: unique_together=("property","tenant")

class Message(models.Model):
    sender=models.ForeignKey(User,on_delete=models.CASCADE,related_name="sent_messages")
    recipient=models.ForeignKey(User,on_delete=models.CASCADE,related_name="received_messages")
    subject=models.CharField(max_length=160)
    body=models.TextField()
    read=models.BooleanField(default=False)
    created_at=models.DateTimeField(auto_now_add=True)

class Notification(models.Model):
    user=models.ForeignKey(User,on_delete=models.CASCADE)
    text=models.CharField(max_length=255)
    url=models.CharField(max_length=255,blank=True)
    read=models.BooleanField(default=False)
    created_at=models.DateTimeField(auto_now_add=True)
