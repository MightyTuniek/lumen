"""The Lumen application.

Starts up, finds the monitor and its backends, and opens either the
wizard or the live panel.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio  # noqa: E402

from lumen.backends.base import DdcError  # noqa: E402
from lumen.backends.registry import (  # noqa: E402
    make_ddc_backend,
    make_gamma_backend,
)
from lumen.engine.apply import ProfileApplier  # noqa: E402
from lumen.engine.calibration import CalibrationSession  # noqa: E402

from .nightlight import NightLightOwnership  # noqa: E402
from .panel import PanelWindow  # noqa: E402
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

    def do_activate(self) -> None:
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
        if self._night_light.available and self._night_light.scheduled:
            self._night_light.take_ownership()

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
        backend = make_ddc_backend()
        monitors = backend.list_monitors()
        if not monitors:
            raise DdcError("no monitors were found")
        monitor = monitors[0]
        if hasattr(backend, "describe"):
            monitor = backend.describe(monitor)
        capabilities = backend.capabilities(monitor)
        gamma = make_gamma_backend()
        output = None
        if gamma is not None:
            outputs = gamma.list_outputs()
            output = outputs[0] if outputs else None
        return ProfileApplier(backend, monitor, capabilities, gamma, output)

    def do_shutdown(self) -> None:
        """Put the night light schedule back the way it was."""
        if hasattr(self, "_night_light"):
            self._night_light.release()
        Adw.Application.do_shutdown(self)

    def _show_error(self, heading: str, body: str) -> None:
        window = Adw.ApplicationWindow(application=self, title="Lumen")
        window.set_default_size(480, 240)
        window.present()
        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.add_response("close", "Close")
        dialog.set_close_response("close")
        dialog.connect("response", lambda _dialog, _response: window.destroy())
        dialog.present(window)


def main() -> int:
    Adw.init()
    application = LumenApplication()
    return application.run([])
