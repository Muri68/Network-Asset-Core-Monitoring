"""Locations, racks and devices (PLAN.md §6.2)."""

import re

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models.functions import Lower
from django.urls import reverse
from django.utils import timezone

from apps.core.models import TrackedModel


class DeviceType(models.TextChoices):
    SERVER = "server", "Server"
    SWITCH = "switch", "Switch"
    ROUTER = "router", "Router"
    FIREWALL = "firewall", "Firewall"
    ACCESS_POINT = "access_point", "Access point"
    ACCESS_CONTROL = "access_control", "Access control"
    NVR = "nvr", "NVR"
    CAMERA = "camera", "Camera"
    PATCH_PANEL = "patch_panel", "Patch panel"
    UPS = "ups", "UPS"
    PDU = "pdu", "PDU"
    STORAGE = "storage", "Storage"
    OTHER = "other", "Other"


DEVICE_ICONS = {
    "server": "server", "switch": "network", "router": "router", "firewall": "flame",
    "access_point": "wifi", "access_control": "fingerprint", "nvr": "hard-drive", "camera": "cctv",
    "patch_panel": "ethernet-port", "ups": "battery-charging", "pdu": "plug", "storage": "database",
    "other": "box",
}


class Location(TrackedModel):
    name = models.CharField(max_length=100, unique=True)
    address = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Rack(TrackedModel):
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="racks")
    name = models.CharField(max_length=100)
    height_u = models.PositiveSmallIntegerField(
        "height (U)", default=42, validators=[MinValueValidator(1), MaxValueValidator(60)]
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["location__name", "name"]
        constraints = [models.UniqueConstraint(
            fields=["location", "name"], name="rack_unique_name_per_location",
            violation_error_message="This location already has a rack with that name.",
        )]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("inventory:rack_detail", args=[self.pk])

    def occupants(self, face=None, exclude=None):
        qs = self.devices.filter(position_u__isnull=False)
        if face:
            qs = qs.filter(face=face)
        if exclude is not None and exclude.pk:
            qs = qs.exclude(pk=exclude.pk)
        return qs

    def conflicts(self, position_u, height_u, face, exclude=None):
        """Devices on `face` overlapping units position_u … position_u + height_u - 1."""
        top = position_u + max(height_u, 1) - 1
        return [
            d for d in self.occupants(face=face, exclude=exclude)
            if d.position_u <= top and position_u <= d.top_u
        ]


class Manufacturer(TrackedModel):
    name = models.CharField(max_length=100)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(Lower("name"), name="manufacturer_name_ci_unique",
                                              violation_error_message="That manufacturer already exists.")]

    def __str__(self):
        return self.name


class DeviceModel(TrackedModel):
    manufacturer = models.ForeignKey(Manufacturer, on_delete=models.PROTECT, related_name="models")
    name = models.CharField(max_length=100)
    device_type = models.CharField(max_length=20, choices=DeviceType.choices, default=DeviceType.OTHER)
    height_u = models.PositiveSmallIntegerField("height (U)", default=1, validators=[MaxValueValidator(20)])
    # One pattern per line, e.g. "GigabitEthernet1/0/[1-48]". Used to create ports in Phase 3.
    port_template = models.TextField(blank=True)

    class Meta:
        ordering = ["manufacturer__name", "name"]
        constraints = [
            models.UniqueConstraint("manufacturer", Lower("name"), name="device_model_ci_unique_per_manufacturer")
        ]

    def __str__(self):
        return f"{self.manufacturer} {self.name}"


MAC_RE = re.compile(r"^[0-9a-f]{2}([:-]?)[0-9a-f]{2}(\1[0-9a-f]{2}){4}$", re.I)


def normalise_mac(value):
    """'AA-BB-CC-DD-EE-FF' / 'aabb.ccdd.eeff' / 'aabbccddeeff' → 'aa:bb:cc:dd:ee:ff'."""
    if not value:
        return ""
    digits = re.sub(r"[^0-9a-fA-F]", "", value)
    if len(digits) != 12:
        raise ValidationError("Enter a MAC address like aa:bb:cc:dd:ee:ff.")
    return ":".join(digits[i:i + 2] for i in range(0, 12, 2)).lower()


class Device(TrackedModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        SPARE = "spare", "Spare"
        FAULTY = "faulty", "Faulty"
        RETIRED = "retired", "Retired"

    class Face(models.TextChoices):
        FRONT = "front", "Front"
        REAR = "rear", "Rear"

    name = models.CharField(max_length=100)
    device_type = models.CharField("type", max_length=20, choices=DeviceType.choices, default=DeviceType.OTHER)
    device_model = models.ForeignKey(DeviceModel, on_delete=models.PROTECT, related_name="devices",
                                     verbose_name="model")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    serial = models.CharField("serial number", max_length=100, blank=True)
    asset_tag = models.CharField(max_length=50, unique=True, null=True, blank=True,
                                 error_messages={"unique": "Another device already has this asset tag."})
    firmware = models.CharField("firmware / OS", max_length=100, blank=True)
    # Becomes a link to an IPAM address in Phase 4.
    mgmt_ip = models.GenericIPAddressField("management IP", protocol="IPv4", unique=True, null=True, blank=True,
                                           error_messages={"unique": "Another device already uses this management IP."})
    mac = models.CharField("MAC address", max_length=17, blank=True)

    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="devices")
    rack = models.ForeignKey(Rack, on_delete=models.PROTECT, related_name="devices", null=True, blank=True)
    position_u = models.PositiveSmallIntegerField("starting U", null=True, blank=True,
                                                  validators=[MinValueValidator(1)])
    height_u = models.PositiveSmallIntegerField("height (U)", default=1, validators=[MaxValueValidator(20)])
    face = models.CharField(max_length=5, choices=Face.choices, default=Face.FRONT)

    parent = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="children",
                               verbose_name="depends on")

    purchase_date = models.DateField(null=True, blank=True)
    warranty_expiry = models.DateField("warranty until", null=True, blank=True)
    owner = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(Lower("name"), name="device_name_ci_unique",
                                              violation_error_message="A device with this name already exists.")]
        indexes = [models.Index(fields=["device_type"]), models.Index(fields=["status"])]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("inventory:device_detail", args=[self.pk])

    # --- display helpers -----------------------------------------------------

    @property
    def icon(self):
        return DEVICE_ICONS.get(self.device_type, "box")

    @property
    def type_label(self):
        return self.get_device_type_display()

    @property
    def top_u(self):
        return self.position_u + max(self.height_u, 1) - 1 if self.position_u else None

    @property
    def manufacturer(self):
        return self.device_model.manufacturer

    @property
    def warranty_days(self):
        return (self.warranty_expiry - timezone.localdate()).days if self.warranty_expiry else None

    # --- validation ----------------------------------------------------------

    def clean(self):
        errors = {}
        if self.mac:
            try:
                self.mac = normalise_mac(self.mac)
            except ValidationError as e:
                errors["mac"] = e.messages
        if self.asset_tag == "":
            self.asset_tag = None
        if self.mgmt_ip:
            from apps.ipam.models import IPAddress

            record = IPAddress.objects.filter(address=self.mgmt_ip).select_related("device").first()
            # Fine if IPAM already lists it for this very device; otherwise it's taken.
            if record is not None and (self.pk is None or record.device_id != self.pk):
                if record.device_id:
                    owner = f"assigned to {record.device}"
                else:
                    owner = f"recorded in IPAM ({record.service or record.get_status_display()})"
                errors["mgmt_ip"] = f"{self.mgmt_ip} is already {owner}."

        if self.rack_id:
            if self.location_id and self.rack.location_id != self.location_id:
                errors["rack"] = f"{self.rack} is in {self.rack.location}, not {self.location}."
            if not self.position_u:
                errors["position_u"] = "Choose the starting U for a racked device."
            elif self.height_u < 1:
                errors["height_u"] = "A racked device needs a height of at least 1U."
            elif self.top_u > self.rack.height_u:
                errors["position_u"] = (f"U{self.position_u}–U{self.top_u} doesn't fit in {self.rack} "
                                        f"({self.rack.height_u}U).")
            else:
                clash = self.rack.conflicts(self.position_u, self.height_u, self.face, exclude=self)
                if clash:
                    names = ", ".join(f"{d.name} (U{d.position_u}–U{d.top_u})" for d in clash)
                    errors["position_u"] = f"Overlaps {names} on the {self.get_face_display().lower()}."
        elif self.position_u:
            errors["rack"] = "Choose a rack, or leave the starting U empty."

        if self.parent_id:
            if self.pk and self.parent_id == self.pk:
                errors["parent"] = "A device can't depend on itself."
            else:
                seen, node = set(), self.parent
                while node is not None and node.pk not in seen:
                    if self.pk and node.pk == self.pk:
                        errors["parent"] = "That would create a loop of dependencies."
                        break
                    seen.add(node.pk)
                    node = node.parent
        if errors:
            raise ValidationError(errors)

    def retire(self):
        """Keep the record and its history, free its rack space."""
        self.status = self.Status.RETIRED
        self.rack = None
        self.position_u = None
        self.save()


class DeviceAttribute(models.Model):
    """Free-form extra details, e.g. 'NVR channel = 7' (PLAN.md §6.2)."""

    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="attributes")
    key = models.CharField(max_length=60)
    value = models.CharField(max_length=255)

    class Meta:
        ordering = ["key"]
        constraints = [models.UniqueConstraint(fields=["device", "key"], name="device_attribute_unique_key")]

    def __str__(self):
        return f"{self.device} · {self.key}"
