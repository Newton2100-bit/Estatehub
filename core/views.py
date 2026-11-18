from datetime import date, datetime, timedelta
import calendar
import csv
from decimal import Decimal, InvalidOperation
from functools import wraps
import hmac
import json

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.models import User
from django.contrib.auth import update_session_auth_hash
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, Min, Prefetch, Q, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.utils.crypto import get_random_string

from .forms import ApplicationForm, CaretakerAssignmentForm, CaretakerCreateForm, JobForm, MaintenanceForm, ProfileDetailsForm, PropertyForm, RegisterForm, RentalUnitEditForm, RentalUnitForm
from .caretaker_forms import CaretakerJobForm
from .models import Booking, Floor, Job, Lease, LeaseChangeRequest, MaintenanceRequest, Notification, Payment, Profile, Property, PropertyImage, RentalApplication, RentalUnit, WorkerJobRequest
from .locations import KENYA_COUNTIES
from .notifications import notify_admins, notify_user
from . import daraja


def current_role(user):
    if user.is_superuser:
        return "admin"
    try:
        return user.profile.role
    except Profile.DoesNotExist:
        return None


def role_required(*allowed_roles):
    def decorate(view):
        @wraps(view)
        @login_required
        def wrapped(request, *args, **kwargs):
            role = current_role(request.user)
            if role not in allowed_roles:
                raise PermissionDenied
            if role == "landlord" and not request.user.profile.approved:
                if request.user.profile.rejected_at:
                    messages.warning(request, "Your landlord account was not approved. Check your notifications for details.")
                else:
                    messages.warning(request, "Your landlord account is awaiting admin approval.")
                return redirect("dashboard_home")
            return view(request, *args, **kwargs)
        return wrapped
    return decorate


def admin_required(view):
    return role_required("admin")(view)


def ctx(**values):
    return values


def add_months(value, months):
    """Add calendar months while clamping to the target month's final day."""
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, min(value.day, calendar.monthrange(year, month)[1]))


def current_month_term_end(lease, today=None):
    """Return the end date of an active month-to-month term."""
    return lease.monthly_term_end(today)


def lease_has_approved_moveout(lease):
    changes = lease.change_requests.all()
    if hasattr(lease, "_prefetched_objects_cache") and "change_requests" in lease._prefetched_objects_cache:
        return any(change.request_type == "move_out" and change.status == "Approved" for change in changes)
    return changes.filter(request_type="move_out", status="Approved").exists()


def lease_last_billable_period(lease, today=None):
    today = today or timezone.localdate()
    current_period = today.replace(day=1)
    if lease.status == "Active" and not lease_has_approved_moveout(lease):
        return current_period
    return min(current_period, lease.end_date.replace(day=1))


def build_rent_schedule(leases, today=None, include_paid_history=False):
    """Build monthly rent obligations; active leases renew until move-out is approved."""
    today = today or timezone.localdate()
    current_period = today.replace(day=1)
    leases = list(leases)
    lease_ids = [lease.pk for lease in leases]
    latest_payments = {}
    paid_periods = set()
    for payment in Payment.objects.filter(
        lease_id__in=lease_ids,
        kind="Rent payment",
        billing_period__isnull=False,
    ).order_by("-created_at"):
        key = (payment.lease_id, payment.billing_period)
        latest_payments.setdefault(key, payment)
        if payment.status == "Completed":
            paid_periods.add(key)

    schedule = []
    for lease in leases:
        period = lease.start_date.replace(day=1)
        last_period = lease_last_billable_period(lease, today)
        while period <= last_period:
            month_last_day = calendar.monthrange(period.year, period.month)[1]
            due_day = min(lease.rent_due_day, month_last_day)
            due_date = date(period.year, period.month, due_day)
            if period == lease.start_date.replace(day=1) and due_date < lease.start_date:
                due_date = lease.start_date
            payment = latest_payments.get((lease.pk, period))

            if (lease.pk, period) in paid_periods:
                status = "Paid"
            elif payment and payment.status in {"Initiating", "Pending"}:
                status = "Payment pending"
            elif payment and payment.status in {"Review required", "Initiation status unknown"}:
                status = "Needs payment review"
            elif due_date < today:
                status = "Overdue"
            elif due_date == today:
                status = "Due today"
            else:
                status = "Upcoming"

            if include_paid_history or status != "Paid" or period == current_period:
                schedule.append({
                    "lease": lease,
                    "period": period,
                    "period_label": period.strftime("%B %Y"),
                    "due_date": due_date,
                    "amount": lease.rent,
                    "status": status,
                    "payment": payment,
                    "payable": status in {"Overdue", "Due today", "Upcoming"},
                })
            if period.month == 12:
                period = date(period.year + 1, 1, 1)
            else:
                period = date(period.year, period.month + 1, 1)
    schedule.sort(key=lambda item: (item["due_date"], item["lease"].pk))
    return schedule


def admin_account_counts():
    recognized_roles = ["tenant", "landlord", "worker", "management", "admin"]
    return {
        "tenants": Profile.objects.filter(role="tenant", user__is_superuser=False).count(),
        "landlords": Profile.objects.filter(role="landlord", user__is_superuser=False).count(),
        "workers": Profile.objects.filter(role="worker", user__is_superuser=False).count(),
        "caretakers": Profile.objects.filter(role="management", user__is_superuser=False).count(),
        "admins": (
            Profile.objects.filter(role="admin", user__is_superuser=False).count()
            + User.objects.filter(is_superuser=True).count()
        ),
        "other_accounts": (
            Profile.objects.exclude(role__in=recognized_roles).exclude(user__is_superuser=True).count()
            + User.objects.filter(profile__isnull=True, is_superuser=False).count()
        ),
    }


def landing(request):
    return render(request, "landing.html")


def login_view(request):
    if request.method == "POST":
        identifier = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        user = authenticate(request, username=identifier, password=password)
        if user is None and "@" in identifier:
            email_matches = User.objects.filter(email__iexact=identifier)
            if email_matches.count() == 1:
                user = authenticate(request, username=email_matches.first().username, password=password)
        if user:
            login(request, user)
            if getattr(getattr(user, "profile", None), "must_change_password", False):
                return redirect("force_password_change")
            return redirect("dashboard_home")
        messages.error(request, "Invalid username or password.")
    return render(request, "auth/login.html")


def register(request):
    if request.method == "POST":
        form = RegisterForm(request.POST)
        if form.is_valid():
            user = form.save()
            if user.profile.role == "landlord":
                notify_admins(f"Landlord account @{user.username} is waiting for review.")
                notify_user(user, "Your landlord account is waiting for admin review.", "/dashboard/")
            login(request, user)
            return redirect("setup")
    else:
        form = RegisterForm()
    return render(request, "auth/register.html", {"form": form})


@login_required
def force_password_change(request):
    profile = getattr(request.user, "profile", None)
    if not profile or not profile.must_change_password:
        return redirect("dashboard_home")
    form = PasswordChangeForm(user=request.user, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        profile.must_change_password = False
        profile.save(update_fields=["must_change_password"])
        update_session_auth_hash(request, request.user)
        messages.success(request, "Password updated. Your caretaker account is ready.")
        return redirect("dashboard_home")
    return render(request, "auth/force_password_change.html", {"form": form})


def role(request):
    return render(request, "auth/role.html")


@login_required
def setup(request):
    profile = request.user.profile
    if request.method == "POST":
        profile.phone = request.POST.get("phone", "")
        profile.bio = request.POST.get("bio", "")
        profile.save(update_fields=["phone", "bio"])
        return redirect("all_set")
    return render(request, "auth/setup.html", {"profile": profile})


@login_required
def all_set(request):
    return render(request, "auth/all_set.html")


def pricing(request): return render(request, "public/pricing.html")
def about(request): return render(request, "public/about.html")
def blog(request): return render(request, "public/blog.html")
def support(request): return render(request, "public/support.html")


@login_required
def dashboard(request, role=None):
    actual_role = current_role(request.user)
    if actual_role is None:
        raise PermissionDenied
    if role and role.lower() != actual_role:
        return redirect("dashboard_home")

    rejected_landlord = actual_role == "landlord" and bool(request.user.profile.rejected_at)
    pending_landlord = actual_role == "landlord" and not request.user.profile.approved and not rejected_landlord
    properties = Property.objects.none()
    requests = MaintenanceRequest.objects.none()
    jobs = Job.objects.none()
    units = RentalUnit.objects.none()
    payments = Payment.objects.none()
    payout_due_total = 0
    payout_paid_total = 0
    open_job_count = 0
    assigned_job_count = 0
    applications = RentalApplication.objects.none()
    leases = Lease.objects.none()
    rent_dues = []
    rent_due_total = 0
    rent_due_count = 0

    if actual_role == "tenant":
        properties = Property.objects.filter(status="Available", floors__units__status="Available").distinct().prefetch_related("images", "floors__units")[:3]
        leases = Lease.objects.filter(tenant=request.user, status="Active").select_related("property", "unit")
        rent_dues = build_rent_schedule(leases)
        rent_due_items = [item for item in rent_dues if item["status"] in {"Overdue", "Due today"}]
        rent_due_total = sum((item["amount"] for item in rent_due_items), start=Decimal("0"))
        rent_due_count = len(rent_due_items)
        applications = RentalApplication.objects.filter(tenant=request.user).select_related("property")
        requests = MaintenanceRequest.objects.filter(requester=request.user).select_related("property")
        payments = Payment.objects.filter(user=request.user).select_related("lease", "lease__property")
    elif actual_role == "landlord" and not pending_landlord and not rejected_landlord:
        properties = Property.objects.filter(owner=request.user).prefetch_related("images")
        requests = MaintenanceRequest.objects.filter(property__owner=request.user).select_related("property", "requester")
        jobs = Job.objects.filter(property__owner=request.user).select_related("property", "worker")
        leases = Lease.objects.filter(property__owner=request.user, status="Active").select_related("property", "tenant")
        payments = Payment.objects.filter(lease__property__owner=request.user).select_related("lease", "user")
        applications = RentalApplication.objects.filter(property__owner=request.user).select_related("property", "tenant")
    elif actual_role == "worker":
        jobs = Job.objects.filter(Q(status="Open") | Q(worker=request.user)).select_related("property")
        worker_payouts = Payment.objects.filter(job__worker=request.user, kind="Worker payout")
        payout_due_total = worker_payouts.filter(status="Awaiting payment").aggregate(total=Sum("amount"))["total"] or 0
        payout_paid_total = worker_payouts.filter(status="Paid").aggregate(total=Sum("amount"))["total"] or 0
        open_job_count = Job.objects.filter(status="Open", worker__isnull=True).count()
        assigned_job_count = Job.objects.filter(worker=request.user, status__in=["Assigned", "In Progress"]).count()
    elif actual_role == "management":
        properties = Property.objects.filter(caretaker=request.user).prefetch_related("images", "floors__units")
        units = RentalUnit.objects.filter(floor__property__caretaker=request.user).select_related("floor", "floor__property")
        requests = MaintenanceRequest.objects.filter(property__caretaker=request.user).select_related(
            "property", "requester", "job"
        ).order_by("-created_at")
        jobs = Job.objects.filter(property__caretaker=request.user).select_related("property", "worker").order_by("-created_at")
    elif actual_role == "admin":
        properties = Property.objects.all()
        requests = MaintenanceRequest.objects.all().select_related("property", "requester", "unit", "unit__floor")
        jobs = Job.objects.all().select_related("property", "worker")
        payments = Payment.objects.all()
        applications = RentalApplication.objects.all().select_related("property", "tenant")
        leases = Lease.objects.all().select_related("property", "tenant")

    if leases:
        report_today = timezone.localdate()
        for lease in leases:
            lease.current_term_end = (
                current_month_term_end(lease, report_today)
                if lease.status == "Active" and not lease_has_approved_moveout(lease)
                else lease.end_date
            )

    counts = {
        "users": User.objects.count(),
        "properties": Property.objects.count(),
        "leases": Lease.objects.filter(status="Active").count(),
        "open_requests": MaintenanceRequest.objects.exclude(status="Resolved").count(),
        **admin_account_counts(),
    } if actual_role == "admin" else {}

    templates = {
        "tenant": "dashboards/tenant.html",
        "landlord": "dashboards/landlord.html",
        "worker": "dashboards/worker.html",
        "management": "dashboards/management.html",
        "admin": "dashboards/admin.html",
    }
    return render(request, templates.get(actual_role, templates["tenant"]), ctx(
        role=actual_role, pending_landlord=pending_landlord, rejected_landlord=rejected_landlord, properties=properties,
        requests=requests, jobs=jobs, payments=payments, applications=applications, leases=leases,
        units=units,
        counts=counts, payout_due_total=payout_due_total, payout_paid_total=payout_paid_total,
        open_job_count=open_job_count, assigned_job_count=assigned_job_count,
        rent_dues=rent_dues, rent_due_total=rent_due_total, rent_due_count=rent_due_count,
    ))


def properties(request):
    query = request.GET.get("q", "").strip()
    property_type = request.GET.get("type", "").strip()
    rental_mode = request.GET.get("mode", "").strip()
    bedrooms = request.GET.get("bedrooms", "").strip()
    min_price = request.GET.get("min_price", "").strip()
    max_price = request.GET.get("max_price", "").strip()
    county = request.GET.get("county", "").strip()
    check_in = request.GET.get("check_in", "").strip()
    check_out = request.GET.get("check_out", "").strip()
    role = current_role(request.user) if request.user.is_authenticated else None
    units = RentalUnit.objects.all() if role == "landlord" else RentalUnit.objects.filter(status="Available")
    today = timezone.localdate()
    valid_dates = False
    try:
        requested_check_in = date.fromisoformat(check_in) if check_in else None
        requested_check_out = date.fromisoformat(check_out) if check_out else None
        valid_dates = bool(requested_check_in and requested_check_out and requested_check_in >= today and requested_check_out > requested_check_in)
    except ValueError:
        requested_check_in = requested_check_out = None
    if role != "landlord" and (check_in or check_out):
        if not valid_dates:
            messages.error(request, "Choose a check-in date from today and a check-out date after it.")
        else:
            units = units.exclude(bookings__status__in=["Pending", "Confirmed", "Checked in"], bookings__check_in__lt=requested_check_out, bookings__check_out__gt=requested_check_in)
    elif role != "landlord" and request.GET.get("mode") == "short_stay":
        units = units.exclude(bookings__status__in=["Pending", "Confirmed", "Checked in"], bookings__check_out__gt=today)
    if bedrooms:
        try:
            units = units.filter(bedrooms__gte=int(bedrooms))
        except ValueError:
            bedrooms = ""
    if min_price:
        try:
            units = units.filter(rent__gte=min_price)
        except (ValueError, TypeError):
            min_price = ""
    if max_price:
        try:
            units = units.filter(rent__lte=max_price)
        except (ValueError, TypeError):
            max_price = ""
    qs = Property.objects.filter(status="Available", floors__units__in=units).distinct().prefetch_related("images", "floors__units").order_by("-created_at")
    if role == "management":
        return redirect("dashboard_home")
    if role == "landlord":
        qs = Property.objects.filter(owner=request.user).prefetch_related("images", "floors__units").order_by("-created_at")
    if query:
        qs = qs.filter(Q(title__icontains=query) | Q(location__icontains=query) | Q(city__icontains=query))
    if property_type:
        qs = qs.filter(property_type=property_type)
    if county in KENYA_COUNTIES:
        qs = qs.filter(county__iexact=county)
    else:
        county = ""
    if rental_mode:
        qs = qs.filter(rental_mode=rental_mode)
    qs = list(qs)
    available_counts = {
        row["floor__property_id"]: row
        for row in RentalUnit.objects.filter(pk__in=units.values_list("pk", flat=True).distinct())
        .values("floor__property_id").annotate(total=Count("pk"), starting_price=Min("rent"), starting_bedrooms=Min("bedrooms"))
    }
    for prop in qs:
        availability = available_counts.get(prop.pk, {})
        prop.search_available_unit_count = availability.get("total", 0)
        prop.search_starting_price = availability.get("starting_price", prop.rent)
        prop.search_starting_bedrooms = availability.get("starting_bedrooms", prop.bedrooms)
    category_properties = Property.objects.filter(owner=request.user) if role == "landlord" else Property.objects.all()
    return render(request, "portal/properties.html", {
        "properties": qs, "query": query, "property_type": property_type, "rental_mode": rental_mode,
        "county": county, "kenya_counties": KENYA_COUNTIES,
        "bedrooms": bedrooms, "min_price": min_price, "max_price": max_price,
        "check_in": check_in, "check_out": check_out,
        "role": role,
        "property_types": category_properties.exclude(property_type="").values_list("property_type", flat=True).distinct().order_by("property_type"),
    })


def property_detail(request, pk):
    prop = get_object_or_404(Property.objects.prefetch_related("images", "floors__units"), pk=pk)
    is_owner = request.user.is_authenticated and prop.owner_id == request.user.id
    role = current_role(request.user) if request.user.is_authenticated else None
    tenant_lease = Lease.objects.filter(property=prop, tenant=request.user, status="Active").select_related("unit").first() if role == "tenant" else None
    is_tenant = tenant_lease is not None
    is_caretaker = request.user.is_authenticated and prop.caretaker_id == request.user.id
    if role == "management" and not is_caretaker:
        raise PermissionDenied
    if prop.status != "Available" and not is_owner and not is_caretaker and role != "admin" and not is_tenant:
        raise PermissionDenied
    application = None
    applied_unit_ids = []
    leased_unit_ids = []
    if role == "tenant":
        application = RentalApplication.objects.filter(property=prop, tenant=request.user).first()
        applied_unit_ids = list(RentalApplication.objects.filter(property=prop, tenant=request.user, unit__isnull=False).values_list("unit_id", flat=True))
        leased_unit_ids = list(Lease.objects.filter(property=prop, tenant=request.user, status="Active", unit__isnull=False).values_list("unit_id", flat=True))
    host_bookings = Booking.objects.filter(property=prop).select_related("tenant", "unit").order_by("check_in") if is_owner or role == "admin" else Booking.objects.none()
    guest_bookings = Booking.objects.filter(property=prop, tenant=request.user).select_related("unit").order_by("-check_in") if role == "tenant" else Booking.objects.none()
    today = timezone.localdate()
    occupied_stays = Booking.objects.filter(property=prop, status__in=["Confirmed", "Checked in"], check_out__gte=today).select_related("unit").order_by("check_in")
    return render(request, "portal/property_detail.html", {"property": prop, "application": application, "applied_unit_ids": applied_unit_ids, "leased_unit_ids": leased_unit_ids, "is_tenant": is_tenant, "tenant_lease": tenant_lease, "role": role, "host_bookings": host_bookings, "guest_bookings": guest_bookings, "occupied_stays": occupied_stays, "today": today, "tomorrow": today + timedelta(days=1)})


@role_required("tenant")
def apply_for_property(request, pk):
    if request.method != "POST":
        raise PermissionDenied
    prop = get_object_or_404(Property, pk=pk, status="Available", rental_mode="long_term")
    unit_id = request.POST.get("unit")
    unit_qs = RentalUnit.objects.filter(floor__property=prop, status="Available")
    unit = get_object_or_404(unit_qs, pk=unit_id) if unit_id else unit_qs.first()
    if unit is None:
        messages.error(request, "There are no available units in this property.")
        return redirect("property_detail", pk=pk)
    form = ApplicationForm(request.POST)
    if form.is_valid():
        application, created = RentalApplication.objects.get_or_create(
            unit=unit,
            tenant=request.user,
            defaults={"property": prop, "message": form.cleaned_data["message"]},
        )
        if not created:
            messages.info(request, "You have already applied for this unit.")
            return redirect("property_detail", pk=pk)
        if prop.owner_id:
            notify_user(prop.owner, f"{request.user.username} applied for Unit {unit.unit_number} at {prop.title}.", "/dashboard/landlord/")
        messages.success(request, f"Your application for Unit {unit.unit_number} was sent to the landlord.")
    else:
        messages.error(request, "Please check your application and try again.")
    return redirect("property_detail", pk=pk)


@role_required("landlord")
def submit_property(request):
    if request.method == "POST":
        form = PropertyForm(request.POST, request.FILES)
        if form.is_valid():
            prop = form.save(commit=False)
            prop.owner = request.user
            prop.status = "Pending"
            prop.save()
            floor = Floor.objects.create(property=prop, number=1, label="Ground floor")
            RentalUnit.objects.create(
                floor=floor, unit_number="1", rent=prop.rent, bedrooms=prop.bedrooms,
                bathrooms=prop.bathrooms, sqft=prop.sqft, parking=prop.parking,
                description=prop.description, status="Pending",
            )
            for image in form.cleaned_data["photos"]:
                PropertyImage.objects.create(property=prop, image=image)
            notify_admins(f"Property listing ‘{prop.title}’ is waiting for review.")
            notify_user(request.user, f"Your property ‘{prop.title}’ was submitted for admin review.", f"/property/{prop.pk}/")
            messages.success(request, "Property submitted for admin review.")
            return redirect("properties")
    else:
        form = PropertyForm()
    return render(request, "portal/submit_property.html", {"form": form, "kenya_counties": KENYA_COUNTIES})


@role_required("tenant")
def request_short_stay(request, pk):
    if request.method != "POST":
        raise PermissionDenied
    prop = get_object_or_404(Property, pk=pk, status="Available", rental_mode="short_stay")
    unit = get_object_or_404(RentalUnit, pk=request.POST.get("unit"), floor__property=prop, status="Available")
    try:
        check_in = date.fromisoformat(request.POST.get("check_in", ""))
        check_out = date.fromisoformat(request.POST.get("check_out", ""))
        guests = int(request.POST.get("guests", "1"))
    except (ValueError, TypeError):
        messages.error(request, "Choose valid check-in and check-out dates and guest count.")
        return redirect("property_detail", pk=pk)
    if check_in < timezone.localdate() or check_out <= check_in:
        messages.error(request, "Check-in must be today or later, and check-out must be after check-in.")
        return redirect("property_detail", pk=pk)
    if guests < 1 or guests > 30:
        messages.error(request, "Guest count must be between 1 and 30.")
        return redirect("property_detail", pk=pk)
    with transaction.atomic():
        locked_unit = RentalUnit.objects.select_for_update().get(pk=unit.pk)
        conflict = Booking.objects.filter(
            unit=locked_unit,
            status__in=["Pending", "Confirmed", "Checked in"],
            check_in__lt=check_out,
            check_out__gt=check_in,
        ).exists()
        if conflict:
            messages.error(request, "Those dates have a pending or confirmed stay. Choose different dates.")
            return redirect("property_detail", pk=pk)
        nights = (check_out - check_in).days
        Booking.objects.create(
            property=prop,
            unit=locked_unit,
            tenant=request.user,
            date=timezone.make_aware(datetime.combine(check_in, datetime.min.time())),
            check_in=check_in,
            check_out=check_out,
            guests=guests,
            nightly_rate=locked_unit.rent,
            total_price=locked_unit.rent * nights,
            status="Pending",
        )
    if prop.owner_id:
        notify_user(prop.owner, f"{request.user.get_full_name() or request.user.username} requested a {nights}-night stay at {prop.title} for {check_in:%b %d}–{check_out:%b %d}.", f"/property/{prop.pk}/")
    messages.success(request, f"Your {nights}-night request was sent to the host. It is not confirmed until approved.")
    return redirect("property_detail", pk=pk)


@role_required("landlord")
def manage_short_stay_booking(request, booking_id, action):
    if request.method != "POST":
        raise PermissionDenied
    booking = get_object_or_404(Booking.objects.select_related("property", "tenant"), pk=booking_id, property__owner=request.user, property__rental_mode="short_stay")
    allowed_transitions = {
        "confirm": ("Pending", "Confirmed"),
        "decline": ("Pending", "Declined"),
        "check-in": ("Confirmed", "Checked in"),
        "check-out": ("Checked in", "Checked out"),
    }
    transition = allowed_transitions.get(action)
    if not transition or booking.status != transition[0]:
        messages.error(request, "That stay can’t be changed to the selected status.")
        return redirect("property_detail", pk=booking.property_id)
    if action == "confirm":
        conflict = Booking.objects.filter(
            unit=booking.unit,
            status__in=["Confirmed", "Checked in"],
            check_in__lt=booking.check_out,
            check_out__gt=booking.check_in,
        ).exclude(pk=booking.pk).exists()
        if conflict:
            messages.error(request, "Another stay is already confirmed for those dates.")
            return redirect("property_detail", pk=booking.property_id)
    booking.status = transition[1]
    booking.save(update_fields=["status"])
    notify_user(booking.tenant, f"Your stay at {booking.property.title} ({booking.check_in:%b %d}–{booking.check_out:%b %d}) is now {booking.status.lower()}.", f"/property/{booking.property_id}/")
    messages.success(request, f"Stay updated: {booking.status}.")
    return redirect("property_detail", pk=booking.property_id)


@role_required("landlord")
def assign_caretaker(request, pk):
    prop = get_object_or_404(Property, pk=pk, owner=request.user)
    if request.method == "POST":
        previous_caretaker_id = prop.caretaker_id
        form = CaretakerAssignmentForm(request.POST, instance=prop, landlord=request.user)
        if form.is_valid():
            prop = form.save()
            if prop.caretaker_id and prop.caretaker_id != previous_caretaker_id:
                notify_user(prop.caretaker, f"You were assigned as caretaker for ‘{prop.title}’. Tenant reports for this property will appear in Maintenance.", "/maintenance/")
            messages.success(request, "Property caretaker assignment updated.")
            return redirect("dashboard_home")
    else:
        form = CaretakerAssignmentForm(instance=prop, landlord=request.user)
    return render(request, "portal/assign_caretaker.html", {"property": prop, "form": form})


@role_required("landlord")
def create_caretaker(request, pk):
    prop = get_object_or_404(Property, pk=pk, owner=request.user)
    form = CaretakerCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        temporary_password = get_random_string(18)
        user = User.objects.create_user(
            username=form.cleaned_data["username"],
            email=form.cleaned_data["email"],
            password=temporary_password,
            first_name=form.cleaned_data["first_name"],
            last_name=form.cleaned_data["last_name"],
        )
        profile = Profile.objects.create(
            user=user, role="management", approved=True,
            phone=form.cleaned_data["phone"], must_change_password=True, created_by=request.user,
        )
        prop.caretaker = user
        prop.save(update_fields=["caretaker"])
        notify_user(user, f"You have been added as caretaker for ‘{prop.title}’. Sign in and change your temporary password to get started.", "/login/")
        return render(request, "portal/caretaker_credentials.html", {
            "property": prop,
            "caretaker": user,
            "temporary_password": temporary_password,
        })
    return render(request, "portal/create_caretaker.html", {"property": prop, "form": form})


@role_required("landlord")
def reset_caretaker_password(request, pk):
    if request.method != "POST":
        raise PermissionDenied
    prop = get_object_or_404(
        Property.objects.select_related("caretaker__profile"),
        pk=pk, owner=request.user, caretaker__profile__role="management",
        caretaker__profile__created_by=request.user,
    )
    temporary_password = get_random_string(18)
    caretaker = prop.caretaker
    caretaker.set_password(temporary_password)
    caretaker.save(update_fields=["password"])
    caretaker.profile.must_change_password = True
    caretaker.profile.save(update_fields=["must_change_password"])
    return render(request, "portal/caretaker_credentials.html", {
        "property": prop,
        "caretaker": caretaker,
        "temporary_password": temporary_password,
        "credentials_renewed": True,
    })


@role_required("landlord")
def add_rental_unit(request, pk):
    prop = get_object_or_404(Property, pk=pk, owner=request.user)
    if prop.status in {"Rejected", "Sold"}:
        messages.error(request, "Units cannot be added to a rejected or sold property.")
        return redirect("dashboard_home")
    form = RentalUnitForm(request.POST or None, property=prop)
    if request.method == "POST" and form.is_valid():
        status = "Pending" if prop.status == "Pending" else "Available"
        unit = form.save(prop, status=status)
        if status == "Available" and prop.status == "Rented":
            prop.status = "Available"
            prop.save(update_fields=["status"])
        messages.success(request, f"Unit {unit.unit_number} was added on Floor {unit.floor.number}.")
        return redirect("property_detail", pk=prop.pk)
    return render(request, "portal/add_unit.html", {"property": prop, "form": form})


@role_required("landlord")
def edit_rental_unit(request, pk):
    unit = get_object_or_404(RentalUnit.objects.select_related("floor", "floor__property"), pk=pk, floor__property__owner=request.user)
    form = RentalUnitEditForm(request.POST or None, instance=unit)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"Unit {unit.unit_number} listing details updated. Existing lease rates stay as agreed in each lease.")
        return redirect("property_detail", pk=unit.floor.property_id)
    return render(request, "portal/edit_unit.html", {"unit": unit, "property": unit.floor.property, "form": form})


@role_required("landlord", "admin")
def review_application(request, application_id, decision):
    if request.method == "GET" and decision == "approve":
        application = get_object_or_404(
            RentalApplication.objects.select_related("property", "unit", "tenant"),
            pk=application_id,
        )
        if current_role(request.user) != "admin" and application.property.owner_id != request.user.id:
            raise PermissionDenied
        if application.status != "Pending" or application.property.status != "Available" or (
            application.unit_id and application.unit.status != "Available"
        ):
            messages.info(request, "This application is no longer pending for an available home.")
            return redirect("dashboard_home")
        return render(request, "portal/confirm_application_approval.html", {"application": application})
    if request.method != "POST" or decision not in {"approve", "reject"}:
        raise PermissionDenied
    application = get_object_or_404(RentalApplication.objects.select_related("property", "unit"), pk=application_id)
    if current_role(request.user) != "admin" and application.property.owner_id != request.user.id:
        raise PermissionDenied
    with transaction.atomic():
        prop = Property.objects.select_for_update().get(pk=application.property_id)
        unit = RentalUnit.objects.select_for_update().get(pk=application.unit_id) if application.unit_id else None
        if application.status != "Pending" or prop.status != "Available" or (unit and unit.status != "Available"):
            messages.error(request, "This application is no longer pending for an available property.")
            return redirect("dashboard_home")
        application.status = "Approved" if decision == "approve" else "Rejected"
        application.reviewed_at = timezone.now()
        application.save(update_fields=["status", "reviewed_at"])
        if decision == "approve":
            lease_start = timezone.localdate()
            rent = unit.rent if unit else prop.rent
            lease = Lease.objects.create(
                property=prop, unit=unit, tenant=application.tenant, application=application,
                start_date=lease_start, end_date=add_months(lease_start, 1) - timedelta(days=1),
                rent=rent, deposit_amount=rent, rent_due_day=lease_start.day,
            )
            Payment.objects.create(
                entity=f"{prop.title} · Unit {unit.unit_number if unit else '1'} · Security deposit",
                kind="Security deposit", amount=lease.deposit_amount, status="Due",
                user=application.tenant, lease=lease, dedupe_key=f"deposit:{lease.pk}",
            )
            if unit:
                unit.status = "Rented"
                unit.save(update_fields=["status"])
            if not RentalUnit.objects.filter(floor__property=prop, status="Available").exists():
                prop.status = "Rented"
                prop.save(update_fields=["status"])
            pending_applications = RentalApplication.objects.filter(property=prop, status="Pending")
            if unit:
                pending_applications = pending_applications.filter(unit=unit)
            pending_applications.exclude(pk=application.pk).update(
                status="Rejected", reviewed_at=timezone.now()
            )
            messages.success(request, "Application approved. A one-month lease was created with a first-month rent deposit due.")
            notify_user(application.tenant, f"Your application for {prop.title}, Unit {unit.unit_number if unit else '1'} was approved. The month-to-month lease is ready; the refundable security deposit due is KES {lease.deposit_amount}, equal to one month's rent.", "/payments/")
        else:
            messages.success(request, "Application declined.")
            notify_user(application.tenant, f"Your application for {prop.title}, Unit {unit.unit_number if unit else '1'} was declined by the landlord.", f"/property/{prop.pk}/")
    return redirect("dashboard_home")


@role_required("tenant", "landlord", "management", "admin")
def maintenance(request):
    role = current_role(request.user)
    if request.method == "POST":
        if role != "tenant":
            raise PermissionDenied
        form = MaintenanceForm(request.POST, user=request.user)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.requester = request.user
            obj.lease = form.cleaned_data["lease"]
            obj.property = obj.lease.property
            obj.unit = obj.lease.unit
            obj.property_name = obj.lease.property.title
            obj.save()
            if obj.property.owner_id:
                notify_user(
                    obj.property.owner,
                    f"{request.user.get_full_name() or request.user.username} reported ‘{obj.title}’ at {obj.property.title}, Unit {obj.unit.unit_number if obj.unit_id else '1'}.",
                    "/maintenance/",
                )
            else:
                notify_admins(f"A tenant reported ‘{obj.title}’ at {obj.property.title}, Unit {obj.unit.unit_number if obj.unit_id else '1'}; this property has no assigned landlord.")
            if obj.property.caretaker_id and obj.property.caretaker_id != obj.property.owner_id:
                notify_user(
                    obj.property.caretaker,
                    f"A tenant reported ‘{obj.title}’ at {obj.property.title}, Unit {obj.unit.unit_number if obj.unit_id else '1'}.",
                    "/maintenance/",
                )
            messages.success(request, "Your maintenance request was sent to the caretaker.")
            return redirect("maintenance")
    else:
        form = MaintenanceForm(user=request.user) if role == "tenant" else None

    if role == "tenant":
        requests = MaintenanceRequest.objects.filter(requester=request.user).select_related("property")
    elif role == "landlord":
        requests = MaintenanceRequest.objects.filter(property__owner=request.user).select_related("property", "requester")
    elif role == "management":
        requests = MaintenanceRequest.objects.filter(property__caretaker=request.user).select_related("property", "requester", "job")
    else:
        requests = MaintenanceRequest.objects.all().select_related("property", "requester")
    return render(request, "portal/maintenance.html", {"requests": requests, "form": form, "role": role})


@role_required("landlord", "management", "admin")
def maintenance_action(request, request_id, status):
    if request.method != "POST" or status not in {"progress", "resolved"}:
        raise PermissionDenied
    status = {"progress": "In Progress", "resolved": "Resolved"}[status]
    maintenance_request = get_object_or_404(
        MaintenanceRequest.objects.select_related("property", "requester", "job"), pk=request_id
    )
    role = current_role(request.user)
    if role == "landlord" and (not maintenance_request.property or maintenance_request.property.owner_id != request.user.id):
        raise PermissionDenied
    if role == "management" and (not maintenance_request.property or maintenance_request.property.caretaker_id != request.user.id):
        raise PermissionDenied
    if maintenance_request.status == "Resolved":
        messages.info(request, "This report is already resolved.")
    elif hasattr(maintenance_request, "job"):
        messages.error(request, "This report is linked to a worker job. Track progress from that job page.")
    else:
        maintenance_request.status = status
        maintenance_request.save(update_fields=["status"])
        status_message = "marked as in progress" if status == "In Progress" else "marked resolved"
        notify_user(
            maintenance_request.requester,
            f"Your maintenance report ‘{maintenance_request.title}’ was {status_message} by the property team.",
            "/maintenance/",
        )
        messages.success(request, f"Maintenance report {status_message}.")
    return redirect("maintenance")


@role_required("landlord", "management", "admin")
def post_job(request, request_id):
    maintenance_request = get_object_or_404(MaintenanceRequest.objects.select_related("property"), pk=request_id)
    role = current_role(request.user)
    if role == "landlord" and (not maintenance_request.property or maintenance_request.property.owner_id != request.user.id):
        raise PermissionDenied
    if role == "management" and (not maintenance_request.property or maintenance_request.property.caretaker_id != request.user.id):
        raise PermissionDenied
    if hasattr(maintenance_request, "job"):
        messages.info(request, "A worker job has already been posted for this request.")
        return redirect("maintenance")
    if request.method == "POST":
        form = JobForm(request.POST)
        if form.is_valid():
            Job.objects.create(
                title=maintenance_request.title,
                location=(f"{maintenance_request.property.location} · Unit {maintenance_request.unit.unit_number}" if maintenance_request.property and maintenance_request.unit else maintenance_request.property.location if maintenance_request.property else maintenance_request.property_name),
                trade=form.cleaned_data["trade"], payout=form.cleaned_data["payout"],
                urgent=form.cleaned_data["urgent"], description=form.cleaned_data["description"] or maintenance_request.description,
                property=maintenance_request.property, unit=maintenance_request.unit, request=maintenance_request, posted_by=request.user,
            )
            maintenance_request.status = "In Progress"
            maintenance_request.save(update_fields=["status"])
            notify_user(
                maintenance_request.requester,
                f"A worker job has been posted for your report ‘{maintenance_request.title}’ at {maintenance_request.property.title if maintenance_request.property else maintenance_request.property_name}.",
                "/maintenance/",
            )
            messages.success(request, "Job posted to the worker marketplace.")
            return redirect("maintenance")
    else:
        form = JobForm(initial={"trade": maintenance_request.category, "urgent": maintenance_request.priority == "Urgent", "description": maintenance_request.description})
    return render(request, "portal/post_job.html", {"maintenance_request": maintenance_request, "form": form})


@role_required("management")
def post_caretaker_job(request):
    form = CaretakerJobForm(request.POST or None, caretaker=request.user)
    if request.method == "POST" and form.is_valid():
        prop = form.cleaned_data["property"]
        unit = form.cleaned_data["unit"]
        work_area = form.cleaned_data["work_area"].strip()
        location = f"{prop.location} · {work_area}"
        if unit:
            location = f"{location} · Unit {unit.unit_number}"
        job = form.save(commit=False)
        job.property = prop
        job.unit = unit
        job.location = location[:180]
        job.posted_by = request.user
        job.save()
        if prop.owner_id:
            notify_user(
                prop.owner,
                f"Caretaker {request.user.get_full_name() or request.user.username} posted ‘{job.title}’ for {prop.title} (estimated payout KES {job.payout}).",
                f"/jobs/{job.pk}/",
            )
        messages.success(request, "The worker job is live in the marketplace.")
        return redirect("jobs")
    if not form.fields["property"].queryset.exists():
        messages.error(request, "You need an active assigned property before you can post a worker job.")
        return redirect("dashboard_home")
    return render(request, "portal/caretaker_post_job.html", {"form": form})


@login_required
def payments(request):
    role = current_role(request.user)
    rent_dues = []
    bookings = Booking.objects.none()
    if role == "tenant":
        leases = Lease.objects.filter(tenant=request.user, status__in=["Active", "Ended"]).select_related("property", "unit")
        rent_dues = build_rent_schedule(leases, include_paid_history=True)
        bookings = Booking.objects.filter(tenant=request.user, status="Confirmed").select_related("property", "unit").prefetch_related(
            Prefetch("payments", queryset=Payment.objects.filter(kind="Short stay payment").order_by("-created_at"), to_attr="stay_payments")
        ).order_by("check_in")
        qs = Payment.objects.filter(user=request.user, job__isnull=True).select_related("lease", "lease__property", "booking", "booking__property")
    elif role == "landlord":
        leases = Lease.objects.filter(property__owner=request.user, status="Active").select_related("property", "tenant")
        qs = Payment.objects.filter(
            Q(lease__property__owner=request.user)
            | Q(booking__property__owner=request.user)
            | Q(job__property__owner=request.user)
            | Q(job__posted_by=request.user)
        ).select_related("lease", "lease__property", "booking", "booking__property", "job", "job__property", "job__worker", "user", "paid_by")
    elif role == "worker":
        leases = Lease.objects.none()
        qs = Payment.objects.filter(job__worker=request.user, kind="Worker payout").select_related(
            "job", "job__property", "user", "paid_by"
        )
    elif role == "admin":
        leases = Lease.objects.filter(status="Active").select_related("property", "tenant")
        qs = Payment.objects.all().select_related(
            "lease", "lease__property", "booking", "booking__property", "job", "job__property", "job__worker", "user", "paid_by"
        )
    else:
        raise PermissionDenied

    lease_rows = None
    if role == "tenant":
        lease_rows = list(leases)
        deposit_by_lease = {}
        for deposit in Payment.objects.filter(lease_id__in=[lease.pk for lease in lease_rows], kind="Security deposit").order_by("created_at"):
            deposit_by_lease[deposit.lease_id] = deposit
        for lease in lease_rows:
            lease.deposit_payment = deposit_by_lease.get(lease.pk)

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "initiate_security_deposit" and role == "tenant":
            lease = get_object_or_404(leases, pk=request.POST.get("lease"))
            deposit = Payment.objects.filter(lease=lease, kind="Security deposit").order_by("-created_at").first()
            if deposit is None:
                messages.error(request, "The deposit ledger entry is missing. Contact the landlord before attempting payment.")
                return redirect("payments")
            with transaction.atomic():
                deposit = Payment.objects.select_for_update().get(pk=deposit.pk)
                if deposit.status == "Completed":
                    messages.info(request, "The security deposit for this lease is already recorded as paid.")
                    return redirect("payments")
                if deposit.status not in {"Due", "Failed"}:
                    messages.warning(request, "This deposit payment is awaiting confirmation or review. Check the payment ledger before retrying.")
                    return redirect("payments")
                deposit.status = "Initiating"
                deposit.payer_phone = request.POST.get("phone", "").strip()[:20]
                deposit.payment_method = "M-Pesa"
                deposit.provider_result_description = ""
                deposit.save(update_fields=["status", "payer_phone", "payment_method", "provider_result_description"])
            try:
                response, normalized_phone = daraja.initiate_stk_push(deposit, deposit.payer_phone)
            except daraja.DarajaUnknownOutcome as exc:
                deposit.status = "Initiation status unknown"
                deposit.provider_result_description = str(exc)[:255]
                deposit.save(update_fields=["status", "provider_result_description"])
                messages.error(request, f"{exc} The deposit is locked to prevent a duplicate charge.")
                notify_admins(f"Security deposit payment {deposit.pk} has an unknown Daraja outcome and needs reconciliation.", "/admin/core/payment/")
                return redirect("payments")
            except daraja.DarajaError as exc:
                deposit.status = "Failed"
                deposit.provider_result_description = str(exc)[:255]
                deposit.save(update_fields=["status", "provider_result_description"])
                messages.error(request, str(exc))
                return redirect("payments")
            deposit.status = "Pending"
            deposit.payer_phone = normalized_phone
            deposit.checkout_request_id = response["CheckoutRequestID"]
            deposit.merchant_request_id = response.get("MerchantRequestID", "")
            deposit.provider_initiated_at = timezone.now()
            deposit.save(update_fields=["status", "payer_phone", "checkout_request_id", "merchant_request_id", "provider_initiated_at"])
            messages.success(request, "M-Pesa deposit request sent. The ledger updates after Safaricom confirms payment.")
            return redirect("payments")

        if action == "initiate_rent_payment" and role == "tenant":
            lease = get_object_or_404(leases, pk=request.POST.get("lease"))
            phone = request.POST.get("phone", "").strip()
            try:
                billing_period = datetime.strptime(request.POST.get("billing_period", ""), "%Y-%m").date().replace(day=1)
            except ValueError:
                messages.error(request, "Choose the month this rent payment covers.")
                return redirect("payments")
            if billing_period < lease.start_date.replace(day=1) or billing_period > lease_last_billable_period(lease):
                messages.error(request, "Choose a rent month within your active month-to-month lease and no later than this month.")
                return redirect("payments")
            period_payments = Payment.objects.filter(lease=lease, kind="Rent payment", billing_period=billing_period)
            if period_payments.filter(status__in=["Initiating", "Pending", "Completed", "Review required", "Initiation status unknown"]).exists():
                messages.info(request, "A payment for this rent month is already recorded or awaiting confirmation. Check your payment history before trying again.")
                return redirect("payments")
            try:
                with transaction.atomic():
                    payment = Payment.objects.create(
                        entity=f"{lease.property.title} · Unit {lease.unit.unit_number if lease.unit_id else '1'} · {billing_period:%B %Y}",
                        kind="Rent payment", amount=lease.rent, status="Initiating", user=request.user,
                        lease=lease, payer_phone=phone[:20], payment_method="M-Pesa", billing_period=billing_period,
                        dedupe_key=f"rent:{lease.pk}:{billing_period:%Y%m}",
                    )
            except IntegrityError:
                messages.info(request, "A rent payment request for that month already exists. Check your payment history.")
                return redirect("payments")
            try:
                response, normalized_phone = daraja.initiate_stk_push(payment, phone)
            except daraja.DarajaUnknownOutcome as exc:
                payment.status = "Initiation status unknown"
                payment.provider_result_description = str(exc)[:255]
                payment.save(update_fields=["status", "provider_result_description"])
                messages.error(request, f"{exc} This rent month is locked to prevent a duplicate charge.")
                notify_admins(f"Rent payment request {payment.pk} has an unknown Daraja outcome and needs reconciliation.", "/admin/core/payment/")
                return redirect("payments")
            except daraja.DarajaError as exc:
                payment.status = "Failed"
                payment.dedupe_key = None
                payment.provider_result_description = str(exc)[:255]
                payment.save(update_fields=["status", "dedupe_key", "provider_result_description"])
                messages.error(request, str(exc))
                return redirect("payments")
            payment.status = "Pending"
            payment.payer_phone = normalized_phone
            payment.checkout_request_id = response["CheckoutRequestID"]
            payment.merchant_request_id = response.get("MerchantRequestID", "")
            payment.provider_initiated_at = timezone.now()
            payment.save(update_fields=["status", "payer_phone", "checkout_request_id", "merchant_request_id", "provider_initiated_at"])
            messages.success(request, "M-Pesa request sent. Enter your PIN on your phone; the ledger will update when Safaricom confirms payment.")
            return redirect("payments")

        if action == "initiate_booking_payment" and role == "tenant":
            booking = get_object_or_404(bookings, pk=request.POST.get("booking"))
            prior_payments = Payment.objects.filter(booking=booking, kind="Short stay payment")
            if prior_payments.filter(status__in=["Initiating", "Pending", "Completed", "Review required", "Initiation status unknown"]).exists():
                messages.info(request, "This stay already has a payment recorded or awaiting confirmation. Check your payment history first.")
                return redirect("payments")
            try:
                with transaction.atomic():
                    payment = Payment.objects.create(
                        entity=f"{booking.property.title} · Unit {booking.unit.unit_number if booking.unit_id else '1'} · {booking.check_in:%d %b}–{booking.check_out:%d %b %Y}",
                        kind="Short stay payment", amount=booking.total_price, status="Initiating", user=request.user,
                        booking=booking, payer_phone=request.POST.get("phone", "").strip()[:20],
                        payment_method="M-Pesa", dedupe_key=f"stay:{booking.pk}",
                    )
            except IntegrityError:
                messages.info(request, "A payment request for this stay already exists. Check your payment history.")
                return redirect("payments")
            try:
                response, normalized_phone = daraja.initiate_stk_push(payment, payment.payer_phone)
            except daraja.DarajaUnknownOutcome as exc:
                payment.status = "Initiation status unknown"
                payment.provider_result_description = str(exc)[:255]
                payment.save(update_fields=["status", "provider_result_description"])
                messages.error(request, f"{exc} This booking is locked to prevent a duplicate charge.")
                notify_admins(f"Short-stay payment request {payment.pk} has an unknown Daraja outcome and needs reconciliation.", "/admin/core/payment/")
                return redirect("payments")
            except daraja.DarajaError as exc:
                payment.status = "Failed"
                payment.dedupe_key = None
                payment.provider_result_description = str(exc)[:255]
                payment.save(update_fields=["status", "dedupe_key", "provider_result_description"])
                messages.error(request, str(exc))
                return redirect("payments")
            payment.status = "Pending"
            payment.payer_phone = normalized_phone
            payment.checkout_request_id = response["CheckoutRequestID"]
            payment.merchant_request_id = response.get("MerchantRequestID", "")
            payment.provider_initiated_at = timezone.now()
            payment.save(update_fields=["status", "payer_phone", "checkout_request_id", "merchant_request_id", "provider_initiated_at"])
            messages.success(request, "M-Pesa request sent. Enter your PIN on your phone; your stay ledger will update after Safaricom confirms payment.")
            return redirect("payments")

        if action == "send_worker_payout" and role == "landlord":
            payout = get_object_or_404(qs.filter(kind="Worker payout", job__isnull=False), pk=request.POST.get("payment_id"))
            if payout.job.status != "Completed" or payout.status != "Awaiting payment" or not payout.user_id:
                messages.error(request, "Only an unpaid payout for a completed job can be sent.")
                return redirect("payments")
            phone = (getattr(getattr(payout.user, "profile", None), "phone", "") or "").strip()
            if not phone:
                messages.error(request, "Ask the worker to add their M-Pesa number to their profile before sending an automated payout.")
                return redirect("payments")
            try:
                phone = daraja.normalize_phone(phone)
            except daraja.DarajaError as exc:
                messages.error(request, str(exc))
                return redirect("payments")
            with transaction.atomic():
                payout = Payment.objects.select_for_update().select_related("job", "user").get(pk=payout.pk)
                if payout.status != "Awaiting payment" or payout.job.status != "Completed":
                    messages.error(request, "This payout has already changed. Refresh the payment ledger.")
                    return redirect("payments")
                payout.status = "Initiating payout"
                payout.paid_by = request.user
                payout.payer_phone = phone
                payout.save(update_fields=["status", "paid_by", "payer_phone"])
            try:
                response, normalized_phone = daraja.initiate_b2c(payout, phone)
            except daraja.DarajaUnknownOutcome as exc:
                payout.refresh_from_db()
                payout.status = "Payout status unknown"
                payout.provider_result_description = str(exc)[:255]
                payout.save(update_fields=["status", "provider_result_description"])
                messages.error(request, f"{exc} The payout is locked to prevent a duplicate transfer.")
                notify_admins(f"Worker payout {payout.pk} has an unknown Daraja outcome and needs reconciliation.", "/admin/core/payment/")
                return redirect("payments")
            except daraja.DarajaError as exc:
                payout.refresh_from_db()
                payout.status = "Awaiting payment"
                payout.provider_result_description = str(exc)[:255]
                payout.save(update_fields=["status", "provider_result_description"])
                messages.error(request, str(exc))
                return redirect("payments")
            payout.status = "Processing"
            payout.payer_phone = normalized_phone
            payout.provider_conversation_id = response["OriginatorConversationID"]
            payout.provider_result_description = response.get("ResponseDescription", "")[:255]
            payout.provider_initiated_at = timezone.now()
            payout.payment_method = "M-Pesa B2C"
            payout.save(update_fields=["status", "payer_phone", "provider_conversation_id", "provider_result_description", "provider_initiated_at", "payment_method"])
            messages.success(request, "M-Pesa payout submitted. The worker will be marked paid only after Safaricom confirms it.")
            return redirect("payments")

        if action == "mark_payout_paid" and role in {"landlord", "admin"}:
            payment_id = request.POST.get("payment_id")
            payout = get_object_or_404(qs.filter(kind="Worker payout", job__isnull=False), pk=payment_id)
            method = request.POST.get("payment_method", "")
            reference = request.POST.get("payment_reference", "").strip()[:100]
            allowed_methods = {"mpesa": "M-Pesa", "bank_transfer": "Bank transfer", "cash": "Cash", "other": "Other"}
            if method not in allowed_methods:
                messages.error(request, "Choose how you sent the worker payout.")
                return redirect("payments")
            if method == "mpesa" and not reference:
                messages.error(request, "Enter the M-Pesa receipt code so the worker payout can be reconciled.")
                return redirect("payments")
            if payout.job.status != "Completed":
                messages.error(request, "A worker payout can only be marked paid after the job is complete.")
                return redirect("payments")
            if payout.status != "Awaiting payment":
                messages.info(request, "This worker payout has already been recorded.")
                return redirect("payments")

            with transaction.atomic():
                payout = Payment.objects.select_for_update().select_related("job", "user").get(pk=payout.pk)
                if payout.status != "Awaiting payment" or payout.job.status != "Completed":
                    messages.error(request, "The payout status changed. Refresh and review the ledger.")
                    return redirect("payments")
                payout.status = "Paid"
                payout.paid_by = request.user
                payout.paid_at = timezone.now()
                payout.payment_method = allowed_methods[method]
                payout.payment_reference = reference
                payout.save(update_fields=["status", "paid_by", "paid_at", "payment_method", "payment_reference"])
                reference_note = f" Reference: {payout.payment_reference}." if payout.payment_reference else ""
                notify_user(
                    payout.user,
                    f"Your KES {payout.amount} payout for ‘{payout.job.title}’ was recorded as paid via {payout.payment_method}.{reference_note}",
                    "/payments/",
                )
            messages.success(request, "Worker payout marked paid. The payment itself was sent outside EstateHub.")
            return redirect("payments")

        raise PermissionDenied

    pending_payout_total = qs.filter(kind="Worker payout", status="Awaiting payment").aggregate(total=Sum("amount"))["total"] or 0
    paid_payout_total = qs.filter(kind="Worker payout", status="Paid").aggregate(total=Sum("amount"))["total"] or 0
    qs = qs.order_by("-created_at")
    payment_methods = [("mpesa", "M-Pesa"), ("bank_transfer", "Bank transfer"), ("cash", "Cash"), ("other", "Other")]
    return render(request, "portal/payments.html", {
        "payments": qs,
        "leases": lease_rows if role == "tenant" else leases,
        "bookings": bookings,
        "rent_dues": rent_dues,
        "role": role,
        "pending_payout_total": pending_payout_total,
        "paid_payout_total": paid_payout_total,
        "payment_methods": payment_methods,
        "profile": getattr(request.user, "profile", None),
        "stk_configured": daraja.configured("DARAJA_CONSUMER_KEY", "DARAJA_CONSUMER_SECRET", "DARAJA_SHORTCODE", "DARAJA_PASSKEY") and settings.PUBLIC_BASE_URL.startswith("https://") and bool(settings.DARAJA_CALLBACK_TOKEN),
        "b2c_enabled": settings.DARAJA_B2C_ENABLED and daraja.configured("DARAJA_CONSUMER_KEY", "DARAJA_CONSUMER_SECRET", "DARAJA_B2C_SHORTCODE", "DARAJA_B2C_INITIATOR_NAME", "DARAJA_B2C_SECURITY_CREDENTIAL") and settings.PUBLIC_BASE_URL.startswith("https://") and bool(settings.DARAJA_CALLBACK_TOKEN),
    })


def _daraja_callback_authorized(request, token):
    configured_token = settings.DARAJA_CALLBACK_TOKEN
    return bool(configured_token and hmac.compare_digest(token, configured_token))


def _daraja_json(request):
    try:
        payload = json.loads(request.body.decode("utf-8"))
        return payload if isinstance(payload, dict) else None
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


@csrf_exempt
def daraja_stk_callback(request, token):
    if request.method != "POST" or not _daraja_callback_authorized(request, token):
        return JsonResponse({"ResultCode": 1, "ResultDesc": "Not found"}, status=404)
    payload = _daraja_json(request) or {}
    body = payload.get("Body") if isinstance(payload.get("Body"), dict) else {}
    callback = body.get("stkCallback") if isinstance(body.get("stkCallback"), dict) else {}
    checkout_id = callback.get("CheckoutRequestID")
    if not checkout_id:
        return JsonResponse({"ResultCode": 0, "ResultDesc": "Accepted"})
    with transaction.atomic():
        payment = Payment.objects.select_for_update().filter(checkout_request_id=checkout_id, kind__in=["Rent payment", "Short stay payment", "Security deposit"]).first()
        if payment is None or payment.status in {"Completed", "Failed"}:
            return JsonResponse({"ResultCode": 0, "ResultDesc": "Accepted"})
        result_code = str(callback.get("ResultCode", ""))
        payment.provider_result_code = result_code[:20]
        payment.provider_result_description = str(callback.get("ResultDesc", ""))[:255]
        if result_code == "0":
            callback_metadata = callback.get("CallbackMetadata")
            items = callback_metadata.get("Item", []) if isinstance(callback_metadata, dict) else []
            metadata = {item.get("Name"): item.get("Value") for item in items if isinstance(item, dict)}
            try:
                received_amount = Decimal(str(metadata.get("Amount", "")))
            except (InvalidOperation, ValueError):
                received_amount = Decimal("-1")
            received_phone = str(metadata.get("PhoneNumber", ""))
            receipt = str(metadata.get("MpesaReceiptNumber", ""))
            expected_phone = payment.payer_phone
            if received_amount != Decimal(int(payment.amount)) or not receipt or not expected_phone or not received_phone or not received_phone.endswith(expected_phone[-9:]):
                payment.status = "Review required"
                payment.provider_result_description = "Safaricom callback details did not match the payment request; review required."
            else:
                payment.status = "Completed"
                payment.paid_at = timezone.now()
                payment.payment_reference = receipt[:100]
                payment.provider_receipt = payment.payment_reference[:40]
                payment.save(update_fields=["status", "paid_at", "payment_reference", "provider_receipt", "provider_result_code", "provider_result_description"])
                notify_user(payment.user, f"Your payment of KES {payment.amount} for {payment.entity} was confirmed. Receipt: {payment.provider_receipt or 'M-Pesa confirmed'}.", "/payments/")
                landlord = payment.lease.property.owner if payment.lease_id else (payment.booking.property.owner if payment.booking_id else None)
                if landlord:
                    notify_user(landlord, f"Customer payment confirmed: KES {payment.amount} from {payment.user.username} for {payment.entity}. Receipt: {payment.provider_receipt or 'M-Pesa confirmed'}.", "/payments/")
                return JsonResponse({"ResultCode": 0, "ResultDesc": "Accepted"})
        else:
            payment.status = "Failed"
            payment.dedupe_key = None
        payment.save(update_fields=["status", "dedupe_key", "provider_result_code", "provider_result_description"])
        if payment.status == "Failed":
            notify_user(payment.user, f"Your M-Pesa payment for {payment.entity} was not completed. You can retry it from Payments.", "/payments/")
        else:
            notify_admins(f"M-Pesa payment {payment.pk} needs manual review because callback details did not match.", "/admin/core/payment/")
    return JsonResponse({"ResultCode": 0, "ResultDesc": "Accepted"})


@csrf_exempt
def daraja_b2c_result(request, token):
    if request.method != "POST" or not _daraja_callback_authorized(request, token):
        return JsonResponse({"ResultCode": 1, "ResultDesc": "Not found"}, status=404)
    payload = _daraja_json(request) or {}
    result = payload.get("Result") if isinstance(payload.get("Result"), dict) else {}
    conversation_id = result.get("OriginatorConversationID")
    if not conversation_id:
        return JsonResponse({"ResultCode": 0, "ResultDesc": "Accepted"})
    with transaction.atomic():
        payment = Payment.objects.select_for_update().select_related("user", "job").filter(
            provider_conversation_id=conversation_id, kind="Worker payout", status__in=["Processing", "Payout status unknown"]
        ).first()
        if payment is None:
            return JsonResponse({"ResultCode": 0, "ResultDesc": "Accepted"})
        result_code = str(result.get("ResultCode", ""))
        payment.provider_result_code = result_code[:20]
        payment.provider_result_description = str(result.get("ResultDesc", ""))[:255]
        if result_code == "0":
            result_parameters = result.get("ResultParameters")
            parameters = result_parameters.get("ResultParameter", []) if isinstance(result_parameters, dict) else []
            metadata = {item.get("Key"): item.get("Value") for item in parameters if isinstance(item, dict)}
            try:
                received_amount = Decimal(str(metadata.get("TransactionAmount", "")))
            except (InvalidOperation, ValueError):
                received_amount = Decimal("-1")
            receipt = str(metadata.get("TransactionReceipt") or result.get("TransactionID") or "")
            recipient = str(metadata.get("ReceiverPartyPublicName", ""))
            if received_amount != Decimal(int(payment.amount)) or not receipt or not recipient or not payment.payer_phone or not "".join(c for c in recipient if c.isdigit()).endswith(payment.payer_phone[-9:]):
                payment.status = "Review required"
                payment.provider_result_description = "Safaricom payout callback details did not match the requested amount and recipient; review required."
                payment.save(update_fields=["status", "provider_result_description", "provider_result_code"])
                notify_user(payment.paid_by, f"The M-Pesa payout callback for ‘{payment.job.title}’ needs manual review before it can be marked paid.", "/payments/")
                notify_admins(f"Worker payout {payment.pk} needs manual review because callback details did not match.", "/admin/core/payment/")
            else:
                payment.status = "Paid"
                payment.paid_at = timezone.now()
                payment.provider_receipt = receipt[:40]
                payment.payment_reference = payment.provider_receipt
                payment.save(update_fields=["status", "paid_at", "provider_receipt", "payment_reference", "provider_result_code", "provider_result_description"])
                notify_user(payment.user, f"Your KES {payment.amount} payout for ‘{payment.job.title}’ was sent to {payment.payer_phone}. M-Pesa receipt: {payment.provider_receipt}.", "/payments/")
        else:
            payment.status = "Awaiting payment"
            payment.save(update_fields=["status", "provider_result_code", "provider_result_description"])
            notify_user(payment.user, f"The M-Pesa payout for ‘{payment.job.title}’ was not completed. Contact the landlord about another payment method.", "/payments/")
            notify_user(payment.paid_by, f"The M-Pesa payout for ‘{payment.job.title}’ was not completed. Review the result and try again.", "/payments/")
    return JsonResponse({"ResultCode": 0, "ResultDesc": "Accepted"})


@csrf_exempt
def daraja_b2c_timeout(request, token):
    if request.method != "POST" or not _daraja_callback_authorized(request, token):
        return JsonResponse({"ResultCode": 1, "ResultDesc": "Not found"}, status=404)
    payload = _daraja_json(request) or {}
    result = payload.get("Result") if isinstance(payload.get("Result"), dict) else {}
    conversation_id = payload.get("OriginatorConversationID") or result.get("OriginatorConversationID")
    if conversation_id:
        updated = Payment.objects.filter(provider_conversation_id=conversation_id, kind="Worker payout", status="Processing").update(
            status="Payout status unknown", provider_result_description="Safaricom timed out; reconcile the transaction before retrying."
        )
        if updated:
            notify_admins(f"Worker payout conversation {conversation_id} timed out and needs reconciliation.", "/admin/core/payment/")
    return JsonResponse({"ResultCode": 0, "ResultDesc": "Accepted"})


@role_required("tenant")
def move_out_request(request, lease_id):
    lease = get_object_or_404(
        Lease.objects.select_related("property", "unit", "tenant").prefetch_related("change_requests"),
        pk=lease_id, tenant=request.user,
    )
    today = timezone.localdate()
    changes = list(lease.change_requests.all())
    pending_change = next((change for change in changes if change.status == "Pending"), None)
    approved_moveout = next((
        change for change in changes
        if change.request_type == "move_out" and change.status == "Approved"
    ), None)
    current_term_end = current_month_term_end(lease, today) if lease.status == "Active" else lease.end_date

    if request.method == "POST":
        if lease.status != "Active":
            messages.error(request, "Move-out requests are available for active leases only.")
            return redirect("move_out_request", lease_id=lease.pk)
        if approved_moveout:
            messages.error(request, "A move-out date has already been approved for this lease.")
            return redirect("move_out_request", lease_id=lease.pk)
        if pending_change:
            messages.error(request, "Resolve the pending lease request before submitting another request.")
            return redirect("move_out_request", lease_id=lease.pk)
        try:
            proposed_end_date = date.fromisoformat(request.POST.get("proposed_end_date", ""))
        except (ValueError, TypeError):
            messages.error(request, "Choose a valid move-out date.")
            return redirect("move_out_request", lease_id=lease.pk)
        if not today <= proposed_end_date <= current_term_end:
            messages.error(request, f"Choose a date from today through the current term end ({current_term_end:%d %b %Y}).")
            return redirect("move_out_request", lease_id=lease.pk)
        try:
            with transaction.atomic():
                change_request = LeaseChangeRequest.objects.create(
                    lease=lease,
                    request_type="move_out",
                    proposed_end_date=proposed_end_date,
                    message=request.POST.get("message", "").strip()[:4000],
                )
        except IntegrityError:
            messages.error(request, "A lease request is already pending. Check its status before trying again.")
            return redirect("move_out_request", lease_id=lease.pk)
        if lease.property.owner_id:
            notify_user(
                lease.property.owner,
                f"{request.user.get_full_name() or request.user.username} requested to move out of {lease.property.title}, Unit {lease.unit.unit_number if lease.unit_id else '1'}.",
                "/leases/",
            )
        messages.success(request, "Your move-out request was sent to the landlord.")
        return redirect("move_out_request", lease_id=lease.pk)

    return render(request, "portal/move_out_request.html", {
        "lease": lease,
        "change_requests": changes,
        "pending_change": pending_change,
        "approved_moveout": approved_moveout,
        "current_term_end": current_term_end,
        "today": today,
    })


@login_required
def leases(request):
    role = current_role(request.user)
    if role == "tenant":
        qs = Lease.objects.filter(tenant=request.user)
    elif role == "landlord":
        qs = Lease.objects.filter(property__owner=request.user)
    elif role == "admin":
        qs = Lease.objects.all()
    else:
        raise PermissionDenied
    search_query = ""
    selected_property = ""
    selected_status = ""
    landlord_properties = Property.objects.none()
    page_obj = None
    filtered_lease_count = qs.count()
    active_lease_count = 0
    ended_lease_count = 0
    pending_moveout_count = 0
    if role == "landlord":
        landlord_properties = Property.objects.filter(owner=request.user).order_by("title")
        active_lease_count = qs.filter(status="Active").count()
        ended_lease_count = qs.filter(status="Ended").count()
        pending_moveout_count = LeaseChangeRequest.objects.filter(
            lease__property__owner=request.user,
            request_type="move_out",
            status="Pending",
        ).count()
        search_query = request.GET.get("q", "").strip()[:100]
        selected_property = request.GET.get("property", "").strip()
        selected_status = request.GET.get("status", "").strip()
        search_terms = search_query.split()
        for search_term in search_terms:
            qs = qs.filter(
                Q(property__title__icontains=search_term)
                | Q(property__location__icontains=search_term)
                | Q(unit__unit_number__icontains=search_term)
                | Q(tenant__username__icontains=search_term)
                | Q(tenant__first_name__icontains=search_term)
                | Q(tenant__last_name__icontains=search_term)
                | Q(tenant__email__icontains=search_term)
            )
        if selected_property.isdigit() and landlord_properties.filter(pk=selected_property).exists():
            qs = qs.filter(property_id=selected_property)
        else:
            selected_property = ""
        if selected_status in {"Active", "Ended"}:
            qs = qs.filter(status=selected_status)
        else:
            selected_status = ""
        qs = qs.order_by("-start_date", "-pk")
        paginator = Paginator(qs, 8)
        page_obj = paginator.get_page(request.GET.get("page"))
        filtered_lease_count = paginator.count
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "request_lease_change":
            if role != "tenant":
                raise PermissionDenied
            lease = get_object_or_404(Lease.objects.select_related("property", "unit"), pk=request.POST.get("lease"), tenant=request.user, status="Active")
            request_type = request.POST.get("request_type")
            if request_type != "move_out":
                messages.error(request, "Active leases renew monthly automatically. Use move-out notice to end the lease.")
                return redirect("leases")
            try:
                proposed_end_date = date.fromisoformat(request.POST.get("proposed_end_date", ""))
            except (ValueError, TypeError):
                messages.error(request, "Choose a valid proposed date.")
                return redirect("leases")
            today = timezone.localdate()
            current_term_end = current_month_term_end(lease, today)
            if not (today <= proposed_end_date <= current_term_end):
                messages.error(request, f"Move-out date must be today or later and no later than the current monthly term end ({current_term_end:%d %b %Y}).")
                return redirect("leases")
            if lease.change_requests.filter(request_type="move_out", status="Approved").exists():
                messages.error(request, "A move-out date has already been approved for this lease.")
                return redirect("leases")
            proposed_rent = None
            if request.POST.get("proposed_rent", "").strip():
                try:
                    proposed_rent = Decimal(request.POST["proposed_rent"].strip())
                except (InvalidOperation, ValueError):
                    proposed_rent = Decimal("0")
                if proposed_rent <= 0 or proposed_rent > Decimal("9999999999.99"):
                    messages.error(request, "Enter a valid positive monthly rent for the renewal.")
                    return redirect("leases")
            if proposed_rent is not None:
                messages.error(request, "A move-out notice does not include a proposed rent.")
                return redirect("leases")
            if LeaseChangeRequest.objects.filter(lease=lease, status="Pending").exists():
                messages.error(request, "There is already a pending request for this lease.")
                return redirect("leases")
            try:
                with transaction.atomic():
                    change_request = LeaseChangeRequest.objects.create(
                        lease=lease, request_type=request_type, proposed_end_date=proposed_end_date,
                        proposed_rent=proposed_rent, message=request.POST.get("message", "").strip()[:2000],
                    )
            except IntegrityError:
                messages.error(request, "There is already a pending request for this lease.")
                return redirect("leases")
            if lease.property.owner_id:
                notify_user(lease.property.owner, f"{request.user.get_full_name() or request.user.username} submitted a {change_request.get_request_type_display().lower()} request for {lease.property.title}, Unit {lease.unit.unit_number if lease.unit_id else '1'}.", "/leases/")
            messages.success(request, "Your request was sent to the landlord.")
            return redirect("leases")

        if action == "review_lease_change":
            if role != "landlord":
                raise PermissionDenied
            decision = request.POST.get("decision")
            if decision not in {"approve", "decline"}:
                raise PermissionDenied
            change_request = get_object_or_404(
                LeaseChangeRequest.objects.select_related("lease", "lease__property", "lease__unit", "lease__tenant"),
                pk=request.POST.get("lease_request"), lease__property__owner=request.user,
            )
            with transaction.atomic():
                locked_request = LeaseChangeRequest.objects.select_for_update().get(pk=change_request.pk)
                lease = Lease.objects.select_for_update().get(pk=locked_request.lease_id)
                if locked_request.status != "Pending" or lease.status != "Active":
                    messages.error(request, "This request is no longer pending for an active lease.")
                    return redirect("leases")
                approved = decision == "approve"
                if approved and locked_request.request_type == "renewal":
                    if locked_request.proposed_rent is not None:
                        lease.rent = locked_request.proposed_rent
                        lease.save(update_fields=["rent"])
                elif approved and locked_request.request_type == "move_out":
                    latest_allowed_end = current_month_term_end(lease) if not lease_has_approved_moveout(lease) else lease.end_date
                    if locked_request.proposed_end_date > latest_allowed_end:
                        messages.error(request, "The lease dates changed; review the move-out date before approving.")
                        return redirect("leases")
                    lease.end_date = locked_request.proposed_end_date
                    if locked_request.proposed_end_date <= timezone.localdate():
                        lease.status = "Ended"
                        lease.save(update_fields=["end_date", "status"])
                        if lease.unit_id:
                            RentalUnit.objects.filter(pk=lease.unit_id).update(status="Available")
                            Property.objects.filter(pk=lease.property_id).update(status="Available")
                        elif not Lease.objects.filter(property_id=lease.property_id, status="Active").exclude(pk=lease.pk).exists():
                            Property.objects.filter(pk=lease.property_id).update(status="Available")
                    else:
                        lease.save(update_fields=["end_date"])
                locked_request.status = "Approved" if approved else "Declined"
                locked_request.landlord_response = request.POST.get("landlord_response", "").strip()[:2000]
                locked_request.reviewed_at = timezone.now()
                locked_request.reviewed_by = request.user
                locked_request.save(update_fields=["status", "landlord_response", "reviewed_at", "reviewed_by"])
            if change_request.lease.tenant_id:
                result_text = "approved" if approved else "declined"
                notify_user(change_request.lease.tenant, f"Your {change_request.get_request_type_display().lower()} request for {change_request.lease.property.title} was {result_text}." + (f" Landlord response: {locked_request.landlord_response}" if locked_request.landlord_response else ""), "/leases/")
            messages.success(request, "Lease request updated.")
            return redirect("leases")

        if action == "finalize_move_out":
            if role != "landlord":
                raise PermissionDenied
            lease = get_object_or_404(Lease.objects.select_related("property", "unit", "tenant"), pk=request.POST.get("lease"), property__owner=request.user, status="Active")
            if lease.end_date > timezone.localdate() or not lease.change_requests.filter(request_type="move_out", status="Approved").exists():
                messages.error(request, "The approved move-out date has not arrived.")
                return redirect("leases")
            with transaction.atomic():
                lease = Lease.objects.select_for_update().get(pk=lease.pk)
                lease.status = "Ended"
                lease.save(update_fields=["status"])
                if lease.unit_id:
                    RentalUnit.objects.filter(pk=lease.unit_id).update(status="Available")
                    Property.objects.filter(pk=lease.property_id).update(status="Available")
                elif not Lease.objects.filter(property_id=lease.property_id, status="Active").exclude(pk=lease.pk).exists():
                    Property.objects.filter(pk=lease.property_id).update(status="Available")
            if lease.tenant_id:
                notify_user(lease.tenant, f"Your lease at {lease.property.title} has been marked ended and the unit is now available.", "/leases/")
            messages.success(request, "Move-out finalized and unit marked available.")
            return redirect("leases")

        if role != "landlord":
            raise PermissionDenied
        lease = get_object_or_404(Lease, pk=request.POST.get("lease"), property__owner=request.user, status__in=["Active", "Ended"])
        if action == "update_rent_due_day":
            if lease.status != "Active":
                messages.error(request, "The rent due day cannot be changed after the lease has ended.")
                return redirect("leases")
            try:
                due_day = int(request.POST.get("rent_due_day", ""))
            except (TypeError, ValueError):
                due_day = 0
            if due_day < 1 or due_day > 31:
                messages.error(request, "Choose a rent due day between 1 and 31.")
                return redirect("leases")
            lease.rent_due_day = due_day
            lease.save(update_fields=["rent_due_day"])
            messages.success(request, f"Rent for {lease.property.title}, Unit {lease.unit.unit_number if lease.unit_id else '1'} is now due on day {due_day} of each month.")
            return redirect("leases")
        if action == "record_deposit_received":
            method = request.POST.get("payment_method", "")
            methods = {"mpesa": "M-Pesa", "bank_transfer": "Bank transfer", "cash": "Cash", "other": "Other"}
            reference = request.POST.get("payment_reference", "").strip()[:100]
            if method not in methods:
                messages.error(request, "Choose how the security deposit was received.")
                return redirect("leases")
            if method == "mpesa" and not reference:
                messages.error(request, "Enter the M-Pesa receipt code to record this deposit.")
                return redirect("leases")
            with transaction.atomic():
                deposit = Payment.objects.select_for_update().filter(lease=lease, kind="Security deposit").order_by("-created_at").first()
                if deposit is None:
                    deposit = Payment.objects.create(
                        entity=f"{lease.property.title} · Unit {lease.unit.unit_number if lease.unit_id else '1'} · Security deposit",
                        kind="Security deposit", amount=lease.deposit_amount or lease.rent,
                        status="Due", user=lease.tenant, lease=lease, dedupe_key=f"deposit:{lease.pk}",
                    )
                if deposit.status == "Completed":
                    messages.info(request, "This security deposit is already recorded as received.")
                    return redirect("leases")
                if deposit.status not in {"Due", "Failed"}:
                    messages.warning(request, "The deposit payment is awaiting confirmation or review; do not record it twice.")
                    return redirect("leases")
                deposit.status = "Completed"
                deposit.user = lease.tenant
                deposit.paid_by = request.user
                deposit.paid_at = timezone.now()
                deposit.payment_method = methods[method]
                deposit.payment_reference = reference
                deposit.save(update_fields=["status", "user", "paid_by", "paid_at", "payment_method", "payment_reference"])
            if lease.tenant_id:
                notify_user(lease.tenant, f"Your security deposit of KES {deposit.amount} for {lease.property.title} was recorded as received.", "/payments/")
            messages.success(request, "Security deposit received and added to the payment history.")
            return redirect("leases")
        if action == "record_rent_received":
            try:
                billing_period = datetime.strptime(request.POST.get("billing_period", ""), "%Y-%m").date().replace(day=1)
            except ValueError:
                messages.error(request, "Choose the month the rent payment covers.")
                return redirect("leases")
            if billing_period < lease.start_date.replace(day=1) or billing_period > lease_last_billable_period(lease):
                messages.error(request, "Choose a rent month within the active month-to-month lease and no later than this month.")
                return redirect("leases")
            method = request.POST.get("payment_method", "")
            methods = {"mpesa": "M-Pesa", "bank_transfer": "Bank transfer", "cash": "Cash", "other": "Other"}
            reference = request.POST.get("payment_reference", "").strip()[:100]
            if method not in methods:
                messages.error(request, "Choose how the rent was received.")
                return redirect("leases")
            if method == "mpesa" and not reference:
                messages.error(request, "Enter the M-Pesa receipt code to record this payment.")
                return redirect("leases")
            existing_period = Payment.objects.filter(
                lease=lease, kind="Rent payment", billing_period=billing_period,
                status__in=["Initiating", "Pending", "Completed", "Review required", "Initiation status unknown"],
            ).exists()
            if existing_period:
                messages.error(request, "A rent payment for that month is already paid or awaiting confirmation.")
                return redirect("leases")
            try:
                with transaction.atomic():
                    Payment.objects.create(
                        entity=f"{lease.property.title} · Unit {lease.unit.unit_number if lease.unit_id else '1'} · {billing_period:%B %Y}",
                        kind="Rent payment", amount=lease.rent, status="Completed", user=lease.tenant,
                        lease=lease, billing_period=billing_period, dedupe_key=f"rent:{lease.pk}:{billing_period:%Y%m}",
                        paid_by=request.user, paid_at=timezone.now(), payment_method=methods[method],
                        payment_reference=reference,
                    )
            except IntegrityError:
                messages.error(request, "A payment for that lease and month already exists or is still processing. Review the payment ledger before retrying.")
                return redirect("leases")
            notify_user(lease.tenant, f"Your {billing_period:%B %Y} rent for {lease.property.title} was recorded as received (KES {lease.rent}).", "/payments/")
            messages.success(request, f"Rent received for {billing_period:%B %Y} was recorded.")
            return redirect("leases")
        raise PermissionDenied
    lease_queryset = page_obj.object_list if page_obj is not None else qs.order_by("-start_date", "-pk")
    lease_rows = list(lease_queryset.select_related("property", "tenant", "unit").prefetch_related("change_requests"))
    deposit_by_lease = {}
    for deposit in Payment.objects.filter(lease_id__in=[item.pk for item in lease_rows], kind="Security deposit").order_by("created_at"):
        deposit_by_lease[deposit.lease_id] = deposit
    today = timezone.localdate()
    for lease in lease_rows:
        lease.deposit_payment = deposit_by_lease.get(lease.pk)
        changes = list(lease.change_requests.all())
        lease.pending_change_request = next((change for change in changes if change.status == "Pending"), None)
        lease.approved_moveout_request = next((
            change for change in changes
            if change.request_type == "move_out" and change.status == "Approved"
        ), None)
        lease.current_term_end = (
            current_month_term_end(lease, today)
            if lease.status == "Active" and not lease_has_approved_moveout(lease)
            else lease.end_date
        )
    return render(request, "portal/leases.html", {
        "leases": lease_rows, "role": role,
        "current_billing_period": today.strftime("%Y-%m"),
        "today": today,
        "page_obj": page_obj,
        "landlord_properties": landlord_properties,
        "search_query": search_query,
        "selected_property": selected_property,
        "selected_status": selected_status,
        "filtered_lease_count": filtered_lease_count,
        "active_lease_count": active_lease_count,
        "ended_lease_count": ended_lease_count,
        "pending_moveout_count": pending_moveout_count,
    })


@role_required("tenant", "worker", "landlord", "management", "admin")
def jobs(request):
    role = current_role(request.user)
    if role == "worker":
        open_jobs = Job.objects.filter(status="Open", worker__isnull=True)
        my_jobs = Job.objects.filter(worker=request.user)
        qs = Job.objects.filter(Q(status="Open", worker__isnull=True) | Q(worker=request.user))
        open_jobs = open_jobs.prefetch_related(Prefetch(
            "worker_requests",
            queryset=WorkerJobRequest.objects.filter(worker=request.user),
            to_attr="my_worker_requests",
        ))
    elif role == "landlord":
        qs = Job.objects.filter(Q(property__owner=request.user) | Q(posted_by=request.user)).distinct()
        open_jobs = qs.filter(status="Open")
        my_jobs = qs.exclude(status="Open")
    elif role == "management":
        qs = Job.objects.filter(property__caretaker=request.user)
        open_jobs = qs.filter(status="Open")
        my_jobs = qs.exclude(status="Open")
    elif role == "tenant":
        leased_property_ids = Lease.objects.filter(
            tenant=request.user, status="Active"
        ).values_list("property_id", flat=True)
        leased_unit_ids = Lease.objects.filter(
            tenant=request.user, status="Active", unit__isnull=False
        ).values_list("unit_id", flat=True)
        reported_request_ids = MaintenanceRequest.objects.filter(
            requester=request.user
        ).values_list("pk", flat=True)
        qs = Job.objects.filter(
            Q(property_id__in=leased_property_ids, request__isnull=True)
            | Q(property_id__in=leased_property_ids, request__unit__isnull=True)
            | Q(request__unit_id__in=leased_unit_ids)
            | Q(request_id__in=reported_request_ids)
        ).distinct()
        open_jobs = qs.filter(status="Open")
        my_jobs = qs.exclude(status="Open")
    else:
        qs = Job.objects.all()
        open_jobs = qs.filter(status="Open")
        my_jobs = qs.exclude(status="Open")
    pending_worker_requests = WorkerJobRequest.objects.none()
    if role in {"landlord", "management"}:
        pending_worker_requests = WorkerJobRequest.objects.filter(
            status="Pending", job__in=qs
        ).select_related("job", "job__property", "worker").order_by("created_at")
    elif role == "admin":
        pending_worker_requests = WorkerJobRequest.objects.filter(
            status="Pending"
        ).select_related("job", "job__property", "worker").order_by("created_at")
    return render(request, "portal/jobs.html", {
        "jobs": qs,
        "open_jobs": open_jobs.select_related("property", "worker").order_by("-created_at"),
        "my_jobs": my_jobs.select_related("property", "worker").order_by("-created_at"),
        "pending_worker_requests": pending_worker_requests,
        "role": role,
    })


@role_required("tenant", "worker", "landlord", "management", "admin")
def job_detail(request, pk):
    job = get_object_or_404(Job.objects.select_related("property", "worker", "request"), pk=pk)
    role = current_role(request.user)
    if role == "worker" and not (
        (job.status == "Open" and job.worker_id is None) or job.worker_id == request.user.id
    ):
        raise PermissionDenied
    if role == "landlord" and not (
        job.posted_by_id == request.user.id
        or (job.property_id and job.property.owner_id == request.user.id)
    ):
        raise PermissionDenied
    if role == "management" and (not job.property or job.property.caretaker_id != request.user.id):
        raise PermissionDenied
    if role == "tenant":
        active_leases = Lease.objects.filter(
            property_id=job.property_id, tenant=request.user, status="Active"
        ) if job.property_id else Lease.objects.none()
        if job.request_id and job.request.unit_id:
            active_leases = active_leases.filter(unit_id=job.request.unit_id)
        has_active_lease = active_leases.exists()
        reported_by_tenant = job.request_id and MaintenanceRequest.objects.filter(
            pk=job.request_id, requester=request.user
        ).exists()
        if not (has_active_lease or reported_by_tenant):
            raise PermissionDenied
    worker_request = None
    if role == "worker":
        worker_request = WorkerJobRequest.objects.filter(job=job, worker=request.user).first()
    return render(request, "portal/job_detail.html", {"job": job, "role": role, "worker_request": worker_request})


@role_required("worker")
def request_worker_job(request, pk):
    if request.method != "POST":
        raise PermissionDenied
    with transaction.atomic():
        job = get_object_or_404(
            Job.objects.select_for_update().select_related("property", "property__owner", "property__caretaker", "posted_by"),
            pk=pk,
        )
        if job.status != "Open" or job.worker_id is not None:
            messages.error(request, "This job is no longer accepting worker requests.")
            return redirect("jobs")
        worker_request = WorkerJobRequest.objects.filter(job=job, worker=request.user).first()
        if worker_request and worker_request.status == "Pending":
            messages.info(request, "Your request is already waiting for approval.")
            return redirect("jobs")
        if worker_request:
            worker_request.status = "Pending"
            worker_request.created_at = timezone.now()
            worker_request.reviewed_at = None
            worker_request.reviewed_by = None
            worker_request.save(update_fields=["status", "created_at", "reviewed_at", "reviewed_by"])
        else:
            worker_request = WorkerJobRequest.objects.create(job=job, worker=request.user)

        reviewers = []
        if job.property_id:
            reviewers.extend([job.property.owner, job.property.caretaker])
        if job.posted_by and not any(reviewers):
            reviewers.append(job.posted_by)
        notified_ids = set()
        for reviewer in reviewers:
            if reviewer and reviewer.id != request.user.id and reviewer.id not in notified_ids:
                notify_user(
                    reviewer,
                    f"{request.user.get_full_name() or request.user.username} requested approval for ‘{job.title}’ at {job.location}.",
                    "/jobs/",
                )
                notified_ids.add(reviewer.id)
    messages.success(request, "Your request was sent to the property team for approval.")
    return redirect("jobs")


@role_required("landlord", "management", "admin")
def review_worker_job_request(request, pk, request_id, decision):
    if request.method != "POST" or decision not in {"approve", "reject"}:
        raise PermissionDenied
    with transaction.atomic():
        job = get_object_or_404(
            Job.objects.select_for_update().select_related("property"), pk=pk
        )
        role = current_role(request.user)
        if role == "landlord":
            owns_job = job.posted_by_id == request.user.id or (
                job.property_id and job.property.owner_id == request.user.id
            )
            if not owns_job:
                raise PermissionDenied
        if role == "management" and (not job.property_id or job.property.caretaker_id != request.user.id):
            raise PermissionDenied
        worker_request = get_object_or_404(
            WorkerJobRequest.objects.select_for_update().select_related("worker"),
            pk=request_id,
            job=job,
            status="Pending",
        )
        if decision == "approve":
            if job.status != "Open" or job.worker_id is not None:
                messages.error(request, "This job has already been assigned. Refresh the jobs page.")
                return redirect("jobs")
            job.worker = worker_request.worker
            job.status = "Assigned"
            job.save(update_fields=["worker", "status"])
            worker_request.status = "Approved"
            competing_requests = list(WorkerJobRequest.objects.filter(
                job=job, status="Pending"
            ).exclude(pk=worker_request.pk).select_related("worker"))
            WorkerJobRequest.objects.filter(job=job, status="Pending").exclude(pk=worker_request.pk).update(
                status="Rejected", reviewed_at=timezone.now(), reviewed_by=request.user
            )
            for competing in competing_requests:
                notify_user(
                    competing.worker,
                    f"Another worker was approved for ‘{job.title}’. You can request another open job.",
                    "/jobs/",
                )
            notify_user(
                worker_request.worker,
                f"Your request was approved. You are assigned to ‘{job.title}’. Review the job details before starting.",
                f"/jobs/{job.pk}/",
            )
            messages.success(request, f"{worker_request.worker.username} was approved and assigned to the job.")
        else:
            worker_request.status = "Rejected"
            notify_user(
                worker_request.worker,
                f"Your request to work on ‘{job.title}’ was not approved. You can request another open job.",
                "/jobs/",
            )
            messages.success(request, "Worker request declined.")
        worker_request.reviewed_at = timezone.now()
        worker_request.reviewed_by = request.user
        worker_request.save(update_fields=["status", "reviewed_at", "reviewed_by"])
    return redirect("jobs")


@role_required("worker")
def worker_job_action(request, pk, action):
    if request.method != "POST":
        raise PermissionDenied
    with transaction.atomic():
        job = get_object_or_404(
            Job.objects.select_for_update().select_related("request", "property", "property__owner", "posted_by"),
            pk=pk,
        )
        if action == "start" and job.worker_id == request.user.id and job.status == "Assigned":
            job.status = "In Progress"
        elif action == "complete" and job.worker_id == request.user.id and job.status == "In Progress":
            job.status = "Completed"
            if job.request_id:
                job.request.status = "Resolved"
                job.request.save(update_fields=["status"])
        else:
            messages.error(request, "That job action is no longer available.")
            return redirect("job_detail", pk=pk)
        job.save(update_fields=["worker", "status"])
        if action == "complete":
            payout, created = Payment.objects.get_or_create(
                job=job,
                defaults={
                    "entity": job.title,
                    "kind": "Worker payout",
                    "amount": job.payout,
                    "status": "Awaiting payment",
                    "user": job.worker,
                },
            )
            if created:
                notify_user(
                    job.worker,
                    f"Job ‘{job.title}’ is complete. Your KES {payout.amount} payout is awaiting payment.",
                    "/payments/",
                )
                payout_contact = job.property.owner if job.property_id and job.property.owner_id else job.posted_by
                notify_user(
                    payout_contact,
                    f"Job ‘{job.title}’ is complete. Record the KES {payout.amount} worker payout in your payments ledger.",
                    "/payments/",
                )
            if job.request_id:
                notify_user(
                    job.request.requester,
                    f"The worker marked ‘{job.title}’ complete. Your maintenance report is now marked resolved.",
                    "/maintenance/",
                )
    messages.success(request, f"Job marked {job.status.lower()}.")
    return redirect("job_detail", pk=pk)


@login_required
def analytics(request):
    role = current_role(request.user)
    if role == "landlord":
        properties = Property.objects.filter(owner=request.user)
        payments = Payment.objects.filter(lease__property__owner=request.user)
    elif role == "admin":
        properties = Property.objects.all()
        payments = Payment.objects.all()
    else:
        raise PermissionDenied
    leases = Lease.objects.filter(property__in=properties, status="Active")
    total_recorded = payments.aggregate(total=Sum("amount"))["total"] or 0
    return render(request, "portal/analytics.html", {
        "properties": properties,
        "payments": payments,
        "property_count": properties.count(),
        "active_lease_count": leases.count(),
        "recorded_total": total_recorded,
        "unresolved_request_count": MaintenanceRequest.objects.filter(
            property__in=properties
        ).exclude(status="Resolved").count(),
    })


@role_required("tenant", "landlord", "worker", "management", "admin")
def reports(request):
    """Role-scoped operational and financial reports for the selected period."""
    role = current_role(request.user)
    today = timezone.localdate()
    default_start = today - timedelta(days=29)

    def read_date(value, fallback):
        try:
            return date.fromisoformat(value) if value else fallback
        except (TypeError, ValueError):
            return fallback

    start = read_date(request.GET.get("from"), default_start)
    end = read_date(request.GET.get("to"), today)
    if end < start:
        start, end = default_start, today

    def dated(queryset, field="created_at"):
        return queryset.filter(**{f"{field}__date__gte": start, f"{field}__date__lte": end})

    cards, tables = [], []
    def card(label, value, note=""):
        if isinstance(value, Decimal):
            display_value = f"{value:,.2f}"
        elif isinstance(value, int):
            display_value = f"{value:,}"
        else:
            display_value = value
        cards.append({"label": label, "value": value, "display_value": display_value, "note": note})

    def table(title, headers, rows):
        tables.append({"title": title, "headers": headers, "rows": rows})

    if role == "landlord":
        owned = Property.objects.filter(owner=request.user).prefetch_related("floors__units")
        property_ids = list(owned.values_list("id", flat=True))
        leases = Lease.objects.filter(property_id__in=property_ids, status="Active").select_related("property", "unit", "tenant")
        active_leases = list(leases)
        active_unit_ids = {lease.unit_id for lease in active_leases if lease.unit_id}
        rent_dues = build_rent_schedule(active_leases, today=today)
        rent_due_by_property = {}
        for due in rent_dues:
            if due["status"] in {"Overdue", "Due today"}:
                property_id = due["lease"].property_id
                rent_due_by_property[property_id] = rent_due_by_property.get(property_id, Decimal("0")) + due["amount"]
        open_repairs = MaintenanceRequest.objects.filter(property_id__in=property_ids).exclude(status="Resolved")
        stays = Booking.objects.filter(property_id__in=property_ids, status__in=["Confirmed", "Checked in", "Checked out"], check_in__lte=end, check_out__gte=start).select_related("property")
        applications = dated(RentalApplication.objects.filter(property_id__in=property_ids).select_related("property", "unit", "tenant").order_by("-created_at"))
        payments = dated(Payment.objects.filter(
            Q(lease__property_id__in=property_ids) | Q(booking__property_id__in=property_ids) | Q(job__property_id__in=property_ids)
        ).select_related("lease__property", "lease__unit", "lease__tenant", "booking__property", "job__property", "job__worker", "user").order_by("-created_at"))
        requests = dated(MaintenanceRequest.objects.filter(property_id__in=property_ids).select_related("property", "unit").order_by("-created_at"))
        jobs = dated(Job.objects.filter(property_id__in=property_ids).select_related("property", "worker").order_by("-created_at"))
        income_by_property, payout_by_property = {}, {}
        for payment in payments:
            prop_id = (payment.lease.property_id if payment.lease_id else
                       payment.booking.property_id if payment.booking_id else
                       payment.job.property_id if payment.job_id else None)
            if not prop_id:
                continue
            if payment.kind in {"Rent payment", "Short stay payment"} and payment.status == "Completed":
                income_by_property[prop_id] = income_by_property.get(prop_id, Decimal("0")) + payment.amount
            elif payment.kind == "Worker payout" and payment.status == "Paid":
                payout_by_property[prop_id] = payout_by_property.get(prop_id, Decimal("0")) + payment.amount
        deposit_records = Payment.objects.filter(
            lease__property_id__in=property_ids, kind="Security deposit"
        ).select_related("lease__property", "lease__unit", "lease__tenant").order_by("-created_at")
        held_deposit_by_property, due_deposit_by_property = {}, {}
        for deposit in deposit_records:
            target = held_deposit_by_property if deposit.status == "Completed" else due_deposit_by_property
            target[deposit.lease.property_id] = target.get(deposit.lease.property_id, Decimal("0")) + deposit.amount
        unit_count = occupied_count = 0
        property_rows = []
        for prop in owned:
            units = [unit for floor in prop.floors.all() for unit in floor.units.all()]
            occupied = sum(1 for unit in units if unit.id in active_unit_ids or unit.status == "Rented")
            unit_count += len(units)
            occupied_count += occupied
            property_open_repairs = open_repairs.filter(property_id=prop.id).count()
            prop_leases = [lease for lease in active_leases if lease.property_id == prop.id]
            monthly_rent = sum((lease.rent for lease in prop_leases), Decimal("0"))
            confirmed_stays = sum(1 for stay in stays if stay.property_id == prop.id)
            property_rows.append([prop.title, f"{prop.city}, {prop.county or prop.location}", prop.get_status_display(), len(units), occupied, len(units)-occupied, len(prop_leases), monthly_rent, rent_due_by_property.get(prop.id, Decimal("0")), held_deposit_by_property.get(prop.id, Decimal("0")), due_deposit_by_property.get(prop.id, Decimal("0")), confirmed_stays, income_by_property.get(prop.id, Decimal("0")), payout_by_property.get(prop.id, Decimal("0")), property_open_repairs])
        collected = sum((p.amount for p in payments if p.kind in {"Rent payment", "Short stay payment"} and p.status == "Completed"), Decimal("0"))
        card("Properties", owned.count())
        card("Units occupied", f"{occupied_count} / {unit_count}", f"{(occupied_count / unit_count * 100):.0f}% occupancy" if unit_count else "Add units to track occupancy")
        card("Contracted monthly rent", sum((lease.rent for lease in active_leases), Decimal("0")), "Active long-term leases")
        card("Rent due now", sum(rent_due_by_property.values(), Decimal("0")), "Overdue or due today")
        card("Deposits held", sum(held_deposit_by_property.values(), Decimal("0")), "Recorded as received")
        card("Deposits outstanding", sum(due_deposit_by_property.values(), Decimal("0")), "Active leases without confirmed deposit receipt")
        card("Collected in period", collected, f"KES · {start:%d %b} to {end:%d %b %Y}")
        card("Worker payouts paid", sum(payout_by_property.values(), Decimal("0")), "Recorded worker payments in period")
        card("Open repair backlog", open_repairs.count(), "Across all your properties")
        table("Property by property", ["Property", "Location", "Listing status", "Units", "Occupied", "Vacant", "Active leases", "Monthly rent (KES)", "Rent due now (KES)", "Deposits held (KES)", "Deposits outstanding (KES)", "Confirmed stays in period", "Collected in period (KES)", "Worker payouts paid in period (KES)", "Open repairs"], property_rows)
        table("Maintenance activity", ["Property", "Unit", "Report", "Priority", "Status", "Reported"], [[r.property.title if r.property_id else r.property_name, r.unit.unit_number if r.unit_id else "—", r.title, r.priority, r.status, r.created_at.strftime("%d %b %Y")] for r in requests])
        table("Worker jobs", ["Property", "Job", "Worker", "Status", "Payout (KES)", "Posted"], [[j.property.title if j.property_id else j.location, j.title, (j.worker.get_full_name() or j.worker.username) if j.worker_id else "Unassigned", j.status, j.payout, j.created_at.strftime("%d %b %Y")] for j in jobs])
        table("Rental applications", ["Property", "Unit", "Applicant", "Status", "Submitted"], [[a.property.title, a.unit.unit_number if a.unit_id else "—", a.tenant.get_full_name() or a.tenant.username, a.status, a.created_at.strftime("%d %b %Y")] for a in applications])
        table("Worker payout ledger", ["Property", "Job", "Worker", "Status", "Amount (KES)", "Date"], [[p.job.property.title if p.job_id and p.job.property_id else "—", p.job.title if p.job_id else p.entity, p.job.worker.get_full_name() or p.job.worker.username if p.job_id and p.job.worker_id else "—", p.status, p.amount, p.created_at.strftime("%d %b %Y")] for p in payments if p.kind == "Worker payout"])
        table("Monthly rent receipts", ["Property", "Unit", "Tenant", "Rent month", "Status", "Amount (KES)", "Received", "Method / reference"], [[p.lease.property.title, p.lease.unit.unit_number if p.lease.unit_id else "—", p.lease.tenant.get_full_name() or p.lease.tenant.username if p.lease.tenant_id else "—", p.billing_period.strftime("%B %Y") if p.billing_period else "—", p.status, p.amount, p.paid_at.strftime("%d %b %Y") if p.paid_at else "—", p.payment_reference or p.provider_receipt or p.payment_method or "—"] for p in payments if p.kind == "Rent payment" and p.lease_id])
        table("Security deposit register", ["Property", "Unit", "Tenant", "Status", "Deposit (KES)", "Received", "Method / reference"], [[p.lease.property.title, p.lease.unit.unit_number if p.lease.unit_id else "—", p.lease.tenant.get_full_name() or p.lease.tenant.username if p.lease.tenant_id else "—", p.status, p.amount, p.paid_at.strftime("%d %b %Y") if p.paid_at else "—", p.payment_reference or p.provider_receipt or p.payment_method or "—"] for p in deposit_records])
    elif role == "tenant":
        leases = Lease.objects.filter(tenant=request.user).select_related("property", "unit").order_by("-start_date")
        active_leases = list(leases.filter(status="Active"))
        dues = build_rent_schedule(leases, today=today, include_paid_history=True)
        payments = dated(Payment.objects.filter(user=request.user).select_related("lease__property", "booking__property").order_by("-created_at"))
        requests = dated(MaintenanceRequest.objects.filter(requester=request.user).select_related("property", "unit").order_by("-created_at"))
        applications = dated(RentalApplication.objects.filter(tenant=request.user).select_related("property", "unit").order_by("-created_at"))
        deposit_records = Payment.objects.filter(lease__tenant=request.user, kind="Security deposit").select_related("lease__property", "lease__unit").order_by("-created_at")
        stays = Booking.objects.filter(tenant=request.user, check_in__lte=end, check_out__gte=start).select_related("property", "unit").order_by("-check_in")
        paid_total = sum((p.amount for p in payments if p.status == "Completed" and p.kind != "Worker payout"), Decimal("0"))
        outstanding = sum((d["amount"] for d in dues if d["status"] in {"Overdue", "Due today"}), Decimal("0"))
        card("Active homes", len(active_leases))
        card("Rent due now", outstanding, "Overdue or due today")
        card("Paid in period", paid_total, "Completed rent and stay payments")
        card("Deposits paid", sum((p.amount for p in deposit_records if p.status == "Completed"), Decimal("0")))
        card("Deposits due", sum((p.amount for p in deposit_records if p.status != "Completed"), Decimal("0")))
        card("Open maintenance reports", MaintenanceRequest.objects.filter(requester=request.user).exclude(status="Resolved").count(), "Current unresolved backlog")
        table("My leases", ["Home", "Unit", "Status", "Monthly rent (KES)", "Current term", "Deposit (KES)", "Due day"], [[l.property.title, l.unit.unit_number if l.unit_id else "—", l.status, l.rent, f"{l.start_date:%d %b %Y} – {current_month_term_end(l, today):%d %b %Y}" if l.status == "Active" and not lease_has_approved_moveout(l) else f"{l.start_date:%d %b %Y} – {l.end_date:%d %b %Y}", l.deposit_amount, l.rent_due_day] for l in leases])
        table("Rent due schedule", ["Home", "Unit", "Period", "Due date", "Amount (KES)", "Status"], [[d["lease"].property.title, d["lease"].unit.unit_number if d["lease"].unit_id else "—", d["period_label"], d["due_date"].strftime("%d %b %Y"), d["amount"], d["status"]] for d in dues])
        table("Payment activity", ["Description", "Type", "Status", "Amount (KES)", "Date", "Receipt"], [[p.lease.property.title if p.lease_id else p.booking.property.title if p.booking_id else p.entity, p.kind, p.status, p.amount, p.created_at.strftime("%d %b %Y"), p.provider_receipt or p.payment_reference or "—"] for p in payments])
        table("Security deposits", ["Home", "Unit", "Status", "Deposit (KES)", "Received", "Method / reference"], [[p.lease.property.title, p.lease.unit.unit_number if p.lease.unit_id else "—", p.status, p.amount, p.paid_at.strftime("%d %b %Y") if p.paid_at else "—", p.payment_reference or p.provider_receipt or p.payment_method or "—"] for p in deposit_records])
        table("Maintenance reports", ["Home", "Unit", "Report", "Priority", "Status", "Reported"], [[r.property.title if r.property_id else r.property_name, r.unit.unit_number if r.unit_id else "—", r.title, r.priority, r.status, r.created_at.strftime("%d %b %Y")] for r in requests])
        table("Rental applications and stays", ["Home", "Type", "Dates / submitted", "Status", "Amount (KES)"], [[a.property.title, "Rental application", a.created_at.strftime("%d %b %Y"), a.status, "—"] for a in applications] + [[b.property.title, "Short stay", f"{b.check_in or b.date:%d %b %Y} – {b.check_out or b.date:%d %b %Y}", b.status, b.total_price or "—"] for b in stays])
    elif role == "worker":
        jobs = dated(Job.objects.filter(worker=request.user).select_related("property").order_by("-created_at"))
        all_payouts = Payment.objects.filter(job__worker=request.user, kind="Worker payout")
        payouts = dated(all_payouts.select_related("job__property").order_by("-created_at"))
        paid = sum((p.amount for p in payouts if p.status == "Paid"), Decimal("0"))
        pending = all_payouts.filter(status="Awaiting payment").aggregate(total=Sum("amount"))["total"] or Decimal("0")
        completed = jobs.filter(status="Completed").count()
        card("Assigned jobs", jobs.count())
        card("Completed jobs", completed)
        card("Awaiting payout", pending, "Completed work not yet marked paid")
        card("Paid in period", paid, "Recorded worker payouts")
        table("My work", ["Job", "Property", "Trade", "Status", "Payout (KES)", "Posted"], [[j.title, j.property.title if j.property_id else j.location, j.trade, j.status, j.payout, j.created_at.strftime("%d %b %Y")] for j in jobs])
        table("Payout history", ["Job", "Property", "Status", "Amount (KES)", "Date", "Reference"], [[p.job.title if p.job_id else p.entity, p.job.property.title if p.job_id and p.job.property_id else "—", p.status, p.amount, p.created_at.strftime("%d %b %Y"), p.payment_reference or p.provider_receipt or "—"] for p in payouts])
    elif role == "management":
        properties = Property.objects.filter(caretaker=request.user).prefetch_related("floors__units")
        property_ids = list(properties.values_list("id", flat=True))
        open_requests = MaintenanceRequest.objects.filter(property_id__in=property_ids).exclude(status="Resolved")
        active_jobs = Job.objects.filter(property_id__in=property_ids, status__in=["Open", "Assigned", "In Progress"])
        requests = dated(MaintenanceRequest.objects.filter(property_id__in=property_ids).select_related("property", "unit", "requester").order_by("-created_at"))
        jobs = dated(Job.objects.filter(property_id__in=property_ids).select_related("property", "worker").order_by("-created_at"))
        units = [u for p in properties for f in p.floors.all() for u in f.units.all()]
        card("Assigned properties", properties.count())
        card("Units", len(units), f"{sum(1 for u in units if u.status == 'Rented')} recorded as occupied")
        card("Open reports", MaintenanceRequest.objects.filter(property_id__in=property_ids).exclude(status="Resolved").count(), "Current unresolved backlog")
        card("Jobs in period", jobs.count(), f"{sum(1 for j in jobs if j.status == 'Completed')} completed")
        table("Building condition", ["Property", "Location", "Units", "Available", "Occupied", "Open reports", "Active jobs"], [[p.title, p.location, sum(len(f.units.all()) for f in p.floors.all()), sum(1 for f in p.floors.all() for u in f.units.all() if u.status == "Available"), sum(1 for f in p.floors.all() for u in f.units.all() if u.status == "Rented"), open_requests.filter(property_id=p.id).count(), active_jobs.filter(property_id=p.id).count()] for p in properties])
        table("Maintenance reports", ["Property", "Unit", "Report", "Priority", "Status", "Reported"], [[r.property.title if r.property_id else r.property_name, r.unit.unit_number if r.unit_id else "—", r.title, r.priority, r.status, r.created_at.strftime("%d %b %Y")] for r in requests])
        table("Worker jobs", ["Property", "Job", "Worker", "Status", "Payout (KES)", "Posted"], [[j.property.title if j.property_id else j.location, j.title, (j.worker.get_full_name() or j.worker.username) if j.worker_id else "Unassigned", j.status, j.payout, j.created_at.strftime("%d %b %Y")] for j in jobs])
    else:
        properties = Property.objects.all()
        payments = dated(Payment.objects.select_related("lease__property", "booking__property", "job__property").order_by("-created_at"))
        requests = dated(MaintenanceRequest.objects.select_related("property").order_by("-created_at"))
        jobs = dated(Job.objects.select_related("property", "worker").order_by("-created_at"))
        role_counts = {key: User.objects.filter(profile__role=key).count() for key, _ in Profile.ROLE_CHOICES}
        role_counts["admin"] = User.objects.filter(Q(is_superuser=True) | Q(profile__role="admin")).distinct().count()
        card("Users", User.objects.count(), "All accounts")
        card("Properties", properties.count(), f"{properties.filter(status='Pending').count()} awaiting review")
        card("Active leases", Lease.objects.filter(status="Active").count())
        card("Open maintenance", MaintenanceRequest.objects.exclude(status="Resolved").count())
        card("Payments recorded", sum((p.amount for p in payments if p.status == "Completed"), Decimal("0")), f"Completed entries in selected period")
        table("Accounts by role", ["Role", "Accounts"], [[label, role_counts.get(key, 0)] for key, label in Profile.ROLE_CHOICES])
        table("Listings by status", ["Status", "Listings"], [[label, properties.filter(status=key).count()] for key, label in Property.STATUS])
        table("Platform activity", ["Type", "Record", "Status", "Amount / priority", "Date"], [["Maintenance", r.property.title if r.property_id else r.property_name, r.status, r.priority, r.created_at.strftime("%d %b %Y")] for r in requests] + [["Worker job", j.property.title if j.property_id else j.location, j.status, f"KES {j.payout}", j.created_at.strftime("%d %b %Y")] for j in jobs] + [[p.kind, p.entity, p.status, f"KES {p.amount}", p.created_at.strftime("%d %b %Y")] for p in payments])

    if request.GET.get("export") == "csv":
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="estatehub-{role}-report-{start.isoformat()}-to-{end.isoformat()}.csv"'
        writer = csv.writer(response)
        def csv_safe(value):
            return "'" + value if isinstance(value, str) and value.startswith(("=", "+", "-", "@")) else value
        writer.writerow(["EstateHub", role.title(), f"{start.isoformat()} to {end.isoformat()}"])
        for item in tables:
            writer.writerow([])
            writer.writerow([item["title"]])
            writer.writerow(item["headers"])
            writer.writerows([[csv_safe(value) for value in row] for row in item["rows"]])
        return response

    return render(request, "portal/reports.html", {
        "role": role, "report_start": start, "report_end": end,
        "report_start_value": start.isoformat(), "report_end_value": end.isoformat(),
        "cards": cards, "tables": tables,
    })


@login_required
def profile(request):
    default_role = "admin" if request.user.is_superuser else "tenant"
    profile, _ = Profile.objects.get_or_create(
        user=request.user,
        defaults={"role": default_role, "approved": True},
    )
    role = profile.role
    user = request.user
    open_section = ""
    details_form = ProfileDetailsForm(initial={
        "first_name": user.first_name,
        "last_name": user.last_name,
        "email": user.email,
        "phone": profile.phone,
        "bio": profile.bio,
    })
    password_form = PasswordChangeForm(user=user)

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "details":
            details_form = ProfileDetailsForm(request.POST)
            open_section = "details"
            if details_form.is_valid():
                user.first_name = details_form.cleaned_data["first_name"]
                user.last_name = details_form.cleaned_data["last_name"]
                user.email = details_form.cleaned_data["email"]
                user.save(update_fields=["first_name", "last_name", "email"])
                profile.phone = details_form.cleaned_data["phone"]
                profile.bio = details_form.cleaned_data["bio"]
                profile.save(update_fields=["phone", "bio"])
                messages.success(request, "Your profile details have been updated.")
                return redirect("profile")
        elif action == "password":
            password_form = PasswordChangeForm(user=user, data=request.POST)
            open_section = "password"
            if password_form.is_valid():
                password_form.save()
                update_session_auth_hash(request, user)
                messages.success(request, "Your password has been changed.")
                return redirect("profile")
        elif action == "delete_account":
            block_reason = ""
            if user.is_superuser or role == "admin":
                block_reason = "Admin accounts cannot be deleted from their own profile. Ask another administrator to manage this account."
            elif role == "tenant" and Lease.objects.filter(tenant=user, status="Active").exists():
                block_reason = "Close your active lease before deleting your account."
            elif role == "tenant" and MaintenanceRequest.objects.filter(requester=user).exclude(status="Resolved").exists():
                block_reason = "Wait until your open maintenance requests are resolved before deleting your account."
            elif role == "landlord" and Lease.objects.filter(property__owner=user, status="Active").exists():
                block_reason = "End or transfer your active leases before deleting your account."
            elif role == "landlord" and Job.objects.filter(property__owner=user, status__in=["Assigned", "In Progress"]).exists():
                block_reason = "Wait until active worker jobs are completed before deleting your account."
            elif role == "worker" and Job.objects.filter(worker=user, status__in=["Assigned", "In Progress"]).exists():
                block_reason = "Complete or have your active jobs reassigned before deleting your account."

            if block_reason:
                messages.error(request, block_reason)
            elif not user.check_password(request.POST.get("current_password", "")):
                messages.error(request, "Enter your current password to delete your account.")
            elif request.POST.get("confirm_delete") != "yes":
                messages.error(request, "Confirm that you understand account deletion is permanent.")
            else:
                with transaction.atomic():
                    if role == "landlord":
                        Property.objects.filter(owner=user).delete()
                    user.delete()
                logout(request)
                return redirect("landing")

    if request.method != "POST" or request.POST.get("action") not in {"details", "password"}:
        details_form = ProfileDetailsForm(initial={
            "first_name": user.first_name,
            "last_name": user.last_name,
            "email": user.email,
            "phone": profile.phone,
            "bio": profile.bio,
        })
    if request.method != "POST" or request.POST.get("action") != "password":
        password_form = PasswordChangeForm(user=user)

    stats = []
    active_leases = Lease.objects.none()
    applications = RentalApplication.objects.none()
    properties = Property.objects.none()
    recent_requests = MaintenanceRequest.objects.none()
    recent_jobs = Job.objects.none()
    recent_payments = Payment.objects.none()

    if role == "tenant":
        active_leases = Lease.objects.filter(tenant=user, status="Active").select_related("property")
        applications = RentalApplication.objects.filter(tenant=user).select_related("property").order_by("-created_at")[:5]
        recent_requests = MaintenanceRequest.objects.filter(requester=user).select_related("property").order_by("-created_at")[:5]
        recent_payments = Payment.objects.filter(user=user).order_by("-created_at")[:5]
        payment_count = Payment.objects.filter(user=user).count()
        stats = [
            {"label": "Active rentals", "value": active_leases.count(), "href": "/leases/"},
            {"label": "Rental applications", "value": RentalApplication.objects.filter(tenant=user).count(), "href": "/properties/"},
            {"label": "Maintenance reports", "value": MaintenanceRequest.objects.filter(requester=user).count(), "href": "/maintenance/"},
            {"label": "Rent ledger entries", "value": payment_count, "href": "/payments/"},
        ]
    elif role == "landlord":
        properties = Property.objects.filter(owner=user).order_by("-created_at")
        active_leases = Lease.objects.filter(property__owner=user, status="Active").select_related("property", "tenant")
        applications = RentalApplication.objects.filter(property__owner=user).select_related("property", "tenant").order_by("-created_at")[:5]
        recent_requests = MaintenanceRequest.objects.filter(property__owner=user).select_related("property").order_by("-created_at")[:5]
        recent_jobs = Job.objects.filter(property__owner=user).select_related("property", "worker").order_by("-created_at")[:5]
        stats = [
            {"label": "My properties", "value": properties.count(), "href": "/properties/"},
            {"label": "Active leases", "value": active_leases.count(), "href": "/leases/"},
            {"label": "Pending applications", "value": RentalApplication.objects.filter(property__owner=user, status="Pending").count(), "href": "/dashboard/"},
            {"label": "Maintenance requests", "value": MaintenanceRequest.objects.filter(property__owner=user).exclude(status="Resolved").count(), "href": "/maintenance/"},
        ]
    elif role == "management":
        properties = Property.objects.filter(caretaker=user).order_by("-created_at")
        recent_requests = MaintenanceRequest.objects.filter(property__caretaker=user).select_related("property", "requester").order_by("-created_at")[:5]
        recent_jobs = Job.objects.filter(property__caretaker=user).select_related("property", "worker").order_by("-created_at")[:5]
        stats = [
            {"label": "Assigned properties", "value": properties.count(), "href": "/dashboard/management/"},
            {"label": "Available units", "value": RentalUnit.objects.filter(floor__property__caretaker=user, status="Available").count(), "href": "/maintenance/"},
            {"label": "Open reports", "value": MaintenanceRequest.objects.filter(property__caretaker=user).exclude(status="Resolved").count(), "href": "/maintenance/"},
            {"label": "Worker jobs", "value": Job.objects.filter(property__caretaker=user).count(), "href": "/jobs/"},
        ]
    elif role == "worker":
        recent_jobs = Job.objects.filter(worker=user).order_by("-created_at")[:5]
        stats = [
            {"label": "Jobs available", "value": Job.objects.filter(status="Open", worker__isnull=True).count(), "href": "/jobs/"},
            {"label": "Assigned to me", "value": Job.objects.filter(worker=user, status__in=["Assigned", "In Progress"]).count(), "href": "/jobs/"},
            {"label": "Work completed", "value": Job.objects.filter(worker=user, status="Completed").count(), "href": "/jobs/"},
        ]
    elif role == "admin":
        stats = [
            {"label": "Users", "value": User.objects.count(), "href": "/admin/auth/user/"},
            {"label": "Properties", "value": Property.objects.count(), "href": "/oversight/"},
            {"label": "Active leases", "value": Lease.objects.filter(status="Active").count(), "href": "/oversight/"},
            {"label": "Open requests", "value": MaintenanceRequest.objects.exclude(status="Resolved").count(), "href": "/oversight/"},
        ]

    if active_leases:
        today = timezone.localdate()
        for lease in active_leases:
            lease.current_term_end = (
                current_month_term_end(lease, today)
                if not lease_has_approved_moveout(lease)
                else lease.end_date
            )

    deletion_block_reason = ""
    if user.is_superuser or role == "admin":
        deletion_block_reason = "Admin accounts must be removed by another administrator."
    elif role == "tenant" and Lease.objects.filter(tenant=user, status="Active").exists():
        deletion_block_reason = "You have an active lease. Close or transfer it before deleting your account."
    elif role == "tenant" and MaintenanceRequest.objects.filter(requester=user).exclude(status="Resolved").exists():
        deletion_block_reason = "You have unresolved maintenance requests. Wait until they are resolved before deleting your account."
    elif role == "landlord" and Lease.objects.filter(property__owner=user, status="Active").exists():
        deletion_block_reason = "You have properties with active leases. End or transfer them before deleting your account."
    elif role == "landlord" and Job.objects.filter(property__owner=user, status__in=["Assigned", "In Progress"]).exists():
        deletion_block_reason = "You have active worker jobs. Wait until they are completed before deleting your account."
    elif role == "management" and Property.objects.filter(caretaker=user).exists():
        deletion_block_reason = "Ask the landlord to reassign your properties before deleting this caretaker account."
    elif role == "worker" and Job.objects.filter(worker=user, status__in=["Assigned", "In Progress"]).exists():
        deletion_block_reason = "You have active jobs. Complete them or have them reassigned before deleting your account."

    return render(request, "portal/profile.html", {
        "profile": profile,
        "role": role,
        "stats": stats,
        "active_leases": active_leases,
        "applications": applications,
        "properties": properties,
        "recent_requests": recent_requests,
        "recent_jobs": recent_jobs,
        "recent_payments": recent_payments,
        "details_form": details_form,
        "password_form": password_form,
        "open_section": open_section,
        "deletion_block_reason": deletion_block_reason,
    })


@login_required
def settings_page(request): return render(request, "portal/settings.html")


@admin_required
def oversight(request):
    context = {
        "pending_landlords": Profile.objects.filter(role="landlord", approved=False, rejected_at__isnull=True).select_related("user"),
        "pending_properties": Property.objects.filter(status="Pending").select_related("owner").prefetch_related("images"),
        "requests": MaintenanceRequest.objects.all().select_related("property", "requester", "unit", "unit__floor").order_by("-created_at")[:30],
        "jobs": Job.objects.all().select_related("property", "worker").order_by("-created_at")[:30],
        "counts": {
            "users": User.objects.count(),
            "properties": Property.objects.count(),
            "leases": Lease.objects.filter(status="Active").count(),
            "open_requests": MaintenanceRequest.objects.exclude(status="Resolved").count(),
            **admin_account_counts(),
        },
    }
    return render(request, "portal/oversight.html", context)


@admin_required
def landlord_decision(request, user_id, decision):
    if request.method != "POST" or decision not in {"approve", "reject"}:
        raise PermissionDenied
    profile = get_object_or_404(Profile, user_id=user_id, role="landlord")
    if decision == "approve":
        profile.approved = True
        profile.approved_at = timezone.now()
        profile.rejected_at = None
        profile.approved_by = request.user
        profile.save(update_fields=["approved", "approved_at", "rejected_at", "approved_by"])
        notify_user(profile.user, "Your landlord account has been approved. You can now manage and submit properties.", "/dashboard/")
        messages.success(request, f"Landlord account {profile.user.username} approved.")
    else:
        profile.approved = False
        profile.rejected_at = timezone.now()
        profile.save(update_fields=["approved", "rejected_at"])
        notify_user(profile.user, "Your landlord account was not approved. Contact EstateHub support if you need help.", "/support/")
        messages.success(request, "Landlord account rejected. The user can sign in to view the decision.")
    return redirect("oversight")


@admin_required
def property_decision(request, pk, decision):
    if request.method == "GET" and decision == "approve":
        prop = get_object_or_404(Property.objects.select_related("owner"), pk=pk)
        owner_profile = getattr(prop.owner, "profile", None) if prop.owner_id else None
        if prop.status != "Pending":
            messages.info(request, "This property is no longer waiting for review.")
            return redirect("oversight")
        if prop.owner_id and (owner_profile is None or not owner_profile.approved):
            messages.error(request, "Approve this landlord account before approving their property.")
            return redirect("oversight")
        return render(request, "portal/confirm_property_approval.html", {"property": prop})
    if request.method != "POST" or decision not in {"approve", "reject", "delete"}:
        raise PermissionDenied
    prop = get_object_or_404(Property, pk=pk)
    if decision == "approve":
        owner_profile = getattr(prop.owner, "profile", None) if prop.owner_id else None
        if prop.owner_id and (owner_profile is None or not owner_profile.approved):
            messages.error(request, "Approve this landlord account before approving their property.")
        else:
            prop.status = "Available"
            prop.save(update_fields=["status"])
            RentalUnit.objects.filter(floor__property=prop, status="Pending").update(status="Available")
            if prop.owner_id:
                notify_user(prop.owner, f"Your property ‘{prop.title}’ has been approved and is now visible to tenants.", f"/property/{prop.pk}/")
            messages.success(request, "Property approved and published to tenants.")
    elif decision == "reject":
        prop.status = "Rejected"
        prop.save(update_fields=["status"])
        RentalUnit.objects.filter(floor__property=prop, status="Pending").update(status="Unavailable")
        if prop.owner_id:
            notify_user(prop.owner, f"Your property ‘{prop.title}’ was not approved. Please review it and contact support if needed.", "/properties/")
        messages.success(request, "Property submission rejected.")
    else:
        if prop.owner_id:
            notify_user(prop.owner, f"Your property listing ‘{prop.title}’ was removed by an administrator.", "/properties/")
        prop.delete()
        messages.success(request, "Property removed.")
    return redirect("oversight")


@login_required
def notification_center(request):
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "mark_all_read":
            Notification.objects.filter(user=request.user, read=False).update(read=True)
            messages.success(request, "All notifications marked as read.")
        elif action == "mark_read":
            item = get_object_or_404(Notification, pk=request.POST.get("notification_id"), user=request.user)
            item.read = True
            item.save(update_fields=["read"])
        return redirect("notifications")

    notifications = Notification.objects.filter(user=request.user).order_by("read", "-created_at")
    return render(request, "portal/notifications.html", {
        "notifications": notifications,
        "unread_notification_count": notifications.filter(read=False).count(),
    })


@login_required
def open_notification(request, pk):
    item = get_object_or_404(Notification, pk=pk, user=request.user)
    if not item.read:
        item.read = True
        item.save(update_fields=["read"])
    if item.url.startswith("/") and not item.url.startswith("//"):
        return redirect(item.url)
    return redirect("notifications")


def logout_view(request):
    logout(request)
    return redirect("landing")
