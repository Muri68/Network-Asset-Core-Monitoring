from django import forms


class StyledFormMixin:
    """Give every widget the design-system classes (checkboxes excluded)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs.setdefault("class", "size-4 accent-[var(--accent)]")
            else:
                widget.attrs.setdefault("class", "input")
