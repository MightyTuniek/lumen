"""The background service that applies a saved calibration.

DDC/CI settings live in the monitor and survive sleep, reboot and even
the X server restarting. The software gamma ramp lives in the X server
and does not, so it has to be put back after every session starts and
after the display wakes.

The service therefore does two things:

- applies the active profile when the session starts
- re-applies it after the display is re-enabled

It watches the display's DPMS state rather than the kernel's, because
that is the moment the X server discards its gamma ramp.
"""

from __future__ import annotations

import time

from lumen.backends.registry import make_ddc_backend, make_gamma_backend
from lumen.engine.apply import ProfileApplier
from lumen.model.profile import load_profile
from lumen.model.settings import load_settings, selected_profile_path


def active_profile_path():
    """The profile to apply, or None when the user has not saved one."""
    return selected_profile_path()


def build_applier() -> ProfileApplier:
    """The applier for the first monitor the backends can reach."""
    from lumen.backends.displays import applier_for_profile, discover_displays
    path = active_profile_path()
    if path is not None:
        return applier_for_profile(load_profile(path))
    choices, _ = discover_displays()
    if not choices:
        raise RuntimeError("No display could be reached")
    return choices[0].applier()


def apply_active_profile(applier: ProfileApplier | None = None) -> list[str]:
    """Apply the saved profile, returning any notes."""
    path = active_profile_path()
    if path is None:
        return ["no saved calibration to apply"]
    profile = load_profile(path)
    if profile.measured_curves is not None:
        from lumen.backends.displays import applier_for_profile
        applier = applier_for_profile(profile)
    else:
        applier = applier or build_applier()
    from lumen.backends.locking import DisplayBusy
    try:
        if profile.measured_curves is not None:
            from lumen.backends.displays import apply_saved_profile
            return apply_saved_profile(profile, applier)
        return applier.apply(profile)
    except DisplayBusy:
        return ["Skipped profile application: this display is being calibrated"]


def watch_display_wake(applier: ProfileApplier, interval: float = 5.0) -> None:
    """Re-apply the profile whenever the display is re-enabled.

    The X server drops the gamma ramp when the display powers down, so
    the ramp has to be written again on the way back up.
    """
    try:
        from Xlib import display as xdisplay
        from Xlib.ext import dpms
    except ImportError:
        return

    try:
        x_display = xdisplay.Display()
    except Exception:
        return

    if not x_display.has_extension("DPMS"):
        return

    try:
        state = dpms.get_state(x_display).state
    except Exception:
        return

    was_on = state != dpms.DPMSStateOff
    while True:
        time.sleep(interval)
        try:
            current = dpms.get_state(x_display).state
        except Exception:
            continue
        now_on = current != dpms.DPMSStateOff
        if was_on and not now_on:
            was_on = False
            continue
        if not was_on and now_on:
            was_on = True
            if load_settings().reapply_on_wake:
                try:
                    apply_active_profile(applier)
                except Exception:
                    pass


def main() -> int:
    """Run the service."""
    try:
        applier = build_applier()
    except Exception:
        return 1
    if load_settings().apply_at_login:
        apply_active_profile(applier)
    try:
        watch_display_wake(applier)
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
