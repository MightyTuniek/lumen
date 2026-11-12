"""The live control panel.

Once a calibration has been made, the panel gives day-to-day control:
sliders for every control the monitor supports, a profile switcher, and
a way to put the last calibration back.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk  # noqa: E402

from lumen.engine.apply import ProfileApplier  # noqa: E402
from lumen.engine.controls import build_vcp_controls  # noqa: E402
from lumen.model.profile import load_profile, profile_directory  # noqa: E402

from .widgets import ControlList, GroupHeader  # noqa: E402


class PanelWindow(Adw.ApplicationWindow):
    """Day-to-day control of one monitor."""

    def __init__(self, application, applier: ProfileApplier) -> None:
        super().__init__(application=application, title="Lumen")
        self.set_default_size(520, 760)
        self._applier = applier

        self._header = Adw.HeaderBar()
        self._header.set_title_widget(Adw.WindowTitle(title="Lumen"))
        self._toast = None

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        body.set_margin_top(12)
        body.set_margin_bottom(12)
        body.set_margin_start(12)
        body.set_margin_end(12)

        self._profile_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._profile_button = Gtk.Button(label="Apply profile")
        self._profile_button.connect("clicked", self._on_apply_profile)
        self._restore_button = Gtk.Button(label="Restore last")
        self._restore_button.add_css_class("destructive-action")
        self._restore_button.connect("clicked", self._on_restore)
        self._profile_row.append(self._profile_button)
        self._profile_row.append(self._restore_button)

        body.append(GroupHeader("Calibrations"))
        body.append(self._profile_row)

        self._groups_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        body.append(self._groups_box)

        scroll = Gtk.ScrolledWindow()
        scroll.set_child(body)
        scroll.set_vexpand(True)
        view = Adw.ToolbarView()
        view.add_top_bar(self._header)
        view.set_content(scroll)
        self._overlay = Adw.ToastOverlay()
        self._overlay.set_child(view)
        self.set_content(self._overlay)

        self._build_controls()

    def _build_controls(self) -> None:
        controls = build_vcp_controls(
            self._applier.backend,
            self._applier.monitor,
            self._applier.capabilities,
        )
        groups: dict[str, list] = {}
        for control in controls:
            groups.setdefault(control.label.split(":")[0], []).append(control)

        for name in sorted(groups):
            self._groups_box.append(GroupHeader(name))
            self._groups_box.append(
                ControlList(groups[name], self._on_control_changed)
            )

    def _on_control_changed(self, control, value) -> None:
        try:
            control.write(value)
        except Exception as error:
            self._notify(str(error))

    def _notify(self, message: str) -> None:
        self._toast = Adw.Toast(title=message)
        self._overlay.add_toast(self._toast)

    def _on_apply_profile(self, _button) -> None:
        directory = profile_directory()
        paths = sorted(directory.glob("*.json")) if directory.exists() else []
        if not paths:
            self._notify("No saved calibrations yet")
            return
        profile = load_profile(paths[0])
        notes = self._applier.apply(profile)
        message = "; ".join(notes) if notes else f"Applied {profile.name}"
        self._notify(message)

    def _on_restore(self, _button) -> None:
        self._notify("No snapshot to restore")
