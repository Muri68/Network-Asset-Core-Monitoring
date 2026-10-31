from django.db import transaction

from apps.audit.log import log_event
from apps.inventory.models import DeviceType

from .models import Port
from .patterns import expand_lines

REAR_SUFFIX = " (rear)"


@transaction.atomic
def create_ports(device, names, *, type=Port.Type.COPPER, speed="", mode="", pair_rear=False):
    """
    Create the named ports on `device`, skipping names that already exist.
    With pair_rear (patch panels) each port becomes a front port with a paired "<name> (rear)" port.
    Returns (created ports, skipped names).
    """
    existing = {n.lower() for n in device.ports.values_list("name", flat=True)}
    created, skipped = [], []
    for name in names:
        if name.lower() in existing:
            skipped.append(name)
            continue
        front = Port(device=device, name=name, speed=speed, mode=mode,
                     type=Port.Type.FRONT if pair_rear else type)
        front.save()
        existing.add(name.lower())
        created.append(front)
        if pair_rear:
            rear_name = f"{name}{REAR_SUFFIX}"
            if rear_name.lower() in existing:
                continue
            rear = Port(device=device, name=rear_name, type=Port.Type.REAR, paired_port=front)
            rear.save()
            front.paired_port = rear
            front.save(update_fields=["paired_port"])
            existing.add(rear_name.lower())
            created.append(rear)
    return created, skipped


def apply_model_template(device):
    """Create the ports listed in the device model's port template (patch panels get paired rear ports)."""
    template = device.device_model.port_template
    if not template.strip():
        return [], []
    return create_ports(device, expand_lines(template), pair_rear=device.device_type == DeviceType.PATCH_PANEL)


def set_port_vlans(port, untagged, tagged):
    """Set a port's VLANs, logging the tagged list (a many-to-many the automatic diff can't see)."""
    port.untagged_vlan = untagged
    port.save()
    before = sorted(v.vid for v in port.tagged_vlans.all())
    port.tagged_vlans.set(tagged)
    after = sorted(v.vid for v in tagged)
    if before != after:
        log_event("update", port, changes={"tagged_vlans": [", ".join(map(str, before)) or None,
                                                            ", ".join(map(str, after)) or None]})


def vlan_summary(port):
    """'10' for access ports, 'U 10 · T 20,30' for trunks — for tables."""
    tagged = sorted(v.vid for v in port.tagged_vlans.all())
    untagged = port.untagged_vlan.vid if port.untagged_vlan_id else None
    if port.mode == Port.Mode.TRUNK:
        parts = []
        if untagged:
            parts.append(f"U {untagged}")
        if tagged:
            parts.append("T " + ",".join(map(str, tagged)))
        return " · ".join(parts) or "—"
    return str(untagged) if untagged else "—"
