from django import forms

from .models import Job, Property, RentalUnit


class CaretakerJobForm(forms.ModelForm):
    property = forms.ModelChoiceField(queryset=Property.objects.none(), label="Assigned property")
    unit = forms.ModelChoiceField(
        queryset=RentalUnit.objects.none(),
        required=False,
        label="Unit (optional)",
        empty_label="Building-wide / common area",
    )
    work_area = forms.CharField(
        max_length=120,
        label="Work area",
        help_text="For example: roof above Block A, lobby, stairwell, or parking area.",
    )

    class Meta:
        model = Job
        fields = ["property", "unit", "title", "work_area", "trade", "payout", "urgent", "description"]
        labels = {
            "title": "Job title",
            "trade": "Trade / skill needed",
            "payout": "Estimated worker payout (KES)",
        }
        widgets = {"description": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *args, caretaker, **kwargs):
        super().__init__(*args, **kwargs)
        assigned = Property.objects.filter(caretaker=caretaker).exclude(
            status__in=["Rejected", "Sold"]
        ).order_by("title")
        self.fields["property"].queryset = assigned
        self.fields["unit"].queryset = RentalUnit.objects.filter(
            floor__property__caretaker=caretaker
        ).select_related("floor", "floor__property")
        self.fields["unit"].label_from_instance = (
            lambda unit: f"{unit.floor.property.title} · Floor {unit.floor.number} · Unit {unit.unit_number}"
        )
        if assigned.count() == 1 and not self.is_bound:
            self.fields["property"].initial = assigned.first()

    def clean(self):
        cleaned = super().clean()
        prop = cleaned.get("property")
        unit = cleaned.get("unit")
        if prop and unit and unit.floor.property_id != prop.id:
            self.add_error("unit", "Choose a unit from the selected property.")
        return cleaned
