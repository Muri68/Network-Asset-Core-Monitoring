import re
from functools import lru_cache

from django import template
from django.conf import settings
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()

ICON_DIR = settings.BASE_DIR / "static" / "icons"


@lru_cache(maxsize=256)
def _load_icon(name):
    svg = (ICON_DIR / f"{name}.svg").read_text()
    svg = re.sub(r"<!--.*?-->", "", svg, flags=re.S).strip()
    # Strip sizing/class only from the root <svg> tag; inner <rect width=…> must stay.
    head, body = svg.split(">", 1)
    head = re.sub(r'\s(class|width|height)="[^"]*"', "", head)
    return f"{head}>{body}"


@register.simple_tag
def icon(name, cls="size-4"):
    """Inline a Lucide icon: {% icon "server" "size-5 text-muted" %}."""
    svg = _load_icon(name)
    return mark_safe(svg.replace("<svg", f'<svg class="{escape(cls)}" aria-hidden="true"', 1))


@register.filter
def get(mapping, key):
    try:
        return mapping.get(key)
    except AttributeError:
        return None


@register.filter
def pct_tone(value):
    """Colour tone for a utilisation percentage."""
    value = float(value or 0)
    if value >= 90:
        return "down"
    if value >= 70:
        return "warn"
    return "up"


@register.filter
def uptime_tone(value):
    value = float(value or 0)
    if value >= 99.5:
        return "up"
    if value >= 98:
        return "warn"
    return "down"


@register.filter
def days_tone(days):
    if days is None:
        return "muted"
    if days <= 14:
        return "down"
    if days <= 45:
        return "warn"
    return "up"


@register.inclusion_tag("components/ring.html")
def ring(value, total, label, tone="up", size=84):
    """Circular progress ring, as in the 'severity' rings of the reference design."""
    radius = 34
    circumference = 2 * 3.14159 * radius
    fraction = (value / total) if total else 0
    return {
        "value": value, "label": label, "tone": tone, "size": size, "r": radius,
        "circ": round(circumference, 2), "dash": round(circumference * fraction, 2),
    }


@register.inclusion_tag("components/gauge.html")
def gauge(percent, caption=""):
    """Half-circle health gauge with a needle (0–100)."""
    percent = max(0.0, min(100.0, float(percent)))
    arc_len = 3.14159 * 80
    return {
        "percent": percent, "caption": caption, "arc": round(arc_len, 2),
        "dash": round(arc_len * percent / 100, 2), "angle": round(-90 + 180 * percent / 100, 2),
    }


@register.filter
def duration(incident):
    """Precise incident length: '19s', '12m 40s', '3h 05m'."""
    from apps.monitoring.models import human_duration

    return human_duration(incident.duration)


@register.filter
def initials(name):
    """'Grace Adeyemi' → 'GA', 'Unknown (grace)' → 'UG', 'System' → 'S'."""
    words = re.findall(r"[^\W_]+", str(name or ""))
    return "".join(w[0] for w in words[:2]).upper() or "?"
