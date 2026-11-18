from django.contrib.auth.models import User
from django.db.models import Q

from .models import Notification


def notify_user(user, text, url=""):
    if user and user.is_active:
        Notification.objects.create(user=user, text=text, url=url)


def notify_admins(text, url="/oversight/"):
    admins = User.objects.filter(is_active=True).filter(
        Q(is_superuser=True) | Q(profile__role="admin")
    ).distinct()
    Notification.objects.bulk_create(
        Notification(user=admin, text=text, url=url) for admin in admins
    )
