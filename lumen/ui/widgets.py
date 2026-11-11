"""Reusable widgets.

Kept separate from the windows so that both the wizard and the live
panel can use them without importing each other.
"""

from __future__ import annotations

import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")

from gi.repository import Adw, Gdk, GLib, Graphene, Gtk  # noqa: E402

from lumen.engine.controls import Control, ControlKind  # noqa: E402


_READ_WORKER = None


def _ensure_worker():
    """The single background worker that reads controls.

    The ddccontrol service serialises its requests, so two readers
    competing for it make every read slower. One worker reading the
    controls in turn is much faster, and it keeps the interface
    responsive throughout.
    """
    global _READ_WORKER
    import queue

    if _READ_WORKER is not None:
        return _READ_WORKER

    _READ_WORKER = queue.Queue()

    def worker() -> None:
        while True:
            item = _READ_WORKER.get()
            if item is None:
                return
            target, callback = item
            try:
                target.read()
            except Exception:
                pass
            if callback is not None:
                GLib.idle_add(callback)

    threading.Thread(target=worker, daemon=True).start()
    return _READ_WORKER


def read_controls(controls, on_done=None) -> None:
    """Read every control in the background, one at a time."""
    worker = _ensure_worker()
    for control in controls:
        worker.put((control, None))
    if on_done is not None:
        GLib.idle_add(on_done)


def _read_queue(control, on_done) -> None:
    """Queue a single control to be read on the shared background worker."""
    worker = _ensure_worker()
    worker.put((control, on_done))


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
        self._pending = False
        self._asked = False
        self._updating = False

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

    def _request_value(self) -> None:
        """Ask for the control's value, without blocking the interface.

        The ddccontrol service serialises its requests, so several
        sliders reading at once would each wait for the others. The
        read is therefore queued on one shared worker, and the slider
        waits its turn.
        """
        if not self._pending:
            return
        self._pending = False
        _read_queue(self._control, self._apply_value)

    def _apply_value(self) -> bool:
        """Show the value that was read.

        The control is marked as read even when the read failed, so a
        control the monitor does not support is not asked for again. It
        would otherwise be re-queued forever, and every attempt would
        cost a trip over the bus.
        """
        self._asked = True
        self.refresh()
        return False

    def _on_slider_changed(self, scale) -> None:
        if self._updating:
            # The slider was being refreshed, not moved by the user.
            return
        value = scale.get_value()
        if self._control.kind is ControlKind.PRESET:
            return
        self._value.set_label(f"{value:g}")
        if self._on_change is not None:
            self._on_change(self._control, value)

    def refresh(self) -> None:
        """Show the control's current value.

        The control caches its value. When the value is not known yet -
        because the background warm-up has not finished - the slider shows
        a placeholder and asks for the value without blocking the
        interface.
        """
        value = self._control.peek()
        if value is None:
            # The value is not known yet. Ask for it once, in the
            # background, rather than blocking the interface: each read
            # costs about fifty milliseconds over the bus.
            self._value.set_label("reading...")
            self.set_sensitive(False)
            if self._asked:
                self._value.set_label("unavailable")
                return
            self._pending = True
            self._request_value()
            return
        self.set_sensitive(True)
        self._pending = False
        if self._control.kind is ControlKind.PRESET:
            options = getattr(self._control, "options", ())
            allowed = ", ".join(f"0x{option:02X}" for option in options)
            self._value.set_label(f"0x{int(value):02X}  ({allowed})")
        else:
            self._value.set_label(f"{value:g}")
            # Setting the slider's value emits "value-changed", which
            # would write straight back to the monitor. The update is
            # blocked while the slider is being refreshed, so showing a
            # value never changes the display.
            self._updating = True
            self._scale.set_value(value)
            self._updating = False


class ControlList(Gtk.Box):
    """A group of sliders for a set of controls."""

    def __init__(self, controls, on_change=None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.sliders: list[ValueSlider] = []
        for control in controls:
            slider = ValueSlider(control, on_change)
            self.sliders.append(slider)
            self.append(slider)

    def refresh(self) -> None:
        for slider in self.sliders:
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
