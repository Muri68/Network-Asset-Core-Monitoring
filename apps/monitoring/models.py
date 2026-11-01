"""Monitors, check results, incidents and maintenance windows (PLAN.md §6.5)."""

import datetime

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils import timezone

from apps.core.models import TrackedModel
from apps.inventory.models import Device


class Status(models.TextChoices):
    UP = "up", "Up"
    PENDING = "pending", "Pending"
    DOWN = "down", "Down"
    UNREACHABLE = "unreachable", "Unreachable"
    MAINTENANCE = "maintenance", "Maintenance"
    PAUSED = "paused", "Paused"
    UNKNOWN = "unknown", "Not checked yet"


class Monitor(TrackedModel):
    class Type(models.TextChoices):
        PING = "ping", "Ping (ICMP)"
        TCP = "tcp", "TCP port"
        HTTP = "http", "HTTP(S)"
        KEYWORD = "keyword", "HTTP(S) keyword"
        DNS = "dns", "DNS record"
        SSL = "ssl", "SSL certificate"

    DNS_TYPES = [("A", "A"), ("AAAA", "AAAA"), ("CNAME", "CNAME"), ("MX", "MX"), ("TXT", "TXT"), ("NS", "NS")]

    # --- configuration -----------------------------------------------------
    name = models.CharField(max_length=100)
    type = models.CharField(max_length=10, choices=Type.choices, default=Type.PING)
    target = models.CharField(max_length=255, help_text="Host / IP, or the full URL for HTTP checks.")
    port = models.PositiveIntegerField(null=True, blank=True, validators=[MaxValueValidator(65535)])
    interval_s = models.PositiveIntegerField("check every (seconds)", default=60,
                                             validators=[MinValueValidator(20), MaxValueValidator(3600)])
    timeout_s = models.PositiveIntegerField("timeout (seconds)", default=10,
                                            validators=[MinValueValidator(1), MaxValueValidator(60)])
    retries = models.PositiveSmallIntegerField("down after (failed checks)", default=3,
                                               validators=[MinValueValidator(1), MaxValueValidator(10)])
    http_method = models.CharField("method", max_length=4, choices=[("GET", "GET"), ("HEAD", "HEAD")], default="GET")
    expected_status = models.CharField("accepted status codes", max_length=60, default="200-299",
                                       help_text="e.g. 200-299 or 200,301,302")
    keyword = models.CharField(max_length=200, blank=True, help_text="Text that must appear in the page.")
    follow_redirects = models.BooleanField(default=True)
    ignore_tls = models.BooleanField("ignore certificate errors", default=False)
    dns_record_type = models.CharField("record type", max_length=5, choices=DNS_TYPES, default="A")
    dns_expected = models.CharField("expected value", max_length=255, blank=True,
                                    help_text="Optional: the answer must contain this.")
    ssl_warn_days = models.PositiveSmallIntegerField("warn days before expiry", default=14)
    device = models.ForeignKey(Device, on_delete=models.SET_NULL, null=True, blank=True, related_name="monitors")
    parent = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="children",
                               verbose_name="depends on",
                               help_text="If empty, the monitor of the device's upstream device is used.")
    enabled = models.BooleanField(default=True)

    # --- live state (updated by the monitor process) ----------------------
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.UNKNOWN, db_index=True)
    consecutive_failures = models.PositiveSmallIntegerField(default=0)
    next_check_at = models.DateTimeField(default=timezone.now, db_index=True)
    last_check_at = models.DateTimeField(null=True, blank=True)
    last_change_at = models.DateTimeField(null=True, blank=True)
    last_response_ms = models.PositiveIntegerField(null=True, blank=True)
    last_error = models.CharField(max_length=255, blank=True)
    cert_expires_at = models.DateTimeField(null=True, blank=True)

    STATE_FIELDS = ["status", "consecutive_failures", "next_check_at", "last_check_at", "last_change_at",
                    "last_response_ms", "last_error", "cert_expires_at"]

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("monitoring:monitor_detail", args=[self.pk])

    @property
    def is_http(self):
        return self.type in (self.Type.HTTP, self.Type.KEYWORD)

    @property
    def cert_days(self):
        if not self.cert_expires_at:
            return None
        return (self.cert_expires_at - timezone.now()).days

    def effective_parent(self):
        """The explicit parent, else the first monitor of the device's upstream device."""
        if self.parent_id:
            return self.parent
        if self.device_id and self.device.parent_id:
            return Monitor.objects.filter(device_id=self.device.parent_id, enabled=True).order_by("pk").first()
        return None

    def ancestors(self):
        chain, seen, node = [], {self.pk}, self.effective_parent()
        while node is not None and node.pk not in seen:
            chain.append(node)
            seen.add(node.pk)
            node = node.effective_parent()
        return chain

    def clean(self):
        errors = {}
        if self.is_http and not self.target.startswith(("http://", "https://")):
            errors["target"] = "Enter the full URL, starting with http:// or https://"
        if self.type in (self.Type.TCP,) and not self.port:
            errors["port"] = "Enter the port to check."
        if self.type == self.Type.KEYWORD and not self.keyword:
            errors["keyword"] = "Enter the text that must appear on the page."
        if self.is_http:
            try:
                parse_status_ranges(self.expected_status)
            except ValueError:
                errors["expected_status"] = "Use numbers and ranges, e.g. 200-299 or 200,301,302."
        if self.timeout_s and self.interval_s and self.timeout_s >= self.interval_s:
            errors["timeout_s"] = "The timeout must be shorter than the check interval."
        if self.parent_id and self.pk:
            node, seen = self.parent, set()
            while node is not None and node.pk not in seen:
                if node.pk == self.pk:
                    errors["parent"] = "That would create a loop of dependencies."
                    break
                seen.add(node.pk)
                node = node.parent
        if errors:
            raise ValidationError(errors)


def parse_status_ranges(text):
    """'200-299, 301' → [(200, 299), (301, 301)]."""
    ranges = []
    for part in text.replace(" ", "").split(","):
        if not part:
            continue
        lo, _, hi = part.partition("-")
        lo, hi = int(lo), int(hi or lo)
        if not (100 <= lo <= hi <= 599):
            raise ValueError(part)
        ranges.append((lo, hi))
    if not ranges:
        raise ValueError(text)
    return ranges


class CheckResult(models.Model):
    """One check. Kept for 30 days (PLAN.md A8); summarised into DailyUptime."""

    monitor = models.ForeignKey(Monitor, on_delete=models.CASCADE, related_name="results")
    checked_at = models.DateTimeField(default=timezone.now)
    ok = models.BooleanField()
    response_ms = models.PositiveIntegerField(null=True, blank=True)
    status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    error_class = models.CharField(max_length=30, blank=True)
    message = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-checked_at"]
        indexes = [models.Index(fields=["monitor", "-checked_at"])]

    def __str__(self):
        return f"{self.monitor} {'ok' if self.ok else 'fail'} @ {self.checked_at:%H:%M:%S}"


class DailyUptime(models.Model):
    """One row per monitor per day, kept forever."""

    monitor = models.ForeignKey(Monitor, on_delete=models.CASCADE, related_name="daily")
    date = models.DateField()
    checks = models.PositiveIntegerField()
    failures = models.PositiveIntegerField()
    uptime_pct = models.DecimalField(max_digits=6, decimal_places=3)
    avg_ms = models.PositiveIntegerField(null=True)
    p95_ms = models.PositiveIntegerField(null=True)

    class Meta:
        ordering = ["-date"]
        constraints = [models.UniqueConstraint(fields=["monitor", "date"], name="daily_uptime_unique")]

    def __str__(self):
        return f"{self.monitor} {self.date} {self.uptime_pct}%"


class Incident(models.Model):
    class Kind(models.TextChoices):
        DOWN = "down", "Down"
        UNREACHABLE = "unreachable", "Unreachable (parent down)"

    monitor = models.ForeignKey(Monitor, on_delete=models.CASCADE, related_name="incidents")
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.DOWN)
    started_at = models.DateTimeField(default=timezone.now)
    ended_at = models.DateTimeField(null=True, blank=True)
    cause = models.CharField(max_length=255)
    error_class = models.CharField(max_length=30, blank=True)
    suppressed_by = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name="suppressed", help_text="The parent's incident this one is part of.")
    acknowledged_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                        related_name="+")
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [models.Index(fields=["monitor", "ended_at"])]

    def __str__(self):
        return f"#{self.pk} {self.monitor}"

    @property
    def is_open(self):
        return self.ended_at is None

    @property
    def duration(self):
        return (self.ended_at or timezone.now()) - self.started_at


def human_duration(delta):
    seconds = int(delta.total_seconds())
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


class MaintenanceWindow(TrackedModel):
    class Recurrence(models.TextChoices):
        NONE = "none", "One-time"
        DAILY = "daily", "Every day"
        WEEKLY = "weekly", "Every week"

    name = models.CharField(max_length=100)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    recurrence = models.CharField(max_length=6, choices=Recurrence.choices, default=Recurrence.NONE)
    devices = models.ManyToManyField(Device, blank=True, related_name="maintenance_windows")
    monitors = models.ManyToManyField(Monitor, blank=True, related_name="maintenance_windows")
    include_children = models.BooleanField("also mute devices behind them", default=True)
    reason = models.CharField(max_length=255, blank=True)
    ended_early_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["-starts_at"]

    def __str__(self):
        return self.name

    def clean(self):
        if self.starts_at and self.ends_at:
            if self.ends_at <= self.starts_at:
                raise ValidationError({"ends_at": "The end must be after the start."})
            length = self.ends_at - self.starts_at
            if self.recurrence == self.Recurrence.DAILY and length >= datetime.timedelta(days=1):
                raise ValidationError({"ends_at": "A daily window must be shorter than a day."})
            if self.recurrence == self.Recurrence.WEEKLY and length >= datetime.timedelta(days=7):
                raise ValidationError({"ends_at": "A weekly window must be shorter than a week."})

    def occurrence_at(self, when):
        """(start, end) of the occurrence covering `when`, or None. "End now" skips only that occurrence."""
        length = self.ends_at - self.starts_at
        if self.recurrence == self.Recurrence.NONE:
            occurrence = (self.starts_at, self.ends_at) if self.starts_at <= when < self.ends_at else None
        elif when < self.starts_at:
            occurrence = None
        else:
            period = datetime.timedelta(days=1 if self.recurrence == self.Recurrence.DAILY else 7)
            start = self.starts_at + period * ((when - self.starts_at) // period)
            occurrence = (start, start + length) if start <= when < start + length else None
        if occurrence and self.ended_early_at and occurrence[0] <= self.ended_early_at < occurrence[1]:
            return None
        return occurrence

    def is_active(self, when=None):
        return self.occurrence_at(when or timezone.now()) is not None

    def next_start(self, when=None):
        when = when or timezone.now()
        if self.recurrence == self.Recurrence.NONE:
            return self.starts_at if self.starts_at > when else None
        if when < self.starts_at:
            return self.starts_at
        period = datetime.timedelta(days=1 if self.recurrence == self.Recurrence.DAILY else 7)
        return self.starts_at + period * ((when - self.starts_at) // period + 1)

    @property
    def state(self):
        now = timezone.now()
        if self.is_active(now):
            return "active"
        if self.next_start(now):
            return "scheduled"
        return "completed"

    @property
    def current_end(self):
        occurrence = self.occurrence_at(timezone.now())
        return occurrence[1] if occurrence else None

    def covered_monitor_ids(self):
        """Monitors muted by this window: listed monitors, monitors of listed devices and (optionally) behind them."""
        ids = set(self.monitors.values_list("pk", flat=True))
        device_ids = set(self.devices.values_list("pk", flat=True))
        if self.include_children and device_ids:
            frontier = set(device_ids)
            while frontier:
                frontier = set(Device.objects.filter(parent_id__in=frontier).values_list("pk", flat=True)) - device_ids
                device_ids |= frontier
        ids |= set(Monitor.objects.filter(device_id__in=device_ids).values_list("pk", flat=True))
        if self.include_children:
            frontier = set(ids)
            while frontier:
                frontier = set(Monitor.objects.filter(parent_id__in=frontier).values_list("pk", flat=True)) - ids
                ids |= frontier
        return ids


def monitors_in_maintenance(when=None):
    when = when or timezone.now()
    ids = set()
    for window in MaintenanceWindow.objects.filter(starts_at__lte=when).prefetch_related("monitors", "devices"):
        if window.is_active(when):
            ids |= window.covered_monitor_ids()
    return ids


class MonitorHeartbeat(models.Model):
    """Single row the monitor process updates, so the UI can tell whether it's running."""

    last_beat = models.DateTimeField()
    started_at = models.DateTimeField()
    checks_last_minute = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"Monitor process, last beat {self.last_beat:%H:%M:%S}"

    @classmethod
    def current(cls):
        return cls.objects.filter(pk=1).first()

    @property
    def alive(self):
        return (timezone.now() - self.last_beat).total_seconds() < 30
