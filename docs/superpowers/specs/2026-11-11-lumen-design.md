# Lumen — Guided Display Calibration

**Status:** Draft for review
**Date:** 2026-11-11
**Author:** MightyTuniek
**Target platform (v1):** Linux/X11, GTK4 + libadwaita
**Future:** Linux/Wayland, other distros, Windows, macOS

> On approval this document is committed to
> `~/lumen/docs/superpowers/specs/2026-11-11-lumen-design.md`.
> The repository has already been initialised at `~/lumen`.

---

## 1. Purpose

Lumen is a guided tool for making a display look its best. It combines the
monitor's own hardware controls (DDC/CI) with software gamma shaping, and
drives the user through the process with live test patterns instead of
exposing a wall of unexplained sliders.

It replaces DDC Control, which offers the same hardware power but no
guidance, no profiles, no test patterns, and no curve control.

### Non-goals (v1)

- Colour management / ICC authoring (explicitly deferred; see §12)
- HDR calibration
- Video signal generators / 3D LUT calibration
- Networked or remote display control

---

## 2. Problem analysis: what the platform actually allows

Every claim in this section was verified on the development machine
(Zorin OS 18.1, Ubuntu 24.04 base, GNOME 46 on X11, AMD Radeon RX 580 /
Polaris 10, `radeonsi`).

### 2.1 The reference machine

| Property | Value |
|---|---|
| OS | Zorin OS 18.1 (Ubuntu 24.04 noble), GNOME 46, **X11** session |
| GPU | AMD Radeon RX 580 (XFX), `amdgpu` driver |
| Monitor | Acer VG240Y, 24" IPS, 1920×1080, connected via HDMI (DRM `HDMI-A-1`, XRandR `HDMI-A-0`) |
| EDID | PNP ID `ACR0673`; serial `0x01122d3a`; chromaticity D65 |
| DDC/CI | Working over `/dev/i2c-4` (AMD display hardware bus) |

### 2.2 Hardware controls actually available

The monitor's own DDC capability string reports:

```
cmds(01 02 03 07 0C E3 F3)
vcp(04 10 12 14(05 06 08 0B) 16 18 1A 59 5A 5B 5C 5D 5E 60(01 0F 11) 62
    6C 6E 70 8D 9B 9C 9D 9E 9F A0 D6 E0(00 04 05) E1(00 01 02)
    E2(00 01 02 03 04 05 06 07 10 11 12) E3 E4 E5 E7(00 01 02)
    E8(00 01 02 03 04))
mccs_ver(2.2) mswhql(1) asset_eep(40)
```

**21 of these are read/write, verified by write-then-read-back round-trips.**
Decoded against the ddcutil VCP table (the authoritative MCCS 2.2 mapping):

| VCP | Function | Range |
|---|---|---|
| `0x10` | Brightness / Luminosity | 0-100 |
| `0x12` | Contrast | 0-100 |
| `0x16` | Video gain: **Red** | 0-100 |
| `0x18` | Video gain: **Green** | 0-100 |
| `0x1A` | Video gain: **Blue** | 0-100 |
| `0x6C` | Video black level: **Red** | 0-100 |
| `0x6E` | Video black level: **Green** | 0-100 |
| `0x70` | Video black level: **Blue** | 0-100 |
| `0x14` | Select colour preset | enum (see 2.3) |
| `0x0C` | Colour temperature request | 0-63 steps of 100 K |
| `0x59` | Saturation: Red | 0-100 |
| `0x5A` | Saturation: Yellow | 0-100 |
| `0x5B` | Saturation: Green | 0-100 |
| `0x5C` | Saturation: Cyan | 0-100 |
| `0x5D` | Saturation: Blue | 0-100 |
| `0x5E` | Saturation: Magenta | 0-100 |
| `0x9B`-`0xA0` | 6-axis hue: Red/Yellow/Green/Cyan/Blue/Magenta | 0-100 |
| `0x62` | Audio speaker volume | 0-100 |

The "white-to-black scale" described in DDC Control is exactly
`0x16/0x18/0x1A` (the white end) plus `0x6C/0x6E/0x70` (the black end),
one pair per colour channel.

### 2.3 Colour preset values (`0x14`)

| Value | Meaning |
|---|---|
| `0x01` | sRGB |
| `0x02` | Display Native |
| `0x03`-`0x0A` | 4000 K - 11500 K |
| `0x0B`-`0x0D` | User 1 / User 2 / User 3 |

This monitor natively supports `0x05` (6500 K), `0x06` (7500 K), `0x08`
(9300 K) and `0x0B` (User 1).

### 2.4 Software controls: X11 gamma ramp

- `XRRGetCrtcGammaSize` on the HDMI-A-0 CRTC returns **1024 entries per
  channel** - 10x the resolution of DDC's 0-100 steps.
- `XRRSetCrtcGamma` accepts independent R/G/B arrays. Verified round-trip:
  wrote a 1.8 gamma curve, read back `[0, 1394, 2049]` instead of the
  linear `[0, 64, 128, ...]`.
- The ramp **persists across process boundaries**. Verified: a synced
  write was still intact when read by a separate process 20 seconds later,
  and across a 180-second observation window with zero external
  interference.
- `xrandr --gamma` works but accepts only a single `r:g:b` triple, not a
  full ramp - Lumen should use the RandR API directly.
- The monitor does **not** expose a hardware gamma LUT: VCP `0x50` (set
  gamma) and `0x51` (get gamma) both read back unsupported (`res=0`). So
  software gamma is the *only* way to shape the curve.

#### 2.4.1 The gamma write must be explicitly flushed - a real trap

`XRRSetCrtcGamma` is an **asynchronous** X request. python-xlib queues it;
it is only transmitted when the connection is flushed. If the process
exits without flushing, **the write is silently discarded** and the ramp
keeps its previous value.

This was observed directly during development and initially looked like
the display was resetting the ramp on its own. A controlled experiment
isolated it:

| Write method | Ramp afterwards |
|---|---|
| `xrandr_set_crtc_gamma(...)`, process exits | **unchanged - write lost** |
| `xrandr_set_crtc_gamma(...)` + `display.sync()` | changed correctly |
| `xrandr_set_crtc_gamma(...)` + `flush()` + `sync()` | changed correctly |

**Consequence for Lumen:** every gamma write must be followed by an
explicit flush/sync, and must be **verified by reading the value back**
before the operation is reported as successful. Without this, the tool
would appear to work sometimes and silently fail to apply otherwise -
the worst possible failure mode for a calibration tool. This is a
first-class requirement of `XRandRGammaBackend`, and a regression test
(§9) must cover it.

### 2.5 The three constraints that shape the architecture

**(a) DDC changes are invisible to software.**
The monitor applies gain and black-level adjustments *after* the signal
leaves the GPU. A framebuffer screenshot is unaffected by them. Verified:
a full-screen white window sampled via `import -window root` returned
`(255,255,255)` before and after writing `0x16` to 0. Consequence:
**Lumen cannot measure DDC effects. Every "automatic" judgement must be
made by the user, guided by test patterns.** Any feature promising
screenshot-based auto-detection of black/white point is impossible on this
hardware class.

**(b) Persistence differs per layer.**
DDC settings live in the monitor and survive sleep, reboot and X restarts
(until the monitor itself power-cycles). The software gamma ramp lives in
the X server and dies with the session. A complete tool must manage both
lifetimes.

**(c) Other software owns the gamma ramp.**
`gsd-color` (GNOME's colour daemon) takes the CRTC gamma to implement
Night Light. On this machine the schedule is automatic, **20:00 to 06:00**,
though `night-light-enabled` was observed `false` and gsd-color reported
`NightLightActive: false` throughout testing - so **no external overwrite
was observed during development**. That is not a guarantee: the moment
Night Light activates, gsd-color will write the ramp and Lumen's
calibration would be silently replaced by a warm-shifted one.

`colord` additionally manages ICC profiles per output.

Consequence: Lumen must still be the *owner* of the ramp for its
lifetime. It cannot rely on "nothing else is using it right now" - it must
detect interference and re-assert, because the interference is scheduled
and would otherwise arrive at the worst moment (an evening session).
Note that this requirement is now the *only* source of gamma contention:
the apparent "mysterious resets" during development were Lumen's own
unsynced writes (§2.4.1), not an external agent.

### 2.6 Access and permissions

- `ddccontrol`'s D-Bus service (`ddccontrol.DDCControl`, path
  `/ddccontrol/DDCControl`) runs as root, and its system policy is
  `<policy context="default"><allow send_destination="ddccontrol.DDCControl"/>`.
  **Any user may read and write VCPs through it - no `sudo`, no `udev`
  rule, no group membership.** This is why Lumen needs no elevated
  privileges on this machine.
- Direct `/dev/i2c-*` access is root-only (`crw-------`), so a tool that
  speaks I2C directly would need a shipped `udev` rule and a privileged
  install step. Lumen does not do this.
- The same D-Bus interface exposes the monitor's raw capability string via
  `OpenMonitor`, so Lumen can read capabilities without any VCP probing.

### 2.7 Portability implications per platform

Lumen's DDC/CI layer must reach hardware through a different mechanism on
each OS. These mechanisms were verified from authoritative sources and from
production implementations (Monitorian for Windows, BetterDisplay for
macOS).

| Platform | DDC/CI mechanism | Gamma ramp mechanism | Elevated rights? |
|---|---|---|---|
| **Linux / X11** | D-Bus `ddccontrol` (no root); `ddcutil` (needs `udev` rule for `/dev/i2c-*`) | `XRRSetCrtcGamma`, 1024 entries/ch | **No** via ddccontrol |
| **Linux / Wayland** | same as X11 | **Unavailable on GNOME** - mutter implements no `gamma-control-unstable-v1`; only wlroots/KDE compositors do | No |
| **Windows** | `Dxva2.dll` - `CapabilitiesRequestAndCapabilitiesReply`, `GetVCPFeatureAndVCPFeatureReply`, `SetVCPFeature` over `CreateFile("\\\\.\\DISPLAY<n>")`. WMI `WmiMonitorBrightnessMethods` as fallback for laptop panels | `SetDeviceGammaRamp` (GDI) | No |
| **macOS** | IOKit `IOAVServiceWriteI2C` via the private DisplayServices framework (`IOAVServiceCreateIOAVService`) - the path BetterDisplay uses | `CGDisplaySetDeviceGammaRamp` (deprecated on modern macOS; restricted on Apple Silicon) | No (private API) |

Additional portability constraints:

- **Wayland is a real gap, not a footnote.** On GNOME/Wayland there is no
  software gamma at all. Lumen must degrade to hardware-only there and say
  so plainly rather than fail. This machine has a `zorin-wayland.desktop`
  session available, so the path will be exercised.
- **Windows has no `dxva2` equivalent on ARM**, and `SetDeviceGammaRamp` is
  restricted on HDR-capable outputs.
- **macOS DisplayServices is a private API**; it can break between macOS
  releases and is unavailable under some sandboxing. It must be an
  optional, degradable backend, never the only one.
- **Hardware gamma LUT (`0x50`/`0x51`)** is rare but real on professional
  panels. Lumen must detect and use it when present, and skip it when not.
- **Multi-monitor, hotplug, and clone/extend mode** all change which CRTC
  maps to which output. Display topology must be re-read, never cached
  across a topology change.
- **Laptop panels** are a different control class entirely: a backlight
  API, not DDC/CI, with no contrast or gain controls at all. Lumen's model
  must accommodate that.
- **Licensing**: Lumen's VCP registry is derived from the *public MCCS
  specification*, not copied from ddcutil's GPL source. ddccontrol is
  invoked only over its documented D-Bus interface, which is an API, not a
  code dependency. Lumen itself is GPL-3.0-only licensed.

---

## 3. Architecture

### 3.1 Layering

```
+---------------------------------------------------------+
| ui/            GTK4 + libadwaita                        |
|                wizard window, live panel, test patterns |
+---------------------------------------------------------+
| engine/        calibration state machine, curve maths,  |
|                profile apply/rollback, ordering rules   |
+---------------------------------------------------------+
| model/         VCP registry, monitor descriptor,        |
|                profile (de)serialisation, capability     |
|                parsing, channel mapping                  |
+---------------------------------------------------------+
| backends/      platform adapters, one interface          |
|                DdcBackend - GammaBackend - NightLight    |
+---------------------------------------------------------+
```

Dependencies point strictly downward. `engine/` and `model/` never import
`gi`, never touch D-Bus or X11 directly. That is what makes the future
Windows/macOS port a matter of writing new `backends/` adapters rather
than rewriting the product.

### 3.2 The uniform `Control` interface

Everything the user can adjust becomes one object, so the wizard and the
live panel never need to know whether a value lives in the monitor or in
the X server:

```python
class Control(Protocol):
    code: str                     # stable identifier, e.g. "vcp_0x10"
    label: str                    # human name
    kind: Literal["vcp", "gamma"]
    minimum: float
    maximum: float
    def read(self) -> float: ...
    def write(self, value: float) -> None: ...
```

Implementations: `VcpControl` (a VCP code via a `DdcBackend`),
`GammaControl` (one channel of the CRTC ramp via a `GammaBackend`),
`PresetControl` (enumerated values such as colour presets).

### 3.3 Backend interfaces

```python
class DdcBackend(Protocol):
    def list_monitors(self) -> list[MonitorDescriptor]: ...
    def get(self, monitor: MonitorDescriptor, vcp: int) -> VcpValue: ...
    def set(self, monitor: MonitorDescriptor, vcp: int, value: int) -> None: ...
    def capabilities(self, monitor: MonitorDescriptor) -> Capabilities: ...

class GammaBackend(Protocol):
    def outputs(self) -> list[OutputDescriptor]: ...
    def read_ramp(self, output: OutputDescriptor) -> Ramp: ...      # 3xN uint16
    def write_ramp(self, output: OutputDescriptor, ramp: Ramp) -> None: ...
```

**Linux/X11 implementations (v1):**

- `DdcControlDBusBackend` - talks to `ddccontrol.DDCControl` over the
  system bus. Read/write of every VCP, plus the raw capability string from
  `OpenMonitor`. Requires no privileges.
- `XRandRGammaBackend` - `XRRGetCrtcGammaSize` / `XRRGetCrtcGamma` /
  `XRRSetCrtcGamma` via `ctypes` on `libXrandr`, resolving the CRTC from
  the XRandR output each time (never cached across topology changes).
  **Every write is followed by an explicit flush and a read-back
  verification** - see §2.4.1. A write that cannot be confirmed is
  reported as a failure, never as a success.
- `GnomeNightLightController` - reads and writes
  `org.gnome.settings-daemon.plugins.color night-light-*` via `gsettings`,
  and detects the 20:00-06:00 window.

**Future backends (interfaces shaped for them, not implemented in v1):**

- `DdcUtilBackend` - shells out to `ddcutil`, for systems where the
  ddccontrol daemon is unavailable. Needs the `udev` rule; Lumen ships the
  rule and an optional privileged setup step.
- `WindowsDxva2Backend` - `Dxva2.dll` bindings.
- `MacOSDisplayServicesBackend` - IOKit `IOAVServiceWriteI2C`.
- `WaylandGammaBackend` - `gamma-control-unstable-v1`, present on
  wlroots/KDE, absent on GNOME.
- `LinuxBacklightBackend` - laptop panels via `/sys/class/backlight`.

### 3.4 Capability discovery

On start-up Lumen enumerates monitors and asks each for its capability
string. It then **intersects the monitor's advertised VCP list with its own
registry**, so only controls the hardware actually supports are offered.
Registry entries unsupported by the monitor are hidden, not shown as dead
sliders.

The registry is Lumen's own data, derived from the public MCCS
specification.

### 3.5 The VCP registry

A data table (Python dict or JSON), not code. Each entry:

```python
{
  "code": 0x10,
  "name": "Brightness",
  "group": "image",          # image | colour | audio | power | input
  "min": 0, "max": 100,
  "writable": True,
  "safety": "safe",          # safe | confirm | dangerous
  "affects": ["black_point"],  # dependency edges - see 6.4
}
```

Safety classification, from the verified capabilities:

| Class | Codes | Rationale |
|---|---|---|
| `safe` | `0x10 0x12 0x16 0x18 0x1A 0x6C 0x6E 0x70 0x59`-`0x5E 0x9B`-`0xA0 0x14 0x0C 0x62` | Only affect image parameters |
| `confirm` | `0x60` (input source) | Switching to an absent input can blank the screen |
| `dangerous` | `0x04` (restore factory defaults), `0x8D` (audio mute **and screen blank**), `0xD6` (DPMS), `0xE0`-`0xE8` (power/scaling) | Can blank the screen, power the monitor off, or destroy all settings |

`0x8D` deserves explicit attention: in MCCS 2.2 its second byte blanks the
screen. A tool that exposes it carelessly can black out the display.

### 3.6 Module layout

```
lumen/
|-- lumen/
|   |-- __main__.py
|   |-- model/
|   |   |-- vcp_registry.py      # the VCP table + safety classes
|   |   |-- monitor.py           # MonitorDescriptor, Capabilities
|   |   |-- profile.py           # Profile model + serialisation
|   |   |-- capability.py        # parse MCCS capability strings
|   |   `-- channels.py          # RGB channel mapping + verification
|   |-- backends/
|   |   |-- base.py              # protocols
|   |   |-- ddc/
|   |   |   |-- ddccontrol_dbus.py
|   |   |   `-- ddcutil_cli.py   # future
|   |   |-- gamma/
|   |   |   `-- xrandr.py
|   |   |-- session/
|   |   |   |-- night_light.py   # GNOME
|   |   |   `-- wayland.py       # session-type detection
|   |   `-- registry.py          # backend discovery + selection
|   |-- engine/
|   |   |-- calibration.py       # wizard state machine
|   |   |-- steps.py             # step definitions
|   |   |-- curves.py            # gamma curve maths
|   |   |-- apply.py             # ordered apply + rollback
|   |   `-- conflict.py          # gamma ownership arbitration
|   |-- ui/
|   |   |-- app.py
|   |   |-- wizard.py
|   |   |-- panel.py
|   |   |-- patterns.py          # Cairo test patterns
|   |   `-- widgets.py
|   `-- service/
|       `-- daemon.py            # systemd --user service
|-- lumenctl                     # CLI entry point
|-- tests/
|-- docs/
`-- pyproject.toml
```

`model/` and `engine/` import nothing from `gi`, `dbus` or `Xlib`. This is
enforced by a test (see section 9).

---

## 4. The calibration wizard

The wizard is a state machine (`engine/calibration.py`) driving a sequence
of steps (`engine/steps.py`). Each step declares:

- the controls it exposes (from the registry, filtered by capability)
- the test pattern to display
- what the user should look for, in plain language
- validation rules for advancing

Steps are *skipped* when the monitor lacks the required controls, so the
same wizard works on a laptop panel (no gain, no black level, no contrast)
as on a full desktop monitor.

### 4.1 Step sequence

| # | Step | Controls | Pattern | What the user is told |
|---|---|---|---|---|
| 1 | **Detect** | - | - | "Found your Acer VG240Y over HDMI. It supports brightness, contrast, per-channel white and black levels, colour temperature and full saturation/hue control." |
| 2 | **Baseline** | brightness, contrast | flat grey field | "Set brightness and contrast to a comfortable starting point. Ignore colour for now." |
| 3 | **White point** | gain R/G/B (`0x16/0x18/0x1A`) | white patch on black | "Raise the white point until the patch is as bright as you want, without the panel blooming. Then adjust R/G/B until the patch looks neutral to you." |
| 4 | **Black point** | black level R/G/B (`0x6C/0x6E/0x70`) | near-black ramp | "Set the black level so the darkest step is truly black, but the second step is still distinguishable. Adjust each channel so shadows have no colour cast." |
| 5 | **Re-check black** | (repeat of 4) | near-black ramp | Step 2's brightness change can re-crush blacks; this step re-runs 4 after any brightness edit. See 6.4. |
| 6 | **Colour temperature** | preset `0x14`, Kelvin `0x0C`, gain R/G/B | greyscale ramp | "Pick the preset closest to what you want (sRGB / Native / 6500 K / 7500 K / 9300 K), then fine-tune with the per-channel gains until the ramp looks neutral." |
| 7 | **Gamma curve** | software ramp (per channel) | greyscale ramp | "Adjust the curve so midtones feel right - a higher value lightens the mids, a lower one deepens them. This is the fine control the hardware can't do." |
| 8 | **Saturation & hue** | `0x59`-`0x5E`, `0x9B`-`0xA0` | colour patches | "Confirm each colour looks right, and that hues point the right way. Most people leave these at the default." |
| 9 | **Verify channels** | - | pure R/G/B patches | "Confirm each patch is the colour you expect. Some monitors swap green and blue." See 6.5. |
| 10 | **Save** | - | - | Name the profile; it is stored and applied automatically at next login. |

### 4.2 Why the wizard cannot automate

Per 2.5(a), Lumen cannot read back the visual effect of a DDC change. Every
step above therefore presents a pattern and asks the user to judge. This is
not a limitation to apologise for - it is how professional calibration is
done with a colourimeter too (the meter measures, the human still decides
about intent), and it is what makes the tool work on hardware with no
measurement hardware attached.

What Lumen *can* automate: reading the true range of every control
(`maximum` is reported per-VCP by the monitor), detecting which controls
exist, remembering the user's choices, and re-applying them.

---

## 5. Profiles

### 5.1 Model

A profile stores *intent*, not raw values, so it stays meaningful when
applied to different hardware:

```python
@dataclass
class Profile:
    name: str
    created: datetime
    monitor: MonitorDescriptor        # EDID hash, model, serial
    ddc: dict[int, int]               # explicit VCP overrides
    gamma: GammaCurve                 # exponent, black lift, white trim
    color_temp_kelvin: int | None
    notes: str
```

`GammaCurve` holds parameters (exponent per channel, optional black lift
and white trim) rather than 1024 raw values, so it is readable, diffable
and re-derivable. The raw ramp is always reproducible from the parameters.

### 5.2 Storage

`~/.config/lumen/profiles/<slug>.json`, plus `active` pointing at the
default. XDG-compliant, so it behaves on every distro. No writes outside
the user's home directory; no system-level state at all.

### 5.3 Applying a profile

`engine/apply.py` applies in a fixed, deliberate order (§6.4), and is
**idempotent** - applying the same profile twice yields the same state, and
applying it to a monitor that has drifted restores it exactly.

---

## 6. Hard problems and how they are handled

### 6.1 Screen blanking and display power

`0x8D`, `0xD6`, `0xE0`-`0xE8` can blank or power off the display. These are
**never exposed in the normal UI**. They are reachable only from an
"Advanced" section that:

- shows each code with its literal MCCS name,
- requires an explicit typed confirmation,
- refuses to run while a wizard session is active,
- and, for `0x04` (restore factory defaults), requires typing the word
  `RESET` *and* states plainly that all current monitor settings will be
  lost.

The user is never one misclick away from a black screen.

### 6.2 Rollback

Every wizard session takes a **snapshot** at entry: all writable VCP values
plus the gamma ramp. `Cancel` at any point restores that snapshot exactly.
The snapshot is also written to disk so it survives a crash, and is
restored automatically if Lumen is killed mid-session.

### 6.3 Gamma ownership and Night Light

`engine/conflict.py` mediates:

1. On start, Lumen detects the session type and whether GNOME's
   `gsd-color` is active.
2. It reads the current Night Light schedule and **offers** to disable the
   automatic schedule (a user-level `gsettings` write - no `sudo`). The
   previous value is saved and restored when Lumen exits.
3. While Lumen owns the ramp it **re-asserts** it periodically (a cheap
   `XRRGetCrtcGamma` compare) so that a stray `gsd-color` write is
   detected and reversed rather than silently corrupting the calibration.
   This matters most between 20:00 and 06:00, when Night Light is
   scheduled to act (§2.5c).
4. On Wayland/GNOME, where no gamma exists, Lumen says so plainly and
   proceeds with hardware-only control instead of failing.

### 6.4 Ordering and interaction

Two ordering rules come from how the hardware actually behaves:

**Rule 1 - black after brightness.** Brightness (`0x10`) and black level
(`0x6C/0x6E/0x70`) interact: raising brightness after setting black level
re-crushes the shadows. The wizard therefore sets black level *after*
brightness, and step 5 re-checks it.

**Rule 2 - software gamma composes multiplicatively with hardware gain.**
The X server's gamma LUT is applied to the framebuffer *before* it leaves
the GPU; the monitor's gain is applied *after*. So the final response is
roughly `software_gamma(x) * hardware_gain`. Consequences:

- A software curve cannot recover what a hardware black level has already
  crushed - the hardware acts last and wins.
- Therefore the wizard sets hardware black/gain **first**, then applies the
  software curve on top.
- The live panel shows the effective relationship rather than pretending
  the two are independent.

The registry's `affects` field encodes these edges so the UI can warn
("changing brightness will re-crush the black level you just set").

### 6.5 RGB channel mapping

The DDC/CI standard and ddcutil's table both map `0x16`=Red, `0x18`=Green,
`0x1A`=Blue, and the reference monitor advertises `16 18 1A` - but **some
monitors transpose green and blue**, and the capability string cannot
reveal it. Because the effect is unmeasurable in software, wizard step 9
verifies it visually and records the mapping in the profile
(`model/channels.py`). If the user reports a swap, Lumen applies the
permutation to all subsequent operations rather than silently sending
green to the blue channel.

### 6.6 Failure handling

- **DDC write failure** (I2C timeout, monitor asleep): retry with
  backoff, then report. Never assume success.
- **Gamma write not applied**: the write is flushed and then read back; if
  the value does not match, the operation is reported as failed rather
  than silently ignored (§2.4.1).
- **Monitor disappears** (unplugged, cable): detect on the next operation,
  pause the session, and offer to resume when it returns.
- **Gamma backend unavailable**: degrade to hardware-only and state it.
- **Daemon restart**: systemd `Restart=always`; the daemon re-reads
  topology and re-applies on wake.

---

## 7. Live panel

After the wizard, a persistent panel gives day-to-day control:

- grouped sliders (Image / White point / Black point / Colour / Curve),
- only controls the monitor actually supports,
- every change written immediately,
- a profile switcher,
- a **"Restore last profile"** button,
- an indicator showing which controls are locked by the current ordering
  rules.

The panel and the wizard share the same `Control` objects, so there is one
implementation of "set brightness", not two.

---

## 8. Persistence at login

A **systemd user service** (`systemd/lumen-service.service`):

- starts after `graphical-session.target`,
- applies the active profile,
- re-applies the software gamma after suspend/resume and after the display
  is re-enabled (DPMS),
- re-reads display topology on every wake, so a monitor added or removed
  is handled,
- holds no elevated privileges.

Because DDC settings live in the monitor and survive power cycles, only the
software gamma strictly needs re-applying; the service re-applies DDC too,
but only when it detects drift.

---

## 9. Testing

| Level | Scope | Notes |
|---|---|---|
| Unit | `model/`, `engine/` | Curve maths, profile (de)serialisation, capability-string parsing, VCP registry, ordering rules. No hardware. |
| Backend contract | Fake DDC + fake gamma | Both implement the same protocols; the whole engine runs against fakes. Simulates I2C timeouts, unsupported VCPs, out-of-range writes, hotplug. |
| Layering guard | Import check | A test asserts `model/` and `engine/` never import `gi`, `dbus` or `Xlib`. This is what keeps the Windows/macOS port viable. |
| Integration | Against real hardware | Read-only by default. `--write` opt-in, with automatic restore of every value it touches. |
| Regression | Gamma flush semantics | A test asserts that a gamma write is **actually applied** after the writer process exits - the §2.4.1 failure mode. |
| Manual | Visual | Wizard walked end to end on the Acer VG240Y; plus a Wayland session run to confirm graceful degradation. |

The fakes are not optional decoration: they are how the engine stays
testable and how the future ports are developed without the hardware.

---

## 10. Public-project considerations

Since Lumen should be usable beyond this machine, the following are
requirements rather than nice-to-haves.

### 10.1 All monitor types

Lumen must not assume the reference monitor's control set:

- **Discovery is capability-driven**, never hardcoded (§3.4).
- **Control classes differ**: a full desktop monitor, a laptop panel
  (backlight only), a TV (often a reduced DDC set), and a projector (may
  expose almost nothing) each get a different available-control set, and
  the wizard adapts.
- **Laptops** need a backlight backend, not DDC.
- **Enums** (colour preset, input source) vary per monitor; the capability
  string's parenthesised value lists are parsed to populate them.
- **Unknown VCPs** are shown generically ("VCP 0xNN") rather than hidden,
  so an unusual monitor still exposes its full capability.
- **Multi-monitor** is a first-class case, with per-monitor profiles.

### 10.2 All Linux distros

- **No distro-specific assumptions.** No hardcoded paths, no reliance on
  GNOME-specific tooling outside the Night Light module, which is
  optional and detected.
- **Backends are pluggable and discovered at runtime** (§3.3), with a
  documented capability probe and a clear message when nothing suitable
  is found.
- **Packaging**: a `pyproject.toml` supporting `pipx`/`pip` install, plus a
  Flatpak manifest. The Flatpak must declare `--talk-name=ddccontrol.DDCControl`
  so the D-Bus route keeps working inside the sandbox - a detail that is
  easy to get wrong and breaks the app silently.
- **No required privileged install.** Where a distro lacks the ddccontrol
  daemon, the `ddcutil` route needs a `udev` rule; Lumen ships the rule
  and an optional `sudo` helper, and remains fully functional without it
  if D-Bus is available.
- **Wayland** is handled explicitly, including the GNOME gamma gap.

### 10.3 Future Windows and macOS

The layer boundaries in §3.1 are the porting strategy:

- `model/` and `engine/` are pure Python and port unchanged.
- `backends/ddc/windows_dxva2.py` and `backends/ddc/macos_displayservices.py`
  implement the same `DdcBackend` protocol.
- `backends/gamma/windows_gdi.py` and `backends/gamma/macos_cg.py`
  implement `GammaBackend`.
- `backends/session/` gains macOS "Night Shift" and Windows "Night Light"
  equivalents for gamma ownership.
- **The GUI is the real constraint.** GTK4 on Windows is workable but
  unusual; on macOS it is painful. The pragmatic long-term answer is a
  second, thin UI layer over the same engine, or a web-based UI served by
  the local daemon. The engine's purity is what makes either possible.

To keep that door open, the UI layer talks to the engine only through the
`Control` and profile interfaces - never through backends directly.

### 10.4 Licensing and provenance

- **GPL-3.0-only licensed**, with a `LICENSE` file.
- The **VCP registry** is authored from the public MCCS/DDC-CI
  specification, citing it in the source. ddcutil's GPL source is *not*
  copied; its table was used as a cross-check during design only.
- **ddccontrol** is used only via its documented D-Bus interface - an API,
  not a code dependency - so no copyleft is inherited.
- Each test pattern and curve is original work.

---

## 11. CLI

`lumenctl` shares the engine with the GUI:

```
lumenctl detect                    # list monitors + capabilities
lumenctl get 0x10                  # read a VCP
lumenctl set 0x10 60               # write a VCP
lumenctl profiles list|show|apply|rm
lumenctl apply <profile>           # apply a profile
lumenctl capture                   # snapshot current state
lumenctl restore <snapshot>        # restore a snapshot
lumenctl doctor                    # diagnose backends, permissions, conflicts
```

`lumenctl doctor` is the first thing to run when something does not work:
it reports which backends were detected, whether DDC is reachable without
root, whether a gamma backend exists, and whether another process owns the
ramp.

---

## 12. Deferred: ICC and colour management

Explicitly out of scope for v1, by decision. `colord` already manages an
EDID-derived profile for this monitor, and the user chose not to touch it.

The interface is left open for it: a future `model/icc.py` plus a
`colord`/`ColorSync`/ICM backend could generate and install a real
profile from the wizard's results. The `Profile` model already has a home
for it. Nothing in v1 depends on this decision.

---

## 13. Success criteria

1. A user who has never calibrated a display can run the wizard and end up
   with a measurably better image, without being told what any of the
   numbers mean.
2. The wizard is safe: no reachable action can blank or power off the
   display, and cancelling always restores the previous state exactly.
3. Settings survive a reboot and are reapplied at login.
4. The app requires no `sudo`, no `udev` rule, and no system-level
   changes on the reference machine.
5. Lumen works on a monitor with a completely different control set, on
   a laptop panel, and on a multi-monitor setup, without code changes.
6. The engine is provably free of platform imports, so a Windows or macOS
   port is a matter of writing backends.
7. It runs correctly on a Wayland session, degrading honestly where the
   platform cannot support a feature.

---

## 14. Open questions for the implementation phase

1. **Curve editing UX.** The 1024-entry ramp is powerful but unwieldy. A
   simple exponent slider covers most needs; a free-form curve editor is
   more capable. Likely: exponent slider plus a small set of anchor
   handles, with the raw ramp in the CLI.
2. **Test pattern rendering.** Cairo on a GTK4 `DrawingArea` is the obvious
   choice; alternatively pre-rendered PNGs at high resolution. The latter
   is simpler and pixel-exact but less flexible.
3. **Whether the daemon should also restore DDC after monitor power
   cycles**, or assume the monitor remembers.
4. **Flatpak or not for v1** - the D-Bus policy interaction needs testing
   before it is offered as a primary install path.
