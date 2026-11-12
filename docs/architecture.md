# Architecture

Lumen is four layers. Dependencies point strictly downward.

```
ui/         GTK4 + libadwaita: wizard, live panel, test patterns
engine/     calibration logic, curve maths, apply and rollback
model/      pure data: the VCP registry, capabilities, profiles
backends/   the only code that talks to real hardware
```

## The layering rule

`model/` and `engine/` must not import `gi`, `dbus`, `Xlib` or `ctypes`.
A test in `tests/test_layering.py` enforces this by parsing the imports.

The rule exists so the project can be ported. A Windows build writes a
new `backends/ddc/windows_dxva2.py` and a new
`backends/gamma/windows_gdi.py`; the calibration logic, the profile
format and the wizard are unchanged.

## The uniform control interface

Everything adjustable is a `Control` with `read()` and `write()`, whether
it is a VCP inside the monitor, an enumerated preset, or one channel of
a software gamma ramp. The wizard and the live panel work with `Control`
objects only, so there is one implementation of "set brightness" rather
than one per layer.

## Two rules that are easy to get wrong

**Every write is verified.** A DDC write is read back; a gamma write is
flushed, synced and read back. `XRRSetCrtcGamma` is an asynchronous X
request: without an explicit flush it never reaches the server and the
ramp silently keeps its old value. A tool that reported success without
checking would work sometimes and silently fail at other times. This
was found the hard way during development, and it is now covered by a
regression test.

**Software gamma is applied before hardware gain.** The X server's gamma
LUT acts on the framebuffer before it leaves the GPU; the monitor's gain
acts after it arrives. The final response is roughly the product of the
two, so a software curve cannot recover what a hardware black level has
already crushed. The wizard therefore sets the hardware black and gain
first, then applies the curve on top.

## Why the wizard asks the user

A DDC change is applied by the monitor after the framebuffer leaves the
GPU, so no screenshot can measure it. Software cannot tell whether the
display looks better. Every judgement in the wizard is therefore the
user's, guided by a test pattern.

What the software does reliably is discover the controls, report their
true ranges, remember the choices, and put everything back if the user
cancels.

## The ordering rule that surprises people

Brightness and the black level interact. Raising the brightness after
setting a black level re-crushes the shadows. The wizard therefore sets
the black point after the baseline, and then checks the black point a
second time.

## Safety

Three groups of VCP codes can disrupt the display and are classified
`dangerous` in the registry:

| Code | What it does |
|---|---|
| `0x04` | Restores the factory defaults, destroying every setting |
| `0x8D` | In MCCS 2.2 its second byte blanks the screen |
| `0xD6`, `0xE0`-`0xE8` | DPMS and power modes, which can blank or power off |

They are never written, whatever a profile asks for, and they are never
offered in the normal control list.

## Measured calibration

The visual wizard remains a human-judgment path. The separate colorimeter
session uses instrument XYZ measurements, bounded DDC brightness/gain
optimization, ArgyllCMS iterative LUT calibration, optional detailed ICC
profiling, and an independent verification chart. It never automatically
adjusts contrast, hardware black levels, presets, hue, or saturation.

The model contains portable targets, readings, display identity, and
sampled curves. The engine orchestrates an injectable measurement adapter,
controls, and an optional ICC installer. Argyll processes, terminal I/O,
X11 output discovery/properties, advisory locks, and colord D-Bus calls
belong to backends. GTK widgets and instrument prompts stay in the UI.

Measured profiles add optional sampled curves, display targets, ICC asset
references, and verification metadata. Legacy profiles retain exponent
curves. Measured samples take precedence and are interpolated to the actual
ramp length; they replace the ramp absolutely. Both green and blue are
verified along with red. Display identity is resolved before applying a
measured profile, including in the service and CLI.

A colorimeter transaction owns output and monitor locks from capture
through review/commit or rollback. It snapshots only the hardware controls
it may adjust, all RGB ramps, and the ICC association/properties. Instrument
access is checked before display writes. Cancellation terminates child
processes before rollback; failed restoration is reported. colord profiling
inhibition and GTK sleep inhibition are released on exit. Save-only restores
the original display state; successful activation keeps the measured state.
Manual monitor-menu changes cannot be restored through unavailable DDC.

See [Colorimeter calibration](colorimeter.md) for dependency, file lifecycle,
profile ownership, and hardware-validation details.
