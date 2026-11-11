"""Reusable widgets.

Kept separate from the windows so that both the wizard and the live
panel can use them without importing each other.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")

from gi.repository import Adw, Gdk, Graphene, Gtk  # noqa: E402

from lumen.engine.controls import Control, ControlKind  # noqa: E402


class PatternArea(Gtk.DrawingArea):
    """A drawing area that shows a test pattern."""

    def __init__(self, pattern) -> None:
        super().__init__()
        self._renderer = None
        self.set_pattern(pattern)
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_content_width(320)
        self.set_content_height(180)

    def set_pattern(self, pattern) -> None:
        from .patterns import PatternRenderer

        self._renderer = PatternRenderer(pattern)
        self.queue_draw()

    def do_snapshot(self, snapshot) -> None:  # noqa: N802 - GTK naming
        if self._renderer is None:
            return
        width = self.get_allocated_width()
        height = self.get_allocated_height()
        if width <= 0 or height <= 0:
            return
        # GTK 4.14 requires the target rectangle to be passed explicitly,
        # and it must be a Graphene rectangle rather than a Gdk one.
        rect = Graphene.Rect()
        rect.init(0, 0, width, height)
        cr = snapshot.append_cairo(rect)
        self._renderer.draw(cr, width, height)


class ValueSlider(Gtk.Box):
    """A labelled slider bound to a control."""

    def __init__(self, control: Control, on_change=None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._control = control
        self._on_change = on_change

        self._label = Gtk.Label(label=control.label)
        self._label.set_xalign(0.0)

        self._value = Gtk.Label(label="")
        self._value.set_xalign(1.0)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        header.append(self._label)
        header.append(Gtk.Box(hexpand=True))
        header.append(self._value)

        self._scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, control.minimum, control.maximum, 1
        )
        self._scale.set_draw_value(False)
        self._scale.set_hexpand(True)
        self._scale.connect("value-changed", self._on_slider_changed)

        self.append(header)
        self.append(self._scale)
        self.refresh()

    def _on_slider_changed(self, scale) -> None:
        value = scale.get_value()
        if self._control.kind is ControlKind.PRESET:
            return
        if self._on_change is not None:
            self._on_change(self._control, value)

    def refresh(self) -> None:
        """Re-read the control and show its current value."""
        try:
            value = self._control.read()
        except Exception:
            self._value.set_label("unavailable")
            self.set_sensitive(False)
            return
        self.set_sensitive(True)
        if self._control.kind is ControlKind.PRESET:
            options = getattr(self._control, "options", ())
            allowed = ", ".join(f"0x{option:02X}" for option in options)
            self._value.set_label(f"0x{int(value):02X}  ({allowed})")
        else:
            self._value.set_label(f"{value:g}")
            self._scale.set_value(value)


class ControlList(Gtk.Box):
    """A group of sliders for a set of controls."""

    def __init__(self, controls, on_change=None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self._sliders: list[ValueSlider] = []
        for control in controls:
            slider = ValueSlider(control, on_change)
            self._sliders.append(slider)
            self.append(slider)

    def refresh(self) -> None:
        for slider in self._sliders:
            slider.refresh()


class GroupHeader(Gtk.Label):
    """A heading above a group of controls."""

    def __init__(self, text: str) -> None:
        super().__init__(label=text)
        self.add_css_class("heading")
        self.set_xalign(0.0)


def about_dialog(application) -> Adw.AboutWindow:
    """The about window."""
    about = Adw.AboutWindow(
        transient_for=application.get_active_window(),
        application_name="Lumen",
        application_icon="lumen",
        version="0.1.0",
        developer_name="Lumen contributors",
        license_type=Gtk.License.MIT,
        comments="Guided display calibration",
    )
    return about
