from django.contrib import messages
from django.contrib.auth import authenticate, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_not_required
from django.contrib.auth.models import Group
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST
from django_otp import match_token
from django_otp.plugins.otp_totp.models import TOTPDevice

from . import flow, roles, services
from .decorators import capability_required
from .forms import (
    ChangePasswordForm,
    ConfirmPasswordForm,
    LoginForm,
    NewPasswordForm,
    RevealCodeForm,
    TokenForm,
    UserForm,
)
from .models import User

SHOW_ONCE = "show_once"  # session key for secrets displayed a single time


def _safe_next(request, url):
    if url and url_has_allowed_host_and_scheme(url, allowed_hosts={request.get_host()},
                                               require_https=request.is_secure()):
        return url
    return ""


# --------------------------------------------------------------------------
# Sign-in steps
# --------------------------------------------------------------------------


@login_not_required
@never_cache
def login_view(request):
    if request.user.is_authenticated:
        return redirect("ui:dashboard")
    form = LoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = authenticate(request, username=form.cleaned_data["username"], password=form.cleaned_data["password"])
        if getattr(request, "axes_locked_out", False):
            return render(request, "auth/locked.html", status=429)
        if user is None:
            form.add_error(None, "Incorrect username or password.")
        else:
            flow.start(request, user, _safe_next(request, request.GET.get("next", "")))
            return redirect(flow.next_step_url(user))
    return render(request, "auth/login.html", {"form": form})


@login_not_required
@never_cache
def login_password(request):
    """Replace a temporary password before going on to 2FA."""
    user = flow.pending_user(request)
    if user is None:
        return redirect("accounts:login")
    if not user.must_change_password:
        return redirect(flow.next_step_url(user))
    form = NewPasswordForm(user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect(flow.next_step_url(user))
    return render(request, "auth/new_password.html", {"form": form, "pending": user})


@login_not_required
@never_cache
def login_2fa_setup(request):
    user = flow.pending_user(request)
    if user is None:
        return redirect("accounts:login")
    if user.must_change_password or services.confirmed_totp(user):
        return redirect(flow.next_step_url(user))
    device = services.pending_totp(user)
    form = TokenForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if device.verify_token(form.cleaned_data["token"]):
            device.confirmed = True
            device.save(update_fields=["confirmed"])
            request.session[SHOW_ONCE] = {"backup_codes": services.new_backup_codes(user)}
            next_url = flow.complete(request, user, device)
            request.session["after_backup_codes"] = next_url
            return redirect("accounts:backup_codes")
        if flow.record_2fa_failure(request):
            messages.error(request, "Too many wrong codes. Please sign in again.")
            return redirect("accounts:login")
        form.add_error("token", "That code didn't match. Check the time on your phone and try the newest code.")
    return render(request, "auth/setup_2fa.html", {
        "form": form, "pending": user, "qr_svg": services.totp_qr_svg(device), "secret": services.totp_secret(device),
    })


@login_not_required
@never_cache
def login_2fa(request):
    user = flow.pending_user(request)
    if user is None:
        return redirect("accounts:login")
    if user.must_change_password or not services.confirmed_totp(user):
        return redirect(flow.next_step_url(user))
    form = TokenForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        device = match_token(user, form.cleaned_data["token"])
        if device is not None:
            if device.persistent_id.startswith("otp_static"):
                messages.warning(request, f"Backup code used. {services.backup_codes_left(user)} left.")
            return redirect(flow.complete(request, user, device))
        if flow.record_2fa_failure(request):
            messages.error(request, "Too many wrong codes. Please sign in again.")
            return redirect("accounts:login")
        form.add_error("token", "That code isn't valid. Try the newest code from your app, or a backup code.")
    return render(request, "auth/login_2fa.html", {"form": form, "pending": user})


@never_cache
def backup_codes(request):
    data = request.session.pop(SHOW_ONCE, None) or {}
    codes = data.get("backup_codes")
    next_url = request.session.pop("after_backup_codes", None) or reverse("accounts:profile")
    if not codes:
        return redirect(next_url)
    return render(request, "auth/backup_codes.html", {"codes": codes, "next_url": next_url})


@never_cache
def reveal_code_setup(request):
    """First-login step for vault roles: choose a reveal code."""
    if not roles.can(request.user, "vault") or request.user.profile.has_reveal_code:
        return redirect("ui:dashboard")
    form = RevealCodeForm(request.user, request.POST or None, require_password=False)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Reveal code saved. You're all set.")
        return redirect("ui:dashboard")
    return render(request, "auth/reveal_code_setup.html", {"form": form})


@login_not_required
@require_POST
def logout_view(request):
    flow.cancel(request)
    logout(request)
    messages.success(request, "You've been signed out.")
    return redirect("accounts:login")


# --------------------------------------------------------------------------
# Profile
# --------------------------------------------------------------------------


def _profile_context(request, **forms):
    user = request.user
    totp = services.confirmed_totp(user)
    ctx = {
        "password_form": ChangePasswordForm(user),
        "reveal_form": RevealCodeForm(user),
        "backup_form": ConfirmPasswordForm(user),
        "totp": totp,
        "backup_left": services.backup_codes_left(user),
        "active": "profile",
    }
    ctx.update(forms)
    return ctx


@never_cache
def profile(request):
    return render(request, "accounts/profile.html", _profile_context(request))


@require_POST
def profile_password(request):
    form = ChangePasswordForm(request.user, request.POST)
    if form.is_valid():
        form.save()
        update_session_auth_hash(request, request.user)
        messages.success(request, "Password changed.")
        return redirect("accounts:profile")
    return render(request, "accounts/profile.html", _profile_context(request, password_form=form), status=400)


@require_POST
def profile_reveal_code(request):
    form = RevealCodeForm(request.user, request.POST)
    if form.is_valid():
        form.save()
        messages.success(request, "Reveal code changed.")
        return redirect(reverse("accounts:profile") + "#reveal-code")
    return render(request, "accounts/profile.html", _profile_context(request, reveal_form=form), status=400)


@require_POST
def profile_backup_codes(request):
    form = ConfirmPasswordForm(request.user, request.POST)
    if form.is_valid():
        request.session[SHOW_ONCE] = {"backup_codes": services.new_backup_codes(request.user)}
        request.session["after_backup_codes"] = reverse("accounts:profile")
        return redirect("accounts:backup_codes")
    return render(request, "accounts/profile.html", _profile_context(request, backup_form=form), status=400)


# --------------------------------------------------------------------------
# User management (Admin only)
# --------------------------------------------------------------------------


def _users_context(request, **extra):
    users = User.objects.prefetch_related(Prefetch("groups", queryset=Group.objects.only("name"))).select_related(
        "profile"
    )
    with_2fa = set(TOTPDevice.objects.filter(confirmed=True).values_list("user_id", flat=True))
    locked = services.login_locked_usernames()
    for u in users:
        u.has_2fa = u.pk in with_2fa
        u.login_locked = u.username in locked
    matrix = [(label, *[r in roles.CAPABILITIES[cap] for r in roles.ROLES]) for cap, label in roles.CAPABILITY_LABELS]
    ctx = {
        "users": users,
        "matrix": matrix,
        "role_names": roles.ROLES,
        "create_form": UserForm(),
        "temp_password": request.session.pop(SHOW_ONCE, {}).get("temp_password"),
        "active": "users",
    }
    ctx.update(extra)
    return ctx


@capability_required("manage_users")
@never_cache
def users(request):
    return render(request, "accounts/users.html", _users_context(request))


@capability_required("manage_users")
@require_POST
def user_create(request):
    form = UserForm(request.POST)
    if form.is_valid():
        user, temp = services.create_user(
            username=form.cleaned_data["username"], full_name=form.cleaned_data["full_name"],
            email=form.cleaned_data["email"], role=form.cleaned_data["role"],
        )
        request.session[SHOW_ONCE] = {"temp_password": {"user": user.name, "username": user.username,
                                                        "password": temp, "new": True}}
        return redirect("accounts:users")
    return render(request, "accounts/users.html", _users_context(request, create_form=form, open_create=True),
                  status=400)


@capability_required("manage_users")
@never_cache
def user_edit(request, pk):
    target = get_object_or_404(User, pk=pk)
    form = UserForm(request.POST or None, instance=target)
    if target == request.user:
        del form.fields["is_active"]  # you can't lock yourself out
    if request.method == "POST" and form.is_valid():
        form.save()
        target.set_role(form.cleaned_data["role"])
        messages.success(request, f"{target.name} updated.")
        return redirect("accounts:users")
    target.login_locked = target.username in services.login_locked_usernames()
    return render(request, "accounts/user_edit.html", {
        "form": form, "target": target, "has_2fa": services.confirmed_totp(target) is not None,
        "login_locked": target.login_locked, "active": "users",
    })


@capability_required("manage_users")
@require_POST
def user_action(request, pk, action):
    target = get_object_or_404(User, pk=pk)
    if action == "reset-password":
        password = services.reset_password(target)
        request.session[SHOW_ONCE] = {"temp_password": {"user": target.name, "username": target.username,
                                                        "password": password, "new": False}}
    elif action == "reset-2fa":
        services.reset_two_factor(target)
        messages.success(request, f"2FA reset for {target.name}. They'll set it up again at next sign-in.")
    elif action == "unlock-login":
        services.unlock_login(target)
        messages.success(request, f"Sign-in unlocked for {target.name}.")
    elif action == "unlock-vault":
        services.unlock_vault(target)
        messages.success(request, f"Vault unlocked for {target.name}.")
    elif action in ("deactivate", "activate"):
        active = action == "activate"
        if target == request.user and not active:
            messages.error(request, "You can't deactivate your own account.")
            return redirect("accounts:users")
        try:
            services.guard_last_admin(target, target.role, active)
        except services.LastAdminError as e:
            messages.error(request, str(e))
            return redirect("accounts:users")
        target.is_active = active
        target.save(update_fields=["is_active"])
        messages.success(request, f"{target.name} {'activated' if active else 'deactivated'}.")
    else:
        messages.error(request, "Unknown action.")
    return redirect("accounts:users")
