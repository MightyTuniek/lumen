"""Taking ownership of the gamma ramp.

GNOME's colour daemon holds the CRTC gamma to implement Night Light.
While it has the ramp, Lumen's calibration is silently replaced by a
warm-shifted one - and it does so on a schedule, so the clash arrives
at the worst possible moment, in the evening.

The user asked for no night light at all, so Lumen offers to switch the
automatic schedule off and remembers the previous value so it can be put
back on exit.

None of this is GNOME specific in principle: the same shape works for
macOS Night Shift and the Windows night light. What differs is how the
setting is reached, so that part lives in its own adapter.
"""

from __future__ import annotations

from lumen.engine.conflict import NightLightController


class NightLightOwnership:
    """Owns the decision about whether night light is left on."""

    def __init__(self, controller: NightLightController | None = None) -> None:
        self._controller = controller or NightLightController()
        self._previous: str | None = None

    @property
    def available(self) -> bool:
        """Whether night light settings can be reached on this system."""
        return self._controller.is_available()

    @property
    def scheduled(self) -> bool:
        """Whether the automatic night light schedule is switched on."""
        return self._controller.is_scheduled()

    def take_ownership(self) -> bool:
        """Switch the automatic schedule off, remembering its old value.

        Returns whether anything changed. Doing this twice is harmless.
        """
        changed, previous = self._controller.disable_schedule()
        if changed:
            self._previous = previous
        return changed

    def release(self) -> bool:
        """Put the schedule back the way it was."""
        if self._previous is None:
            return False
        restored = self._controller.restore_schedule(self._previous)
        if restored:
            self._previous = None
        return restored
