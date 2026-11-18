from django.contrib import admin
from django.contrib.auth.models import Permission, User
from django.db.models import Q
from django.urls import reverse
from django.utils.html import format_html

from .models import (
    Booking,
    Favorite,
    Job,
    Lease,
    LeaseChangeRequest,
    MaintenanceRequest,
    Message,
    Notification,
    Payment,
    Profile,
    Floor,
    Property,
    PropertyImage,
    RentalUnit,
    RentalApplication,
    WorkerJobRequest,
)
from .notifications import notify_user

admin.site.site_header = "EstateHub Admin"
admin.site.site_title = "EstateHub Admin"
admin.site.index_title = "Platform management"


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "created_by", "must_change_password", "approved", "approved_at", "rejected_at")
    list_filter = ("role", "approved")
    search_fields = ("user__username", "user__email", "phone")
    autocomplete_fields = ("user", "approved_by")
    actions = ("grant_estatehub_admin_access",)

    @admin.action(description="Grant selected accounts EstateHub admin access")
    def grant_estatehub_admin_access(self, request, queryset):
        profiles = queryset.select_related("user")
        for profile in profiles:
            profile.role = "admin"
            profile.approved = True
            profile.save(update_fields=["role", "approved"])
            self._grant_admin_access(profile.user)
        self.message_user(request, f"Admin access granted to {profiles.count()} account(s).")

    @staticmethod
    def _grant_admin_access(user):
        if not user.is_staff:
            user.is_staff = True
            user.save(update_fields=["is_staff"])
        permissions = Permission.objects.filter(
            Q(content_type__app_label="core")
            | Q(content_type__app_label="auth", content_type__model="user")
        )
        user.user_permissions.add(*permissions)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "user":
            users = User.objects.filter(profile__isnull=True)
            match = getattr(request, "resolver_match", None)
            object_id = match.kwargs.get("object_id") if match else None
            if object_id:
                current_user_id = self.get_queryset(request).filter(pk=object_id).values_list("user_id", flat=True).first()
                if current_user_id:
                    users = users | User.objects.filter(pk=current_user_id)
            kwargs["queryset"] = users.distinct()
            add_user_url = reverse("admin:auth_user_add")
            kwargs["help_text"] = format_html(
                'Search by username, first name, last name, or email, then select the account. '
                'Names cannot create accounts in this field. '
                '<a href="{}" target="_blank">Create the user account first</a> if it is missing.',
                add_user_url,
            )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if obj.role == "admin":
            self._grant_admin_access(obj.user)


@admin.action(description="Approve selected property listings")
def approve_properties(modeladmin, request, queryset):
    eligible = queryset.filter(status="Pending").filter(
        Q(owner__isnull=True) | Q(owner__profile__approved=True)
    )
    properties = list(eligible.select_related("owner"))
    property_ids = [prop.pk for prop in properties]
    eligible.update(status="Available")
    RentalUnit.objects.filter(floor__property_id__in=property_ids, status="Pending").update(status="Available")
    for prop in properties:
        if prop.owner_id:
            notify_user(prop.owner, f"Your property ‘{prop.title}’ has been approved and is now visible to tenants.", f"/property/{prop.pk}/")


@admin.action(description="Reject selected property listings")
def reject_properties(modeladmin, request, queryset):
    properties = list(queryset.filter(status="Pending").select_related("owner"))
    property_ids = [prop.pk for prop in properties]
    queryset.filter(status="Pending").update(status="Rejected")
    RentalUnit.objects.filter(floor__property_id__in=property_ids, status="Pending").update(status="Unavailable")
    for prop in properties:
        if prop.owner_id:
            notify_user(prop.owner, f"Your property ‘{prop.title}’ was not approved. Please review it and contact support if needed.", "/properties/")


class PropertyImageInline(admin.TabularInline):
    model = PropertyImage
    extra = 0


class FloorInline(admin.TabularInline):
    model = Floor
    extra = 0


@admin.register(Property)
class PropertyAdmin(admin.ModelAdmin):
    list_display = ("title", "owner", "caretaker", "county", "city", "rent", "status", "created_at")
    list_filter = ("status", "property_type", "county", "city")
    search_fields = ("title", "location", "county", "city", "owner__username", "caretaker__username")
    autocomplete_fields = ("owner", "caretaker")
    actions = (approve_properties, reject_properties)
    inlines = (PropertyImageInline, FloorInline)


@admin.register(RentalUnit)
class RentalUnitAdmin(admin.ModelAdmin):
    list_display = ("unit_number", "floor", "rent", "status", "bedrooms", "bathrooms")
    list_filter = ("status", "floor__property")
    search_fields = ("unit_number", "floor__property__title")


@admin.register(MaintenanceRequest)
class MaintenanceRequestAdmin(admin.ModelAdmin):
    list_display = ("title", "property", "unit", "requester", "priority", "status", "created_at")
    list_filter = ("priority", "status", "category")
    search_fields = ("title", "property_name", "requester__username")


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = ("title", "property", "unit", "trade", "payout", "status", "worker", "created_at")
    list_filter = ("status", "trade", "urgent")
    search_fields = ("title", "location", "worker__username", "posted_by__username")


@admin.register(WorkerJobRequest)
class WorkerJobRequestAdmin(admin.ModelAdmin):
    list_display = ("job", "worker", "status", "created_at", "reviewed_at", "reviewed_by")
    list_filter = ("status", "created_at")
    search_fields = ("job__title", "worker__username", "worker__email")


@admin.register(RentalApplication)
class RentalApplicationAdmin(admin.ModelAdmin):
    list_display = ("property", "unit", "tenant", "status", "created_at", "reviewed_at")
    list_filter = ("status", "created_at")
    search_fields = ("property__title", "tenant__username", "tenant__email")


@admin.register(Lease)
class LeaseAdmin(admin.ModelAdmin):
    list_display = ("property", "unit", "tenant", "start_date", "displayed_end_date", "rent", "rent_due_day", "status")
    list_filter = ("status", "start_date", "end_date")
    search_fields = ("property__title", "tenant__username")

    @admin.display(description="Current term end", ordering="end_date")
    def displayed_end_date(self, lease):
        if lease.status == "Active" and not lease.change_requests.filter(request_type="move_out", status="Approved").exists():
            return lease.monthly_term_end()
        return lease.end_date


@admin.register(LeaseChangeRequest)
class LeaseChangeRequestAdmin(admin.ModelAdmin):
    list_display = ("lease", "request_type", "proposed_end_date", "proposed_rent", "status", "created_at", "reviewed_at")
    list_filter = ("request_type", "status", "created_at")
    search_fields = ("lease__property__title", "lease__tenant__username", "message", "landlord_response")
    readonly_fields = ("created_at", "reviewed_at", "reviewed_by")


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("entity", "kind", "user", "amount", "status", "payment_method", "provider_receipt", "paid_at", "created_at")
    list_filter = ("status", "kind", "created_at")
    search_fields = ("entity", "user__username", "job__title", "payment_reference", "provider_receipt", "checkout_request_id", "provider_conversation_id")
    readonly_fields = ("checkout_request_id", "merchant_request_id", "provider_conversation_id", "provider_receipt", "provider_result_code", "provider_result_description", "provider_initiated_at")


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("user", "text", "read", "created_at")
    list_filter = ("read", "created_at")
    search_fields = ("user__username", "text")


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ("property", "unit", "tenant", "check_in", "check_out", "guests", "total_price", "status")
    list_filter = ("status", "property__rental_mode", "check_in", "check_out")
    search_fields = ("property__title", "tenant__username", "tenant__email", "unit__unit_number")


for model in (Favorite, Message):
    admin.site.register(model)
