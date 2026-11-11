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
