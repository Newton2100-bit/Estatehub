from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.core.management.base import BaseCommand, CommandError

from core.models import Floor, Property, PropertyImage, RentalUnit


class Command(BaseCommand):
    help = "Create a clearly labeled Githurai demo listing for the Mwaura landlord account."

    def handle(self, *args, **options):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        owner = User.objects.filter(username__iexact="mwaura").first()
        if owner is None:
            raise CommandError("No landlord account named Mwaura exists in this database.")

        prop, created = Property.objects.get_or_create(
            owner=owner,
            title="Demo apartment in Githurai",
            defaults={
                "location": "Githurai, Nairobi",
                "city": "Nairobi",
                "property_type": "Apartment",
                "rent": 15000,
                "bedrooms": 1,
                "bathrooms": 1,
                "sqft": 550,
                "parking": 0,
                "status": "Pending",
                "description": (
                    "Demonstration listing only. Replace these sample details and stock images "
                    "with verified property information and photos before publishing."
                ),
            },
        )

        floor, _ = Floor.objects.get_or_create(
            property=prop, number=1, defaults={"label": "Ground floor"}
        )
        RentalUnit.objects.get_or_create(
            floor=floor,
            unit_number="1",
            defaults={
                "rent": prop.rent,
                "bedrooms": prop.bedrooms,
                "bathrooms": prop.bathrooms,
                "sqft": prop.sqft,
                "parking": prop.parking,
                "status": "Available" if prop.status == "Available" else "Pending",
                "description": prop.description,
            },
        )

        if not prop.images.exists():
            samples = (
                ("demo-apartment-exterior.jpg", "Illustrative stock image — replace with the actual Githurai property photo."),
                ("demo-apartment-detail.jpg", "Illustrative stock image — this is not a photo of the listed property."),
            )
            for filename, caption in samples:
                path = Path(settings.BASE_DIR) / "static" / "images" / filename
                if not path.is_file():
                    raise CommandError(f"Sample photo is missing: {path}")
                with path.open("rb") as photo:
                    PropertyImage.objects.create(
                        property=prop,
                        image=File(photo, name=filename),
                        caption=caption,
                        is_demo=True,
                    )

        action = "Created" if created else "Found"
        self.stdout.write(self.style.SUCCESS(f"{action} pending demo property #{prop.pk} for @{owner.username} with {prop.images.count()} photos."))
