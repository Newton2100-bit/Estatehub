from django.shortcuts import redirect


class RequirePasswordChangeMiddleware:
    """Keep landlord-created caretaker accounts on password setup until complete."""

    exempt_paths = {"/change-password/", "/logout/"}

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and request.path not in self.exempt_paths:
            if request.path.startswith(("/static/", "/media/")):
                return self.get_response(request)
            profile = getattr(request.user, "profile", None)
            if profile and profile.must_change_password:
                return redirect("force_password_change")
        return self.get_response(request)
