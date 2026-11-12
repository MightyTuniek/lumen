# Lumen

Guided display calibration for Linux.

Lumen walks you through calibrating a monitor. It uses the display's own
hardware controls where they exist, and software gamma shaping for the
fine control the hardware cannot reach, and it shows you a test pattern
at every step so you can judge the result yourself.

## What it can control

On a typical desktop monitor Lumen exposes:

| Control | How |
|---|---|
| Brightness, contrast | DDC/CI `0x10`, `0x12` |
| White point per channel | DDC/CI video gain `0x16`, `0x18`, `0x1A` |
| Black point per channel | DDC/CI video black level `0x6C`, `0x6E`, `0x70` |
| Colour temperature | DDC/CI colour preset `0x14`, temperature `0x0C` |
| Saturation and hue | DDC/CI `0x59`-`0x5E`, `0x9B`-`0xA0` |
| Gamma curve | X11 RandR gamma ramp, 1024 entries per channel |

Which controls appear is decided by what your monitor advertises, not by
a hardcoded list. A projector that only offers brightness gets a short
wizard; a full desktop monitor gets the whole thing.

## Running it

```bash
python -m pytest              # the test suite
lumenctl doctor               # what this machine offers
lumenctl detect               # your monitors and their capabilities
lumenctl get 0x10             # read a value
lumenctl profiles list        # saved calibrations
lumenctl apply-default        # apply the saved calibration
```

The graphical application is built on GTK4 and libadwaita:

```bash
python -m lumen
```

## Settings

Open **Settings** from the header bar in calibration or live controls, or
press **Ctrl+,**. Preferences are saved automatically under
`$XDG_CONFIG_HOME/lumen/settings.json` (normally `~/.config/lumen/settings.json`).

- **General:** follow the system colour scheme or choose light/dark;
  choose guided calibration or live controls as the opening screen;
  choose whether Lumen manages GNOME's automatic Night Light schedule.
- **Profiles:** select the default calibration shared by the live panel,
  `lumenctl apply-default`, and the background service; control application
  at service startup and after display wake; inspect storage locations.
- **Diagnostics:** inspect the current monitor, hardware backend, software
  gamma output, and session type; reset preferences while keeping profiles
  and current monitor values.

Appearance changes immediately. Opening-screen and Night Light changes
take effect on the next launch. Selecting a default profile does not
apply it immediately. If an explicitly selected profile is removed, Lumen
does not silently apply a different one. Automatic selection preserves the
original behaviour of choosing the first profile filename alphabetically.

The login and wake options require the separately enabled user service;
they do not install or enable it. The running service reads the wake
preference again whenever the display wakes. Startup preferences apply
when the service next starts. Explicit `lumenctl apply-default` still works
when automatic application at login is disabled.

## Colorimeter calibration

Choose **Main menu → Colorimeter…** for instrument-guided X11 SDR calibration.
Install ArgyllCMS for measurement and optional colord/dbus-python for ICC
activation. Quick mode produces a matrix ICC; Detailed mode measures 729
profiling patches and produces a LUT ICC. Both independently verify the
result, support manual setup without DDC, and save exact measured curves.
Targets and instrument defaults are under **Settings → Colorimeter**.
See the [setup and workflow guide](docs/colorimeter.md).

## Privileges

Monitor control needs no elevated privileges. Lumen talks to the `ddccontrol` D-Bus service, whose policy
allows any user to read and write display controls. There is no `sudo`
step for DDC control. Optional USB colorimeters may need the distribution’s
Argyll instrument udev rules.

## Applying a calibration at login

Install the package so the `lumen-service` command is available in
`~/.local/bin`, and install the supplied user unit:

```bash
mkdir -p ~/.config/systemd/user
cp systemd/lumen-service.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now lumen-service
```

If your package installer puts commands elsewhere, adjust the unit's
`ExecStart` to the installed `lumen-service` path. After upgrading an
existing installation, copy the updated unit and restart the service.

The service applies your saved calibration when you log in, and again
after the display wakes. The DDC settings live in the monitor and
survive on their own; the software gamma ramp lives in the X server and
does not, which is why the service exists.

## Night Light

GNOME's Night Light uses the same gamma ramp that Lumen needs, and it
would silently replace your calibration with a warm-shifted one. Lumen
takes ownership of the ramp while it runs and puts the schedule back
when it exits. This behaviour can be turned off in Settings. It only
changes GNOME's automatic schedule; other colour tools and manual Night
Light settings may still affect the display. To disable the automatic
schedule permanently:

```bash
gsettings set org.gnome.settings-daemon.plugins.color night-light-schedule-automatic false
```

## Platform support

Linux on X11 today. The core is written to be portable: the model and
engine layers import nothing platform-specific, so a Windows or macOS
version is a matter of writing new backends. Software gamma is not
available on GNOME/Wayland, where Lumen falls back to hardware control
only and says so.

## Licence

Copyright (c) 2026 Lumen contributors.

Licensed under the GNU General Public License, version 3 or later
(`GPL-3.0-or-later`). See [LICENSE](LICENSE) for the full terms.
