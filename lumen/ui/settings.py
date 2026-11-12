"""Native preferences for Lumen, with immediate, persistent changes."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import replace

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from lumen.engine.apply import ProfileApplier  # noqa: E402
from lumen.model.profile import load_profile, profile_directory  # noqa: E402
from lumen.model.settings import Settings, load_settings, save_settings, settings_path  # noqa: E402


class SettingsWindow(Adw.PreferencesWindow):
    """Searchable settings that remain usable without display hardware."""

    def __init__(
        self,
        application: Adw.Application | None = None,
        parent: Gtk.Window | None = None,
        applier: ProfileApplier | None = None,
        on_changed: Callable[[Settings], None] | None = None,
    ) -> None:
        super().__init__(title="Settings", application=application)
        self.set_default_size(660, 680)
        if parent is not None:
            self.set_transient_for(parent)
            self.set_modal(True)
        self.set_search_enabled(True)
        self._settings = load_settings()
        self._on_changed = on_changed
        self._applier = applier
        self._updating = False
        self._rows = {}
        self._pages = []
        self._build()

    def _page(self, title: str, icon: str) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage(title=title, icon_name=icon)
        self.add(page)
        self._pages.append(page)
        return page

    def _group(
        self, page: Adw.PreferencesPage, title: str, description: str | None = None,
    ) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title=title, hexpand=True)
        if description:
            group.set_description(description)
        page.add(group)
        return group

    def _choice(
        self, group: Adw.PreferencesGroup, key: str, title: str,
        labels: list[str], values: list[str | None], subtitle: str | None = None,
    ) -> None:
        row = Adw.ComboRow(title=title, model=Gtk.StringList.new(labels))
        row.set_use_markup(False)
        if subtitle:
            row.set_subtitle(subtitle)
        row.set_selected(values.index(getattr(self._settings, key)))
        row.connect("notify::selected", lambda widget, _prop: self._change(
            key, values[widget.get_selected()]
        ))
        group.add(row)
        self._rows[key] = row

    def _switch(
        self, group: Adw.PreferencesGroup, key: str, title: str, subtitle: str,
    ) -> None:
        row = Adw.SwitchRow(title=title, subtitle=subtitle)
        row.set_active(getattr(self._settings, key))
        row.connect("notify::active", lambda widget, _prop: self._change(
            key, widget.get_active()
        ))
        group.add(row)
        self._rows[key] = row

    def _info(self, group: Adw.PreferencesGroup, title: str, value: str | int) -> None:
        row = Adw.ActionRow(title=title, subtitle=str(value))
        row.set_use_markup(False)
        row.set_subtitle_selectable(True)
        group.add(row)

    def _build(self) -> None:
        general = self._page("General", "preferences-system-symbolic")
        appearance = self._group(general, "Appearance")
        self._choice(appearance, "appearance", "Colour scheme",
                     ["Follow system", "Light", "Dark"], ["system", "light", "dark"])
        startup = self._group(general, "Opening Lumen")
        self._choice(startup, "opening_screen", "Opening screen",
                     ["Guided calibration", "Live controls"], ["wizard", "panel"],
                     "Used the next time Lumen starts.")
        colour = self._group(general, "Colour conflicts")
        self._switch(colour, "manage_night_light", "Manage Night Light schedule",
                     "Temporarily disable GNOME’s automatic schedule while Lumen runs; "
                     "restore it on exit. Takes effect next launch.")
        self._info(colour, "Other colour tools",
                   "Pause other gamma or colour-temperature tools before calibrating. "
                   "Lumen only manages GNOME’s automatic Night Light schedule.")

        profiles = self._page("Profiles", "preferences-desktop-display-symbolic")
        default = self._group(profiles, "Default calibration",
                              "Used by Apply profile, lumenctl apply-default and the background service. "
                              "Changing this selection does not apply it to the display.")
        labels = ["Automatic (alphabetical)"]
        values = [None]
        for path in sorted(profile_directory().glob("*.json")):
            try:
                profile = load_profile(path)
            except (OSError, ValueError, TypeError, AttributeError):
                continue
            labels.append(f"{profile.name} · {path.name}")
            values.append(path.name)
        selected = self._settings.default_profile
        if selected not in values:
            labels.append(f"Unavailable · {selected}")
            values.append(selected)
        self._choice(default, "default_profile", "Default profile", labels, values)
        service = self._group(profiles, "Background service",
                              "These options control an installed lumen-service. Enable it separately "
                              "with: systemctl --user enable --now lumen-service. "
                              "Wake detection requires X11 DPMS.")
        self._switch(service, "apply_at_login", "Apply when the service starts",
                     "Restore the default calibration at login or when restarting the service.")
        self._switch(service, "reapply_on_wake", "Reapply after display wake",
                     "Restore the default calibration when the display powers back on.")
        storage = self._group(profiles, "Storage")
        self._info(storage, "Saved profiles", profile_directory())
        self._info(storage, "Preferences file", settings_path())

        diagnostics = self._page("Diagnostics", "dialog-information-symbolic")
        display = self._group(diagnostics, "Current display")
        if self._applier is None:
            self._info(display, "Display connection", "No display is connected to this window.")
        else:
            monitor = self._applier.monitor
            self._info(display, "Monitor", monitor.model or monitor.identifier)
            self._info(display, "Connection", monitor.connection or "Unknown")
            self._info(display, "Hardware backend",
                       monitor.backend_name or type(self._applier.backend).__name__)
            self._info(display, "Advertised VCP controls", len(self._applier.capabilities.vcp_codes))
            self._info(display, "Software gamma",
                       self._applier.gamma_output if self._applier.gamma_backend is not None
                       and self._applier.gamma_output else "Unavailable; hardware controls only")
        platform = self._group(diagnostics, "Session")
        self._info(platform, "Session type", os.environ.get("XDG_SESSION_TYPE", "Unknown"))
        self._info(platform, "More diagnostics", "Run lumenctl doctor for backend discovery details.")
        safety = self._group(diagnostics, "Calibration safety")
        self._info(safety, "Display protection",
                   "Dangerous VCP controls are blocked. Hardware writes are verified. "
                   "Cancelling calibration restores captured settings.")
        reset = self._group(diagnostics, "Reset preferences",
                            "Restore application defaults. Saved profiles and monitor values are kept.")
        self._reset_button = Gtk.Button(label="Reset preferences…", halign=Gtk.Align.START)
        self._reset_button.add_css_class("destructive-action")
        self._reset_button.connect("clicked", self._confirm_reset)
        reset.add(self._reset_button)

    def _change(self, key: str, value: str | bool | None) -> None:
        if not self._updating:
            self._persist(replace(self._settings, **{key: value}))

    def _persist(self, settings: Settings) -> None:
        try:
            save_settings(settings)
        except OSError as error:
            self._rebuild()
            dialog = Adw.AlertDialog(heading="Preferences could not be saved", body=str(error))
            dialog.add_response("close", "Close")
            dialog.present(self)
            return
        self._settings = settings
        if self._on_changed:
            self._on_changed(settings)

    def _rebuild(self) -> None:
        self._updating = True
        for page in self._pages:
            self.remove(page)
        self._pages = []
        self._rows = {}
        self._build()
        self._updating = False

    def _confirm_reset(self, _button) -> None:
        dialog = Adw.AlertDialog(heading="Reset preferences?",
                                body="Saved profiles and current monitor values will be kept.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("reset", "Reset preferences")
        dialog.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_reset_response)
        dialog.present(self)

    def _on_reset_response(self, _dialog, response: str) -> None:
        if response == "reset":
            self._persist(Settings())
            self._rebuild()


def settings_button() -> Gtk.Button:
    """A shared header-bar entry point for settings."""
    return Gtk.Button(icon_name="emblem-system-symbolic", tooltip_text="Settings",
                      action_name="app.settings")
