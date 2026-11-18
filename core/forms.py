from django import forms
from django.core.exceptions import ValidationError
from django.contrib.auth.models import User

from .models import Floor, Lease, MaintenanceRequest, Profile, Property, RentalUnit
from .locations import KENYA_COUNTIES


class MultiFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class PropertyPhotoField(forms.FileField):
    widget = MultiFileInput

    def clean(self, data, initial=None):
        if not data:
            return []
        uploads = data if isinstance(data, (list, tuple)) else [data]
        if len(uploads) > 8:
            raise ValidationError("Upload up to 8 photos at a time.")
        cleaned = []
        for upload in uploads:
            photo = super().clean(upload, initial)
            if photo.size > 8 * 1024 * 1024:
                raise ValidationError(f"{photo.name} is larger than 8 MB.")
            header = photo.read(12)
            photo.seek(0)
            valid = (
                    header.startswith(b"\xff\xd8\xff")
                    or header.startswith(b"\x89PNG\r\n\x1a\n")
                    or header.startswith((b"GIF87a", b"GIF89a"))
                    or (header.startswith(b"RIFF") and header[8:12] == b"WEBP")
                    )
            if not valid:
                raise ValidationError(f"{photo.name} is not a supported image. Use JPG, PNG, GIF, or WebP.")
            cleaned.append(photo)
        return cleaned


class RegisterForm(forms.ModelForm):
    email = forms.EmailField()
    password = forms.CharField(widget=forms.PasswordInput)
    role = forms.ChoiceField(choices=[
        ("tenant", "Tenant"),
        ("landlord", "Landlord"),
        ("worker", "Worker"),
        ])

    class Meta:
        model = User
        fields = ["username", "email", "password", "role"]

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        user.set_password(self.cleaned_data["password"])
        if commit:
            user.save()
            role = self.cleaned_data["role"]
            Profile.objects.create(user=user, role=role, approved=(role != "landlord"))
        return user


class ProfileDetailsForm(forms.Form):
    first_name = forms.CharField(max_length=150, required=False, label="First name")
    last_name = forms.CharField(max_length=150, required=False, label="Last name")
    email = forms.EmailField(required=False, label="Email address")
    phone = forms.CharField(max_length=40, required=False, label="Phone number")
    bio = forms.CharField(required=False, label="About you", widget=forms.Textarea(attrs={"rows": 4}))


class MaintenanceForm(forms.ModelForm):
    lease = forms.ModelChoiceField(queryset=Lease.objects.none(), label="Rented property")

    class Meta:
        model = MaintenanceRequest
        fields = ["lease", "title", "category", "priority", "description"]

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user is not None:
            self.fields["lease"].queryset = Lease.objects.filter(
                    tenant=user, status="Active", unit__status="Rented"
                    ).select_related("property", "unit")
            self.fields["lease"].label_from_instance = lambda lease: f"{lease.property.title} — Unit {lease.unit.unit_number if lease.unit_id else '1'} ({lease.property.location})"


class PropertyForm(forms.ModelForm):
    county = forms.ChoiceField(
            choices=[("", "Choose a county")] + [(county, county) for county in KENYA_COUNTIES],
            label="County",
            widget=forms.TextInput(attrs={"list": "kenya-counties", "autocomplete": "off", "placeholder": "Type to search counties"}),
            )
    photos = PropertyPhotoField(
            required=False,
            label="Property photos",
            help_text="Choose up to 8 JPG, PNG, GIF, or WebP images. Each can be up to 8 MB.",
            widget=MultiFileInput(attrs={"accept": "image/jpeg,image/png,image/gif,image/webp"}),
            )
    class Meta:
        model = Property
        fields = ["rental_mode", "title", "county", "city", "location", "property_type", "rent", "bedrooms", "bathrooms", "sqft", "parking", "description", "amenities"]
        labels = {"rent": "Starting monthly rent (KES)", "city": "Town / city", "location": "Neighborhood / street address"}
        help_texts = {"rent": "This is the price for the first unit. You can set individual unit prices after creating the property."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        rental_mode = self.data.get("rental_mode") or self.initial.get("rental_mode") or getattr(self.instance, "rental_mode", "long_term")
        if rental_mode == "short_stay":
            self.fields["rent"].label = "Starting nightly price (KES)"
            self.fields["rent"].help_text = "Price per night for the first stay unit. Availability dates and guest bookings are managed separately."


class CaretakerAssignmentForm(forms.ModelForm):
    caretaker = forms.ModelChoiceField(queryset=User.objects.none(), required=False, label="Caretaker account", empty_label="No caretaker assigned")

    def __init__(self, *args, landlord=None, **kwargs):
        super().__init__(*args, **kwargs)
        if landlord is not None:
            self.fields["caretaker"].queryset = User.objects.filter(
                    profile__role="management", profile__created_by=landlord, is_active=True
                    ).order_by("username")

    class Meta:
        model = Property
        fields = ["caretaker"]


class CaretakerCreateForm(forms.Form):
    username = forms.CharField(max_length=150)
    email = forms.EmailField()
    first_name = forms.CharField(max_length=150)
    last_name = forms.CharField(max_length=150, required=False)
    phone = forms.CharField(max_length=40, required=False)

    def clean_username(self):
        username = self.cleaned_data["username"]
        if User.objects.filter(username__iexact=username).exists():
            raise ValidationError("That username is already in use.")
        return username

    def clean_email(self):
        email = self.cleaned_data["email"]
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError("That email address is already in use.")
        return email


class RentalUnitForm(forms.Form):
    floor_number = forms.IntegerField(min_value=0, label="Floor number")
    floor_label = forms.CharField(max_length=80, required=False, label="Floor name (optional)")
    unit_number = forms.CharField(max_length=40, label="House / unit number")
    rent = forms.DecimalField(max_digits=12, decimal_places=2, min_value=0.01, label="Monthly rent (KES)")
    bedrooms = forms.IntegerField(min_value=0, initial=1)
    bathrooms = forms.DecimalField(max_digits=4, decimal_places=1, min_value=0, initial=1)
    sqft = forms.IntegerField(min_value=1, initial=500, label="Area (sqft)")
    parking = forms.IntegerField(min_value=0, initial=0)
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, property=None, **kwargs):
        self.property = property
        super().__init__(*args, **kwargs)
        if property and property.rental_mode == "short_stay":
            self.fields["rent"].label = "Price per night (KES)"

    def clean(self):
        cleaned = super().clean()
        if self.property and cleaned.get("floor_number") is not None and cleaned.get("unit_number"):
            exists = RentalUnit.objects.filter(
                    floor__property=self.property,
                    floor__number=cleaned["floor_number"],
                    unit_number__iexact=cleaned["unit_number"],
                    ).exists()
            if exists:
                self.add_error("unit_number", "That unit number already exists on this floor.")
        return cleaned

    def save(self, property, status="Available"):
        floor, _ = Floor.objects.get_or_create(
                property=property,
                number=self.cleaned_data["floor_number"],
                defaults={"label": self.cleaned_data["floor_label"]},
                )
        if self.cleaned_data["floor_label"] and floor.label != self.cleaned_data["floor_label"]:
            floor.label = self.cleaned_data["floor_label"]
            floor.save(update_fields=["label"])
        return RentalUnit.objects.create(
                floor=floor,
                unit_number=self.cleaned_data["unit_number"],
                rent=self.cleaned_data["rent"],
                bedrooms=self.cleaned_data["bedrooms"],
                bathrooms=self.cleaned_data["bathrooms"],
                sqft=self.cleaned_data["sqft"],
                parking=self.cleaned_data["parking"],
                description=self.cleaned_data["description"],
                status=status,
                )


class RentalUnitEditForm(forms.ModelForm):
    class Meta:
        model = RentalUnit
        fields = ["unit_number", "rent", "bedrooms", "bathrooms", "sqft", "parking", "description"]
        labels = {"rent": "Monthly rent (KES)", "unit_number": "House / unit number", "sqft": "Area (sqft)"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and self.instance.floor.property.rental_mode == "short_stay":
            self.fields["rent"].label = "Price per night (KES)"


class JobForm(forms.Form):
    trade = forms.CharField(max_length=80)
    payout = forms.DecimalField(max_digits=10, decimal_places=2, min_value=0.01, label="Worker payout (KES)")
    urgent = forms.BooleanField(required=False)
    description = forms.CharField(widget=forms.Textarea, required=False)


class ApplicationForm(forms.Form):
    message = forms.CharField(widget=forms.Textarea, required=False, label="Note for the landlord")
