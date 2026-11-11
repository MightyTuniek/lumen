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

## Privileges

None needed. Lumen talks to the `ddccontrol` D-Bus service, whose policy
allows any user to read and write display controls. There is no `sudo`
step, no `udev` rule and no system-wide change.

## Applying a calibration at login

```bash
systemctl --user enable --now lumen-service
```

The service applies your saved calibration when you log in, and again
after the display wakes. The DDC settings live in the monitor and
survive on their own; the software gamma ramp lives in the X server and
does not, which is why the service exists.

## Night Light

GNOME's Night Light uses the same gamma ramp that Lumen needs, and it
would silently replace your calibration with a warm-shifted one. Lumen
takes ownership of the ramp while it runs and puts the schedule back
when it exits. To disable Night Light permanently:

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

MIT. See `LICENSE`.
