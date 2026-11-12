# Settings

Preferences are application configuration, separate from calibration profiles.
`lumen/model/settings.py` supplies validated defaults and atomic JSON writes.
Unknown fields are ignored and invalid fields fall back individually. An
unreadable file is logged and uses defaults. The GUI reports write errors
and restores the displayed preferences to their last saved values.

The searchable native preferences window has General, Profiles, and
Diagnostics pages. Display diagnostics use the existing applier, so opening
Settings does not probe or write hardware. Appearance updates immediately;
the opening screen and Night Light policy apply on the next application
launch. Night Light ownership acquired during a session is still released
on shutdown even if the preference changes mid-session.

The panel, CLI's explicit apply-default command and background service
resolve the same default profile. An explicit missing selection resolves
to no profile, while automatic selection keeps the existing alphabetical
fallback. The service reads the login preference at startup and reads the
wake preference on each DPMS wake. The systemd unit runs the persistent
`lumen-service` entry point rather than the one-shot CLI command.

## Useful next additions

- Per-monitor defaults and explicit DDC-to-gamma-output mapping, so a
  multiple-monitor workstation reliably applies each calibration to its
  intended panel. The current app still controls the first discovered
  monitor and gamma output.
- Profile import, export and duplication, with monitor-identity checks and
  a preview of what will change before applying imported settings.
- A calibration review reminder based on the profile creation date, plus
  an optional warm-up timer before beginning visual adjustments.
- A service status row with enable/disable controls and recent application
  failures, backed by a systemd adapter rather than a stored toggle that
  merely claims the service is running.
- A dedicated full-screen pattern viewer and keyboard adjustments for
  evaluating shadow separation, neutral greys and colour clipping.

These need additional behaviour and are not represented by inactive
preferences in the current settings window.
