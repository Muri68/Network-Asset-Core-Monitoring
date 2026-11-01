import csv
import datetime

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, OuterRef, ProtectedError, Q, Subquery
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from apps.accounts import roles
from apps.accounts.decorators import capability_required
from apps.accounts.models import Profile, User
from apps.audit.log import log_event
from apps.inventory.models import Device

from . import services
from .forms import ChangePasswordForm, CredentialForm, FolderForm, RevealLogFilter
from .models import Credential, CredentialFolder, CredentialHistory, RevealLog

LOG_PAGE_SIZE = 100


def _with_last_reveal(qs):
    last = RevealLog.objects.filter(credential=OuterRef("pk"), action=RevealLog.Action.REVEAL).order_by("-at")
    return qs.annotate(last_at=Subquery(last.values("at")[:1]),
                       last_by=Subquery(last.values("user__full_name")[:1]),
                       last_by_username=Subquery(last.values("user__username")[:1]))


@capability_required("vault")
def vault(request):
    folder = request.GET.get("folder", "")
    q = request.GET.get("q", "").strip()
    qs = Credential.objects.select_related("folder", "device")
    if folder.isdigit():
        qs = qs.filter(folder_id=folder)
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(username__icontains=q) | Q(host__icontains=q)
                       | Q(device__name__icontains=q))
    folders = CredentialFolder.objects.annotate(count=Count("credentials", filter=Q(
        credentials__deleted_at__isnull=True)))
    return render(request, "vault/vault.html", {
        "credentials": _with_last_reveal(qs), "folders": folders, "folder": folder, "q": q,
        "total": Credential.objects.count(), "recent": _visible_log(request.user)[:6], "active": "vault",
    })


def _visible_log(user):
    """Admins see everyone's access; others see their own (PLAN.md §5)."""
    qs = RevealLog.objects.select_related("user", "credential")
    return qs if roles.can(user, "vault_logs_all") else qs.filter(user=user)


@capability_required("vault")
@never_cache
def credential_detail(request, pk):
    c = get_object_or_404(Credential.objects.select_related("folder", "device", "created_by"), pk=pk)
    return render(request, "vault/credential_detail.html", {
        "c": c,
        "log": c.reveal_logs.select_related("user")[:50],
        "history": c.history.select_related("replaced_by"),
        "can_see_history": roles.can(request.user, "vault_logs_all"),
        "has_notes": bool(c.notes_encrypted),
        "active": "vault",
    })


def _form_page(request, form, credential=None):
    return render(request, "vault/credential_form.html", {"form": form, "c": credential, "active": "vault"},
                  status=400 if form.is_bound else 200)


@capability_required("vault")
def credential_create(request):
    if request.method == "POST":
        form = CredentialForm(request.POST)
        if form.is_valid():
            credential = form.save(by=request.user)
            messages.success(request, f"{credential} encrypted and saved.")
            return redirect(credential)
        return _form_page(request, form)
    initial = {}
    device = Device.objects.filter(pk=request.GET.get("device") or 0).first()
    if device:
        initial = {"device": device.pk, "name": f"{device.name} — admin", "host": device.mgmt_ip or ""}
    if (request.GET.get("folder") or "").isdigit():
        initial["folder"] = request.GET["folder"]
    elif CredentialFolder.objects.count() == 1:
        initial["folder"] = CredentialFolder.objects.get().pk
    return _form_page(request, CredentialForm(initial=initial))


@capability_required("vault")
def credential_edit(request, pk):
    credential = get_object_or_404(Credential, pk=pk)
    form = CredentialForm(request.POST or None, instance=credential)
    if request.method == "POST" and form.is_valid():
        form.save(by=request.user)
        messages.success(request, f"{credential} saved.")
        return redirect(credential)
    return _form_page(request, form, credential)


@capability_required("vault")
def credential_password(request, pk):
    credential = get_object_or_404(Credential, pk=pk)
    form = ChangePasswordForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        credential.set_secret(form.cleaned_data["password"], by=request.user)
        credential.save()
        log_event("password_rotate", credential, message="Previous password moved to history")
        messages.success(request, "Password changed. The previous one is kept in the history (Admins only).")
        return redirect(credential)
    return render(request, "vault/password_form.html", {"form": form, "c": credential, "active": "vault"},
                  status=400 if form.is_bound else 200)


@capability_required("delete_hard")
@require_POST
def credential_delete(request, pk):
    """Soft delete: the credential disappears from the vault, its access log stays."""
    credential = get_object_or_404(Credential, pk=pk)
    credential.deleted_at = timezone.now()
    credential.save(update_fields=["deleted_at", "updated_at", "updated_by"])
    log_event("delete", credential, message="Removed from the vault (kept for the access log)")
    messages.success(request, f"{credential} deleted.")
    return redirect("vault:vault")


# --------------------------------------------------------------------------
# Reveal / copy
# --------------------------------------------------------------------------


def _no_store(response):
    response["Cache-Control"] = "no-store, max-age=0"
    response["Pragma"] = "no-cache"
    return response


def _reveal_response(request, credential, outcome, history=None):
    ctx = {"c": credential, "h": history}
    if outcome.status == "ok":
        ctx.update(secret=outcome.secret, notes=outcome.notes)
        template = "vault/partials/reveal_secret.html"
    elif outcome.status == "locked":
        template = "vault/partials/reveal_locked.html"
    elif outcome.status == "no_code":
        template = "vault/partials/reveal_no_code.html"
    else:
        ctx.update(error=True, remaining=outcome.remaining)
        template = "vault/partials/reveal_form.html"
    return _no_store(render(request, template, ctx))


@capability_required("vault")
@require_POST
def credential_reveal(request, pk):
    credential = get_object_or_404(Credential, pk=pk)
    outcome = services.reveal(request.user, credential, request.POST.get("code", ""), request)
    return _reveal_response(request, credential, outcome)


@capability_required("vault_logs_all")
@require_POST
def history_reveal(request, pk):
    history = get_object_or_404(CredentialHistory.objects.select_related("credential"), pk=pk,
                                credential__deleted_at__isnull=True)
    outcome = services.reveal(request.user, history.credential, request.POST.get("code", ""), request,
                              history=history)
    return _reveal_response(request, history.credential, outcome, history)


@capability_required("vault")
@require_POST
def credential_copied(request, pk):
    """Called with navigator.sendBeacon when someone presses Copy on a revealed password."""
    credential = get_object_or_404(Credential, pk=pk)
    if not services.record_copy(request.user, credential, request):
        return HttpResponseForbidden("Reveal the password first.")
    return HttpResponse(status=204)


# --------------------------------------------------------------------------
# Folders
# --------------------------------------------------------------------------


@capability_required("vault")
def folder_form(request, pk=None):
    folder = get_object_or_404(CredentialFolder, pk=pk) if pk else None
    form = FolderForm(request.POST or None, instance=folder)
    if request.method == "POST" and form.is_valid():
        folder = form.save()
        messages.success(request, f"Folder {folder} saved.")
        return redirect(reverse("vault:vault") + f"?folder={folder.pk}")
    return render(request, "vault/folder_form.html", {"form": form, "folder": folder, "active": "vault"},
                  status=400 if form.is_bound else 200)


@capability_required("vault")
@require_POST
def folder_delete(request, pk):
    folder = get_object_or_404(CredentialFolder, pk=pk)
    if Credential.all_objects.filter(folder=folder).exists():
        messages.error(request, f"{folder} still holds credentials (deleted ones are kept for the access log). "
                                "Move them to another folder first.")
        return redirect(reverse("vault:vault") + f"?folder={folder.pk}")
    try:
        folder.delete()
    except ProtectedError:
        messages.error(request, f"{folder} can't be deleted.")
        return redirect("vault:vault")
    messages.success(request, f"Folder {folder} deleted.")
    return redirect("vault:vault")


# --------------------------------------------------------------------------
# Access log
# --------------------------------------------------------------------------


@capability_required("vault")
@never_cache
def reveal_log(request):
    all_users = roles.can(request.user, "vault_logs_all")
    form = RevealLogFilter(request.GET or None, initial={"days": 30},
                           users=User.objects.filter(reveal_logs__isnull=False).distinct() if all_users else None)
    qs = _visible_log(request.user)
    days = 30
    if form.is_valid():
        data = form.cleaned_data
        days = data.get("days") or 30
        if data.get("action"):
            qs = qs.filter(action=data["action"])
        if data.get("user"):
            qs = qs.filter(user=data["user"])
    if (request.GET.get("credential") or "").isdigit():
        qs = qs.filter(credential_id=request.GET["credential"])
    since = timezone.now() - datetime.timedelta(days=days)
    qs = qs.filter(at__gte=since)

    if request.GET.get("format") == "csv" and all_users:
        return _csv(qs, days)

    week = _visible_log(request.user).filter(at__gte=timezone.now() - datetime.timedelta(days=7))
    stats = week.aggregate(reveals=Count("id", filter=Q(action__in=["reveal", "history_reveal"])),
                           copies=Count("id", filter=Q(action="copy")),
                           failures=Count("id", filter=Q(action="reveal_failed")))
    stats["locked"] = Profile.objects.filter(vault_locked_at__isnull=False).count() if all_users else None
    query = request.GET.copy()
    query.pop("page", None)
    return render(request, "vault/reveal_log.html", {
        "page": Paginator(qs, LOG_PAGE_SIZE).get_page(request.GET.get("page")), "form": form, "stats": stats,
        "all_users": all_users, "query": query.urlencode(), "active": "vault",
    })


def _csv(qs, days):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="vault-access-{timezone.localdate():%Y-%m-%d}.csv"'
    writer = csv.writer(response)
    writer.writerow(["time", "user", "action", "credential", "ip", "browser"])
    rows = list(qs)
    for r in rows:
        writer.writerow([timezone.localtime(r.at).isoformat(timespec="seconds"), r.user.username,
                         r.get_action_display(), r.credential.name, r.ip or "", r.user_agent])
    log_event("export", message=f"Vault access log, last {days} days ({len(rows)} rows)")
    return response
