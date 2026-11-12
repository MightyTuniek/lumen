"""The Lumen application.

Starts up, finds the monitor and its backends, and opens either the
wizard or the live panel.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib  # noqa: E402

from lumen.backends.base import DdcError  # noqa: E402
from lumen.backends.registry import (  # noqa: E402
    make_ddc_backend,
    make_gamma_backend,
)
from lumen.engine.apply import ProfileApplier  # noqa: E402
from lumen.engine.calibration import CalibrationSession  # noqa: E402
from lumen.model.settings import Settings, load_settings  # noqa: E402

from .nightlight import NightLightOwnership  # noqa: E402
from .panel import PanelWindow  # noqa: E402
from .settings import SettingsWindow, main_menu_button, settings_button  # noqa: E402
from .wizard import WizardWindow  # noqa: E402


class LumenApplication(Adw.Application):
    """The main application."""

    def __init__(self) -> None:
        super().__init__(
            application_id="io.github.lumen_cal.Lumen",
            flags=Gio.ApplicationFlags.FLAGS_NONE,
        )
        self._applier: ProfileApplier | None = None
        self._last_profile = None
        self._settings_window = None
        self._colorimeter_window = None
        self._switching_colorimeter = False
        self._settings = load_settings()
        action = Gio.SimpleAction.new("settings", None)
        action.connect("activate", self._show_settings)
        self.add_action(action)
        self.set_accels_for_action("app.settings", ["<primary>comma"])
        colorimeter = Gio.SimpleAction.new("colorimeter", None)
        colorimeter.connect("activate", self._show_colorimeter)
        self.add_action(colorimeter)

    def _apply_preferences(self, settings: Settings) -> None:
        self._settings = settings
        scheme = {
            "system": Adw.ColorScheme.DEFAULT,
            "light": Adw.ColorScheme.FORCE_LIGHT,
            "dark": Adw.ColorScheme.FORCE_DARK,
        }[settings.appearance]
        self.get_style_manager().set_color_scheme(scheme)

    def _show_settings(self, _action=None, _parameter=None) -> None:
        if self._settings_window is None:
            self._settings_window = SettingsWindow(
                self, parent=self.get_active_window(), applier=self._applier,
                on_changed=self._apply_preferences,
            )
            self._settings_window.connect("close-request", self._settings_closed)
        self._settings_window.present()

    def _show_colorimeter(self, _action=None, _parameter=None):
        if self._switching_colorimeter:
            return
        if self._colorimeter_window is not None:
            self._colorimeter_window.present()
            return
        parent = self.get_active_window()
        if isinstance(parent, WizardWindow):
            if parent._dirty:
                dialog = Adw.AlertDialog(heading="Switch to colorimeter calibration?",
                    body="Discard this visual calibration and restore the captured settings before continuing.")
                dialog.add_response("cancel", "Cancel")
                dialog.add_response("switch", "Discard and switch")
                dialog.set_default_response("cancel")
                dialog.set_close_response("cancel")
                dialog.connect("response", lambda _, response: self._leave_visual(parent) if response == "switch" else None)
                dialog.present(parent)
                return
            self._leave_visual(parent)
            return
        self._open_colorimeter(parent)

    def _leave_visual(self, parent):
        import threading
        parent.set_sensitive(False)
        self._switching_colorimeter = True
        def worker():
            try:
                errors = parent._applier.restore(parent._snapshot) if parent._dirty and parent._snapshot else []
            except Exception as error:
                errors = [str(error)]
            def done():
                self._switching_colorimeter = False
                if errors:
                    parent.set_sensitive(True)
                    parent._report(RuntimeError("; ".join(errors)))
                    return False
                parent._dirty = False
                parent._closed = True
                parent.destroy()
                panel = PanelWindow(self, self._applier)
                panel.present()
                self._open_colorimeter(panel)
                return False
            GLib.idle_add(done)
        threading.Thread(target=worker, daemon=True).start()

    def _open_colorimeter(self, parent):
        from .colorimeter import ColorimeterWindow
        if parent:
            parent.set_sensitive(False)
        window = ColorimeterWindow(self, parent=parent, on_finished=self._on_calibrated)
        self._colorimeter_window = window
        def closed(_window):
            if window._closed:
                self._colorimeter_window = None
                if parent:
                    parent.set_sensitive(True)
            return False
        window.connect("close-request", closed)
        window.present()

    def _settings_closed(self, _window) -> bool:
        self._settings_window = None
        return False

    def do_activate(self) -> None:
        self._apply_preferences(load_settings())
        window = self.get_active_window()
        if window is not None:
            window.present()
            return
        try:
            applier = self._build_applier()
        except DdcError as error:
            self._show_error("No display could be reached", str(error))
            return
        except Exception as error:  # pragma: no cover - a last resort
            self._show_error("Lumen could not start", str(error))
            return

        self._applier = applier

        # Night Light holds the same gamma ramp Lumen needs. Take
        # ownership of it for the lifetime of the application so the
        # calibration is not silently replaced.
        self._night_light = NightLightOwnership()
        if (self._settings.manage_night_light and self._night_light.available
                and self._night_light.scheduled):
            self._night_light.take_ownership()

        if self._settings.opening_screen == "panel":
            PanelWindow(self, applier).present()
            return

        session = CalibrationSession(
            applier.backend,
            applier.monitor,
            applier.capabilities,
            gamma_backend=applier.gamma_backend,
            gamma_output=applier.gamma_output,
        )
        if not session.steps:
            self._show_error(
                "No calibration controls are available",
                "This display offers no supported calibration controls or software gamma.",
            )
            return
        window = WizardWindow(self, session, applier, on_finished=self._on_calibrated)
        window.present()

    def _on_calibrated(self, profile) -> None:
        """Called when the wizard finishes and saves a calibration."""
        self._last_profile = profile

    def _build_applier(self) -> ProfileApplier:
        from lumen.backends.displays import discover_displays
        choices, _ = discover_displays()
        if not choices:
            raise DdcError("No supported monitor controls or X11 gamma outputs were found")
        return choices[0].applier()

    def do_shutdown(self) -> None:
        """Put the night light schedule back the way it was."""
        if hasattr(self, "_night_light"):
            self._night_light.release()
        Adw.Application.do_shutdown(self)

    def _show_error(self, heading: str, body: str) -> None:
        window = Adw.ApplicationWindow(application=self, title="Lumen")
        window.set_default_size(480, 240)
        header = Adw.HeaderBar()
        header.pack_end(settings_button())
        header.pack_end(main_menu_button())
        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(Adw.StatusPage(title=heading, description=body,
                                        icon_name="video-display-symbolic"))
        window.set_content(view)
        window.present()
        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.add_response("settings", "Settings")
        dialog.add_response("close", "Close")
        dialog.set_close_response("close")
        dialog.connect("response", lambda _dialog, response:
                       self._show_settings() if response == "settings" else window.destroy())
        dialog.present(window)


def main() -> int:
    Adw.init()
    application = LumenApplication()
    return application.run([])
