"""Gamma ownership, and the software that competes for it.

GNOME's colour daemon takes the CRTC gamma to implement Night Light.
When it does, a calibration applied by Lumen is silently replaced by a
warm-shifted one. The user asked for no night light, so Lumen offers to
switch the schedule off and remembers the previous value.

Lumen also watches its own ramp. A cheap read-and-compare detects
another program taking the ramp, and Lumen puts it back rather than
leaving a corrupted calibration in place.

The gsettings calls are injected so the logic can be tested without
touching the user's settings.
"""

from __future__ import annotations

import subprocess

NIGHT_LIGHT_SCHEMA = "org.gnome.settings-daemon.plugins.color"
NIGHT_LIGHT_KEY = "night-light-schedule-automatic"


class GammaOwner:
    """Keeps a display's gamma ramp as Lumen set it."""

    def __init__(self, gamma_backend, output: str, expected_ramp) -> None:
        self._backend = gamma_backend
        self._output = output
        self._expected = expected_ramp

    def claim(self) -> None:
        """Write the expected ramp."""
        self._backend.write_ramp(self._output, *self._expected)

    def has_drifted(self) -> bool:
        """Whether the ramp no longer matches what Lumen set."""
        try:
            observed = self._backend.read_ramp(self._output)
        except Exception:
            return True
        return observed[0] != self._expected[0]

    def reassert(self) -> bool:
        """Put the ramp back if it drifted. Returns whether it did."""
        if not self.has_drifted():
            return False
        self.claim()
        return True


class NightLightController:
    """Reads and writes GNOME's Night Light schedule."""

    def __init__(self, gsettings_runner=None) -> None:
        if gsettings_runner is None:
            gsettings_runner = self._run_gsettings
        self._run = gsettings_runner

    @staticmethod
    def _run_gsettings(*args: str) -> str:
        try:
            result = subprocess.run(
                ["gsettings", *args],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        return result.stdout.strip()

    def is_available(self) -> bool:
        """Whether GNOME's colour settings can be read at all."""
        value = self._run("get", NIGHT_LIGHT_SCHEMA, NIGHT_LIGHT_KEY)
        return value in ("true", "false")

    def is_scheduled(self) -> bool:
        """Whether the automatic Night Light schedule is switched on."""
        return self._run("get", NIGHT_LIGHT_SCHEMA, NIGHT_LIGHT_KEY) == "true"

    def disable_schedule(self) -> tuple[bool, str]:
        """Switch the schedule off, returning whether it changed and the old value."""
        previous = self._run("get", NIGHT_LIGHT_SCHEMA, NIGHT_LIGHT_KEY)
        if previous not in ("true", "false"):
            return False, ""
        if previous == "false":
            return False, previous
        self._run("set", NIGHT_LIGHT_SCHEMA, NIGHT_LIGHT_KEY, "false")
        return True, previous

    def restore_schedule(self, previous: str) -> bool:
        """Put the schedule back the way it was."""
        if previous not in ("true", "false"):
            return False
        self._run("set", NIGHT_LIGHT_SCHEMA, NIGHT_LIGHT_KEY, previous)
        return True
