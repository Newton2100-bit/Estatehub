from django.core.management.base import BaseCommand
from core.models import Floor, Property, RentalUnit


SAMPLE_PROPERTIES = [
    ("The Vista Residences", "1250 Horizon Blvd, Downtown", "Apartment", "2400.00", 2, "2.0", 1100, 2),
    ("Modern Loft in Downtown", "1200 Market St, City Center", "Loft", "2800.00", 2, "2.0", 1200, 1),
    ("Skyview Executive Condo", "888 Horizon Ave, Financial District", "Condo", "3200.00", 1, "1.5", 950, 1),
    ("Charming Historic Flat", "210 Elm St, Old Town", "Apartment", "2100.00", 2, "1.0", 1050, 1),
    ("Grand Penthouse Suite", "1 Summit Way, Uptown", "Penthouse", "12000.00", 4, "4.0", 4200, 2),
]


class Command(BaseCommand):
    help = "Create sample available properties for the EstateHub marketplace"

    def handle(self, *args, **kwargs):
        for title, location, property_type, rent, bedrooms, bathrooms, sqft, parking in SAMPLE_PROPERTIES:
            prop, _ = Property.objects.get_or_create(
                title=title,
                defaults={
                    "location": location,
                    "city": "Seattle",
                    "property_type": property_type,
                    "rent": rent,
                    "bedrooms": bedrooms,
                    "bathrooms": bathrooms,
                    "sqft": sqft,
                    "parking": parking,
                    "status": "Available",
                    "description": "Thoughtfully designed residence with premium finishes and convenient amenities.",
                    "amenities": "AC, Pool, Gym, Smart Home, Concierge, Rooftop",
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
        self.stdout.write(self.style.SUCCESS("Sample EstateHub properties ready."))
