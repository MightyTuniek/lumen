# Lumen Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the platform-independent core of Lumen — the model, engine and backend layer that discovers a monitor's capabilities, exposes every adjustable value through one uniform interface, and can capture, apply, roll back and re-apply a calibration profile.

**Architecture:** Four strictly layered modules (`model` → `engine` → `backends` → `ui`). `model/` and `engine/` import nothing from `gi`, `dbus`, `Xlib` or `ctypes`, so the same engine runs on Linux, Windows and macOS against different backend adapters. Every adjustable value — a DDC VCP in the monitor, or a channel of the X11 gamma ramp — is wrapped in the same `Control` object with `read()`/`write()`, so the wizard and the live panel never branch on where the value lives.

**Tech Stack:** Python 3.12, pytest 9.1, `dbus-python` (backend layer only), `ctypes` on `libXrandr` (backend layer only). GTK4/libadwaita is a later task, UI layer only.

**Spec:** `docs/superpowers/specs/2026-11-11-lumen-design.md`

## Global Constraints

- Python 3.12 minimum. No runtime dependency may be required for `model/` or `engine/` beyond the standard library.
- `model/` and `engine/` must not import `gi`, `dbus`, `Xlib` or `ctypes`. Enforced by a test in Task 1.
- `model/` and `engine/` must not touch display hardware directly; all hardware access goes through a backend implementing the protocols from Task 4.
- Every DDC write must be followed by a read-back verification. A write that cannot be confirmed is reported as a failure, never a success.
- Every gamma write must be followed by an explicit flush and a read-back verification. (Spec §2.4.1 — the write is silently discarded otherwise.)
- Controls are classified `safe`, `confirm` or `dangerous`. `dangerous` controls are never exposed by the normal UI path.
- The VCP registry is authored from the public MCCS/DDC-CI specification. No GPL source (ddcutil) may be copied.
- The project is MIT licensed.
- The application writes nothing outside the user's home directory (XDG config dir only).
- Identifiers use lowercase_with_underscores. No abbreviations that are not in the MCCS specification.

## Review Focus

The input classes and failure modes the spec implies that a user is most likely to hit. Each is pinned by a test in the task that owns the relevant code.

- **A DDC write that silently fails** — the monitor is asleep, the I²C bus times out, or the write is rejected. The tool must report failure and leave the previous value intact, not show a slider that moved when nothing changed. *(Task 4)*
- **A gamma write that is silently discarded** — `XRRSetCrtcGamma` is asynchronous; without a flush the ramp never changes and the tool reports success anyway. *(Task 5)*
- **The monitor reports a VCP whose range differs from the registry's assumption** — e.g. a monitor whose brightness maximum is 50, not 100. The tool must use the reported maximum, never the assumed one. *(Task 6)*
- **A monitor that supports none of the expected controls** — a projector, or a panel where only brightness works. The wizard must skip unsupported steps and still complete, rather than showing dead sliders. *(Task 9)*
- **The user cancels the wizard mid-calibration** — every value changed during the session must be restored exactly, including the gamma ramp. *(Task 10)*
- **Two monitors, one of which is disconnected mid-session** — the tool must not apply a profile to the wrong output or crash. *(Task 10)*
- **A profile saved on one monitor is applied to a different monitor** — the profile must be rejected or flagged, not silently applied with mismatched semantics. *(Task 11)*

---

## File Structure

| Path | Responsibility |
|---|---|
| `lumen/__init__.py` | Package marker, version string |
| `lumen/model/vcp_registry.py` | The VCP code table: name, group, range, safety class, dependency edges |
| `lumen/model/capability.py` | Parse MCCS capability strings into code sets and enum values |
| `lumen/model/monitor.py` | `MonitorDescriptor`, `Capabilities`, `VcpValue` dataclasses |
| `lumen/model/profile.py` | `Profile`, `GammaCurve` dataclasses plus JSON (de)serialisation |
| `lumen/model/channels.py` | RGB channel mapping and its verification state |
| `lumen/engine/controls.py` | The uniform `Control` protocol and its implementations |
| `lumen/engine/curves.py` | Gamma curve maths: exponent, black lift, white trim, ramp construction |
| `lumen/engine/apply.py` | Ordered profile application, idempotency, rollback |
| `lumen/engine/calibration.py` | Wizard state machine and step definitions |
| `lumen/engine/conflict.py` | Gamma ownership arbitration and drift detection |
| `lumen/backends/base.py` | `DdcBackend`, `GammaBackend` protocols and their error types |
| `lumen/backends/ddc/ddccontrol_dbus.py` | DDC over the `ddccontrol` D-Bus service |
| `lumen/backends/gamma/xrandr.py` | Gamma ramp over `libXrandr` via `ctypes` |
| `lumen/backends/registry.py` | Backend discovery and selection |
| `lumen/backends/fakes.py` | Fake DDC and gamma backends for tests |
| `tests/` | pytest suite mirroring the module layout |

---

### Task 1: Project skeleton and the layering guard

**Files:**
- Create: `lumen/__init__.py`, `lumen/model/__init__.py`, `lumen/engine/__init__.py`, `lumen/backends/__init__.py`, `lumen/backends/ddc/__init__.py`, `lumen/backends/gamma/__init__.py`
- Create: `pyproject.toml`
- Create: `tests/test_layering.py`

**Interfaces:**
- Consumes: nothing.
- Produces: importable package `lumen` with subpackages `model`, `engine`, `backends`; `lumen.__version__ == "0.1.0"`; pytest configured to discover `tests/`.

- [ ] **Step 1: Write the failing test**

`tests/test_layering.py`:

```python
"""Guard: the portable core must never import platform-specific modules.

If model/ or engine/ gains an `import dbus`, the project can no longer be
ported to Windows or macOS without rewriting the core. This test is the
mechanical enforcement of that rule.
"""

import ast
import pathlib

import pytest

FORBIDDEN = {"gi", "dbus", "Xlib", "ctypes"}
PORTABLE_PACKAGES = ("model", "engine")

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _imported_modules(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                names.add(node.module.split(".")[0])
    return names


@pytest.mark.parametrize("package", PORTABLE_PACKAGES)
def test_portable_packages_do_not_import_platform_modules(package: str) -> None:
    offenders: list[str] = []
    for path in (ROOT / "lumen" / package).rglob("*.py"):
        bad = _imported_modules(path) & FORBIDDEN
        if bad:
            offenders.append(f"{path.relative_to(ROOT)} imports {sorted(bad)}")
    assert not offenders, "platform imports leaked into the portable core:\n" + "\n".join(offenders)


@pytest.mark.parametrize("package", PORTABLE_PACKAGES)
def test_portable_packages_import_without_display_hardware(package: str) -> None:
    """The core must be importable on a machine with no display at all."""
    import importlib
    import pkgutil

    module = importlib.import_module(f"lumen.{package}")
    for info in pkgutil.iter_modules(module.__path__, f"lumen.{package}."):
        importlib.import_module(info.name)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/lumen && python -m pytest tests/test_layering.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lumen'`

- [ ] **Step 3: Write minimal implementation**

`lumen/__init__.py`:

```python
"""Lumen - guided display calibration."""

__version__ = "0.1.0"
```

`lumen/model/__init__.py`:

```python
"""Model layer: pure data and pure functions. No platform imports."""
```

`lumen/engine/__init__.py`:

```python
"""Engine layer: calibration logic. No platform imports."""
```

`lumen/backends/__init__.py`:

```python
"""Backend layer: the only place that talks to real hardware."""
```

`lumen/backends/ddc/__init__.py`:

```python
"""DDC/CI backend adapters."""
```

`lumen/backends/gamma/__init__.py`:

```python
"""Software gamma backend adapters."""
```

`pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "lumen"
version = "0.1.0"
description = "Guided display calibration"
requires-python = ">=3.12"
license = { text = "MIT" }
dependencies = []

[project.optional-dependencies]
gui = ["PyGObject>=3.48"]
dev = ["pytest>=8"]

[tool.setuptools.packages.find]
include = ["lumen*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/lumen && python -m pytest tests/test_layering.py -v`
Expected: PASS (4 parametrised cases)

- [ ] **Step 5: Commit**

```bash
cd ~/lumen && git add lumen pyproject.toml tests
git commit -m "feat: project skeleton with platform-import guard"
```

---

### Task 2: The VCP registry

**Files:**
- Create: `lumen/model/vcp_registry.py`
- Create: `tests/model/test_vcp_registry.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `enum.Enum` `Safety` with members `SAFE`, `CONFIRM`, `DANGEROUS`.
  - `enum.Enum` `Group` with members `IMAGE`, `COLOUR`, `AUDIO`, `POWER`, `INPUT`.
  - `dataclass(frozen=True) class VcpSpec`: fields `code: int`, `name: str`, `group: Group`, `minimum: int`, `maximum: int`, `writable: bool`, `safety: Safety`, `affects: tuple[str, ...]`.
  - `VCP_REGISTRY: dict[int, VcpSpec]` — keyed by VCP code.
  - `VCP_BY_NAME: dict[str, VcpSpec]` — keyed by lowercase underscore name.
  - `function registry_for(codes: Iterable[int]) -> list[VcpSpec]` — specs for the given codes, sorted by code, unknown codes skipped.
  - `function spec_for(code: int) -> VcpSpec | None`.
  - `function is_dangerous(code: int) -> bool`.
  - `function name_key(name: str) -> str` — the normalisation used for `VCP_BY_NAME` keys.

- [ ] **Step 1: Write the failing test**

`tests/model/test_vcp_registry.py`:

```python
import pytest

from lumen.model.vcp_registry import (
    Group,
    Safety,
    VCP_BY_NAME,
    VCP_REGISTRY,
    is_dangerous,
    name_key,
    registry_for,
    spec_for,
)


def test_brightness_is_registered_with_the_standard_range():
    spec = spec_for(0x10)
    assert spec is not None
    assert spec.name == "Brightness"
    assert spec.minimum == 0
    assert spec.maximum == 100
    assert spec.safety is Safety.SAFE
    assert spec.group is Group.IMAGE


@pytest.mark.parametrize(
    ("code", "expected_name"),
    [
        (0x10, "Brightness"),
        (0x12, "Contrast"),
        (0x16, "Video gain: Red"),
        (0x18, "Video gain: Green"),
        (0x1A, "Video gain: Blue"),
        (0x6C, "Video black level: Red"),
        (0x6E, "Video black level: Green"),
        (0x70, "Video black level: Blue"),
        (0x14, "Select colour preset"),
        (0x0C, "Colour temperature request"),
        (0x59, "Saturation: Red"),
        (0x5E, "Saturation: Magenta"),
        (0x9B, "Hue: Red"),
        (0xA0, "Hue: Magenta"),
        (0x62, "Audio speaker volume"),
    ],
)
def test_known_codes_have_expected_names(code: int, expected_name: str):
    spec = spec_for(code)
    assert spec is not None, f"code 0x{code:02X} must be registered"
    assert spec.name == expected_name


def test_restore_factory_defaults_is_dangerous():
    assert is_dangerous(0x04)


def test_screen_blanking_code_is_dangerous():
    """0x8D blanks the screen in MCCS 2.2 - it must never be a safe control."""
    assert is_dangerous(0x8D)


def test_power_and_dpms_codes_are_dangerous():
    for code in (0xD6, 0xE0, 0xE1, 0xE2, 0xE3, 0xE4, 0xE5, 0xE7, 0xE8):
        assert is_dangerous(code), f"0x{code:02X} must be classified dangerous"


def test_input_source_requires_confirmation():
    assert spec_for(0x60).safety is Safety.CONFIRM


def test_unknown_code_has_no_spec():
    assert spec_for(0xAB) is None


def test_registry_for_skips_unknown_codes_and_sorts_by_code():
    specs = registry_for([0x70, 0x10, 0xAB, 0x12])
    assert [s.code for s in specs] == [0x10, 0x12, 0x70]


def test_registry_for_empty_input_is_empty():
    assert registry_for([]) == []


def test_lookup_by_name_is_case_and_separator_insensitive():
    assert VCP_BY_NAME["brightness"].code == 0x10
    assert VCP_BY_NAME["video_gain_red"].code == 0x16
    assert VCP_BY_NAME["video gain red"].code == 0x16


def test_name_key_normalises_punctuation():
    assert name_key("Video gain: Red") == "video_gain_red"
    assert name_key("Brightness") == "brightness"


def test_every_registered_code_has_a_unique_derived_name():
    seen: dict[str, int] = {}
    for code, spec in VCP_REGISTRY.items():
        key = name_key(spec.name)
        assert key not in seen, f"name collision between 0x{code:02X} and 0x{seen[key]:02X}"
        seen[key] = code
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/lumen && python -m pytest tests/model/test_vcp_registry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lumen.model.vcp_registry'`

- [ ] **Step 3: Write minimal implementation**

`lumen/model/vcp_registry.py`:

```python
"""The DDC/CI VCP code registry.

Authored from the public VESA MCCS / DDC-CI specification. Each entry
records what a VCP code means, the range it operates over, whether it is
safe to expose, and which other calibration steps it interacts with.

`affects` records dependency edges the engine uses to warn that changing
one control changes the result of an earlier step. The edges follow the
hardware behaviour measured on the reference machine:

- brightness interacts with the black point, because raising brightness
  after setting a black level re-crushes the shadows
- the video gain (white) and black level (black) of a channel interact
  with each other
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable


class Safety(Enum):
    """How dangerous a control is to expose."""

    SAFE = "safe"
    CONFIRM = "confirm"
    DANGEROUS = "dangerous"


class Group(Enum):
    """UI grouping for a control."""

    IMAGE = "image"
    COLOUR = "colour"
    AUDIO = "audio"
    POWER = "power"
    INPUT = "input"


@dataclass(frozen=True)
class VcpSpec:
    """Static description of one VCP code."""

    code: int
    name: str
    group: Group
    minimum: int = 0
    maximum: int = 100
    writable: bool = True
    safety: Safety = Safety.SAFE
    affects: tuple[str, ...] = field(default_factory=tuple)


def _spec(
    code: int,
    name: str,
    group: Group,
    *,
    minimum: int = 0,
    maximum: int = 100,
    writable: bool = True,
    safety: Safety = Safety.SAFE,
    affects: tuple[str, ...] = (),
) -> VcpSpec:
    return VcpSpec(
        code=code,
        name=name,
        group=group,
        minimum=minimum,
        maximum=maximum,
        writable=writable,
        safety=safety,
        affects=affects,
    )


_SPECS: tuple[VcpSpec, ...] = (
    # --- image / luminance -------------------------------------------------
    _spec(0x10, "Brightness", Group.IMAGE, affects=("black_point",)),
    _spec(0x12, "Contrast", Group.IMAGE, affects=("white_point", "black_point")),
    # --- white point: per-channel video gain -------------------------------
    _spec(0x16, "Video gain: Red", Group.COLOUR, affects=("white_point",)),
    _spec(0x18, "Video gain: Green", Group.COLOUR, affects=("white_point",)),
    _spec(0x1A, "Video gain: Blue", Group.COLOUR, affects=("white_point",)),
    # --- black point: per-channel video black level ------------------------
    _spec(0x6C, "Video black level: Red", Group.COLOUR, affects=("black_point",)),
    _spec(0x6E, "Video black level: Green", Group.COLOUR, affects=("black_point",)),
    _spec(0x70, "Video black level: Blue", Group.COLOUR, affects=("black_point",)),
    # --- colour temperature -------------------------------------------------
    _spec(0x14, "Select colour preset", Group.COLOUR, maximum=13, affects=("white_point",)),
    _spec(0x0C, "Colour temperature request", Group.COLOUR, maximum=63, affects=("white_point",)),
    # --- saturation: 6 axes -------------------------------------------------
    _spec(0x59, "Saturation: Red", Group.COLOUR),
    _spec(0x5A, "Saturation: Yellow", Group.COLOUR),
    _spec(0x5B, "Saturation: Green", Group.COLOUR),
    _spec(0x5C, "Saturation: Cyan", Group.COLOUR),
    _spec(0x5D, "Saturation: Blue", Group.COLOUR),
    _spec(0x5E, "Saturation: Magenta", Group.COLOUR),
    # --- hue: 6 axes --------------------------------------------------------
    _spec(0x9B, "Hue: Red", Group.COLOUR),
    _spec(0x9C, "Hue: Yellow", Group.COLOUR),
    _spec(0x9D, "Hue: Green", Group.COLOUR),
    _spec(0x9E, "Hue: Cyan", Group.COLOUR),
    _spec(0x9F, "Hue: Blue", Group.COLOUR),
    _spec(0xA0, "Hue: Magenta", Group.COLOUR),
    # --- audio --------------------------------------------------------------
    _spec(0x62, "Audio speaker volume", Group.AUDIO),
    # --- input: switching can blank the screen ------------------------------
    _spec(0x60, "Input source", Group.INPUT, maximum=0x11, safety=Safety.CONFIRM),
    # --- dangerous: blank, power off, or destroy settings -------------------
    _spec(0x04, "Restore factory defaults", Group.POWER, maximum=1, safety=Safety.DANGEROUS),
    _spec(0x8D, "Audio mute and screen blank", Group.POWER, maximum=2, safety=Safety.DANGEROUS),
    _spec(0xD6, "Power mode", Group.POWER, maximum=5, safety=Safety.DANGEROUS),
    _spec(0xE0, "Display scaling mode", Group.POWER, maximum=6, safety=Safety.DANGEROUS),
    _spec(0xE1, "Power indicator", Group.POWER, maximum=2, safety=Safety.DANGEROUS),
    _spec(0xE2, "Power mode (extended)", Group.POWER, maximum=27, safety=Safety.DANGEROUS),
    _spec(0xE3, "Display type", Group.POWER, maximum=4095, safety=Safety.DANGEROUS),
    _spec(0xE4, "Display control", Group.POWER, maximum=3, safety=Safety.DANGEROUS),
    _spec(0xE5, "Display scaling", Group.POWER, maximum=10, safety=Safety.DANGEROUS),
    _spec(0xE7, "Display scaling (mode)", Group.POWER, maximum=3, safety=Safety.DANGEROUS),
    _spec(0xE8, "Display scaling (aspect)", Group.POWER, maximum=4, safety=Safety.DANGEROUS),
)

VCP_REGISTRY: dict[int, VcpSpec] = {spec.code: spec for spec in _SPECS}


def name_key(name: str) -> str:
    """Normalise a display name for lookup: lower case, underscores, no colons."""
    return name.lower().replace(" ", "_").replace(":", "")


class _NameLookup(dict):
    """A lookup that ignores case and separator differences in the query."""

    def __missing__(self, key: str) -> VcpSpec:
        normalised = name_key(key)
        if normalised in self:
            return self[normalised]
        raise KeyError(key)

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default


VCP_BY_NAME: dict[str, VcpSpec] = _NameLookup(
    {name_key(spec.name): spec for spec in _SPECS}
)


def spec_by_name(name: str) -> VcpSpec | None:
    """Look a control up by name, ignoring case and separators."""
    return VCP_BY_NAME.get(name_key(name))


def spec_for(code: int) -> VcpSpec | None:
    """Return the spec for a VCP code, or None if the code is unknown."""
    return VCP_REGISTRY.get(code)


def registry_for(codes: Iterable[int]) -> list[VcpSpec]:
    """Return specs for the given codes, sorted by code, unknown codes skipped."""
    return [VCP_REGISTRY[code] for code in sorted(set(codes)) if code in VCP_REGISTRY]


def is_dangerous(code: int) -> bool:
    """True if the code must never be exposed through the normal UI path."""
    spec = VCP_REGISTRY.get(code)
    return spec is not None and spec.safety is Safety.DANGEROUS
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/lumen && python -m pytest tests/model/test_vcp_registry.py -v`
Expected: PASS (22 tests)

- [ ] **Step 5: Run the full suite to check nothing regressed**

Run: `cd ~/lumen && python -m pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd ~/lumen && git add lumen/model tests/model
git commit -m "feat: VCP registry with safety classes and dependency edges"
```

---

### Task 3: Capability parsing and the monitor model

**Files:**
- Create: `lumen/model/monitor.py`
- Create: `lumen/model/capability.py`
- Create: `tests/model/test_capability.py`
- Create: `tests/model/test_monitor.py`

**Interfaces:**
- Consumes: `lumen.model.vcp_registry.{VcpSpec, Safety, registry_for}`.
- Produces:
  - `dataclass(frozen=True) class VcpValue`: fields `code: int`, `value: int`, `maximum: int`, `supported: bool = True`.
  - `dataclass(frozen=True) class Capabilities`: fields `vcp_codes: frozenset[int]`, `vcp_values: dict[int, tuple[int, ...]]`, `commands: frozenset[int]`, `mccs_version: str | None`, `raw: str`.
  - `dataclass(frozen=True) class MonitorDescriptor`: fields `identifier: str`, `manufacturer: str`, `model: str`, `serial: str`, `pnp_id: str | None`, `edid_hash: str | None`, `connection: str`, `backend_name: str`.
  - `function parse_capabilities(raw: str) -> Capabilities`.
  - `function capabilities_from_vcp_list(codes: Iterable[int]) -> Capabilities`.
  - `function supported_specs(capabilities: Capabilities) -> list[VcpSpec]` — registry specs for advertised codes, excluding dangerous ones.

- [ ] **Step 1: Write the failing test**

`tests/model/test_capability.py`:

```python
from lumen.model.capability import parse_capabilities

REFERENCE = (
    "cmds(01 02 03 07 0C E3 F3)"
    "vcp(04 10 12 14(05 06 08 0B) 16 18 1A 59 5A 5B 5C 5D 5E 60(01 0F 11) 62"
    "    6C 6E 70 8D 9B 9C 9D 9E 9F A0 D6 E0(00 04 05) E1(00 01 02)"
    "    E2(00 01 02 03 04 05 06 07 10 11 12) E3 E4 E5 E7(00 01 02)"
    "    E8(00 01 02 03 04))"
    "mccs_ver(2.2) mswhql(1) asset_eep(40)"
)


def test_parses_the_reference_monitor_capability_string():
    caps = parse_capabilities(REFERENCE)
    assert 0x10 in caps.vcp_codes
    assert 0x12 in caps.vcp_codes
    assert 0x6C in caps.vcp_codes
    assert 0x70 in caps.vcp_codes
    assert 0x04 in caps.vcp_codes
    assert caps.mccs_version == "2.2"


def test_parses_enumerated_values_for_a_code():
    caps = parse_capabilities(REFERENCE)
    assert caps.vcp_values[0x14] == (0x05, 0x06, 0x08, 0x0B)
    assert caps.vcp_values[0x60] == (0x01, 0x0F, 0x11)
    assert caps.vcp_values[0xE1] == (0x00, 0x01, 0x02)


def test_codes_without_enumerated_values_have_no_value_entry():
    caps = parse_capabilities(REFERENCE)
    assert 0x10 not in caps.vcp_values


def test_parses_command_codes():
    caps = parse_capabilities(REFERENCE)
    assert 0x0C in caps.commands
    assert 0xE3 in caps.commands


def test_empty_string_yields_no_codes():
    caps = parse_capabilities("")
    assert caps.vcp_codes == frozenset()
    assert caps.vcp_values == {}


def test_capability_string_without_vcp_section_is_tolerated():
    caps = parse_capabilities("mccs_ver(2.0)")
    assert caps.vcp_codes == frozenset()
    assert caps.mccs_version == "2.0"


def test_lowercase_hex_codes_are_parsed():
    caps = parse_capabilities("vcp(10 12)")
    assert caps.vcp_codes == frozenset({0x10, 0x12})


def test_a_code_with_a_parenthesised_value_list_keeps_its_values():
    """The vcp section nests parentheses, so the parser must not split it."""
    caps = parse_capabilities("vcp(10 12 14(05 06 08 0B) 16 18 1A)")
    assert caps.vcp_codes == frozenset({0x10, 0x12, 0x14, 0x16, 0x18, 0x1A})
    assert caps.vcp_values[0x14] == (0x05, 0x06, 0x08, 0x0B)
    assert 0x10 not in caps.vcp_values


def test_every_readable_control_of_the_reference_monitor_is_found():
    caps = parse_capabilities(REFERENCE)
    for code in (0x10, 0x12, 0x16, 0x18, 0x1A, 0x6C, 0x6E, 0x70):
        assert code in caps.vcp_codes
```

`tests/model/test_monitor.py`:

```python
from lumen.model.capability import parse_capabilities, supported_specs
from lumen.model.monitor import Capabilities, MonitorDescriptor

REFERENCE = (
    "cmds(01 02 03)vcp(04 10 12 16 18 1A 6C 6E 70 14(05 0B) 60(01 0F 11))"
    "mccs_ver(2.2)"
)


def test_supported_specs_excludes_dangerous_codes():
    caps = parse_capabilities(REFERENCE)
    codes = [spec.code for spec in supported_specs(caps)]
    assert 0x10 in codes
    assert 0x6C in codes
    assert 0x04 not in codes, "dangerous codes must never be offered"


def test_supported_specs_are_sorted_by_code():
    caps = parse_capabilities(REFERENCE)
    codes = [spec.code for spec in supported_specs(caps)]
    assert codes == sorted(codes)


def test_monitor_descriptor_is_hashable_and_comparable():
    a = MonitorDescriptor(
        identifier="dev:/dev/i2c-4",
        manufacturer="Acer",
        model="VG240Y",
        serial="0x01122d3a",
        pnp_id="ACR0673",
        edid_hash="abc123",
        connection="HDMI",
        backend_name="ddccontrol",
    )
    b = MonitorDescriptor(**a.__dict__)
    assert a == b
    assert len({a, b}) == 1


def test_capabilities_with_no_codes_produce_no_specs():
    assert supported_specs(Capabilities(vcp_codes=frozenset())) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/lumen && python -m pytest tests/model/test_capability.py tests/model/test_monitor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lumen.model.capability'`

- [ ] **Step 3: Write minimal implementation**

`lumen/model/monitor.py`:

```python
"""Data describing a physical display and its capabilities."""

from __future__ import annotations

from dataclasses import dataclass, field

from .vcp_registry import VcpSpec


@dataclass(frozen=True)
class VcpValue:
    """A single VCP reading."""

    code: int
    value: int
    maximum: int
    supported: bool = True


@dataclass(frozen=True)
class Capabilities:
    """What a monitor advertises about itself."""

    vcp_codes: frozenset[int] = frozenset()
    vcp_values: dict[int, tuple[int, ...]] = field(default_factory=dict)
    commands: frozenset[int] = frozenset()
    mccs_version: str | None = None
    raw: str = ""


@dataclass(frozen=True)
class MonitorDescriptor:
    """Identity of a display, enough to tell two monitors apart."""

    identifier: str
    manufacturer: str = ""
    model: str = ""
    serial: str = ""
    pnp_id: str | None = None
    edid_hash: str | None = None
    connection: str = ""
    backend_name: str = ""
```

`lumen/model/capability.py`:

```python
"""Parsing of MCCS / DDC-CI capability strings.

A monitor's capability string looks like:

    cmds(01 02 03 07 0C E3 F3)
    vcp(04 10 12 14(05 06 08 0B) 16 18 1A ... E0(00 04 05) ...)
    mccs_ver(2.2) mswhql(1) asset_eep(40)

The `vcp(...)` section lists the supported VCP codes. A code may carry a
parenthesised list of its supported values, which is how enumerated
controls (colour presets, input sources) declare their options.
"""

from __future__ import annotations

import re
from typing import Iterable

from .monitor import Capabilities
from .vcp_registry import Safety, VcpSpec, registry_for

def _parse_codes(text: str) -> tuple[set[int], dict[int, tuple[int, ...]]]:
    """Parse a code list, honouring nested parentheses.

    A capability section looks like `10 12 14(05 06 08 0B) 16 18 1A`, so a
    code may carry a parenthesised list of its own values. Splitting on
    whitespace alone would break those values apart, so the text is
    scanned position by position.
    """
    codes: set[int] = set()
    values: dict[int, tuple[int, ...]] = {}

    length = len(text)
    index = 0
    while index < length:
        if text[index].isspace():
            index += 1
            continue

        # Read the code itself.
        start = index
        while index < length and not text[index].isspace() and text[index] != "(":
            index += 1
        head = text[start:index].strip()
        if not head:
            index += 1
            continue
        try:
            code = int(head, 16)
        except ValueError:
            continue
        codes.add(code)

        # Look past the whitespace for an opening parenthesis.
        probe = index
        while probe < length and text[probe].isspace():
            probe += 1
        if probe >= length or text[probe] != "(":
            continue

        # Find the matching closing parenthesis, counting nesting.
        depth = 0
        cursor = probe
        while cursor < length:
            if text[cursor] == "(":
                depth += 1
            elif text[cursor] == ")":
                depth -= 1
                if depth == 0:
                    break
            cursor += 1
        if cursor >= length:
            break

        enumerated: list[int] = []
        for part in text[probe + 1 : cursor].split():
            try:
                enumerated.append(int(part, 16))
            except ValueError:
                continue
        if enumerated:
            values[code] = tuple(enumerated)
        index = cursor + 1

    return codes, values


def _sections(raw: str) -> list[tuple[str, str]]:
    """Split a capability string into its named sections.

    The `vcp(...)` section contains nested parentheses, so the sections are
    found by scanning rather than by matching a flat regular expression.
    """
    sections: list[tuple[str, str]] = []
    index = 0
    length = len(raw)
    while index < length:
        match = _SECTION_START.search(raw, index)
        if match is None:
            break
        name = match.group(1)
        depth = 1
        cursor = match.end()
        while cursor < length and depth:
            if raw[cursor] == "(":
                depth += 1
            elif raw[cursor] == ")":
                depth -= 1
            cursor += 1
        if depth == 0:
            sections.append((name, raw[match.end() : cursor - 1]))
            index = cursor
        else:
            break
    return sections


def parse_capabilities(raw: str) -> Capabilities:
    """Parse an MCCS capability string into a `Capabilities`."""
    codes: set[int] = set()
    values: dict[int, tuple[int, ...]] = {}
    commands: set[int] = set()
    mccs_version: str | None = None

    for name, body in _sections(raw or ""):
        name = name.lower()
        if name == "vcp":
            section_codes, section_values = _parse_codes(body)
            codes |= section_codes
            values.update(section_values)
        elif name == "cmds":
            section_codes, _ = _parse_codes(body)
            commands |= section_codes
        elif name == "mccs_ver":
            mccs_version = body.strip() or None

    return Capabilities(
        vcp_codes=frozenset(codes),
        vcp_values=values,
        commands=frozenset(commands),
        mccs_version=mccs_version,
        raw=raw or "",
    )


def capabilities_from_vcp_list(codes: Iterable[int]) -> Capabilities:
    """Build capabilities for a backend that can only report which codes exist."""
    return Capabilities(vcp_codes=frozenset(int(code) for code in codes))


def supported_specs(capabilities: Capabilities) -> list[VcpSpec]:
    """Registry specs for the codes a monitor advertises.

    Dangerous codes are excluded: they can blank the screen, power the
    monitor off or destroy the user's settings, so they never appear in
    the normal control list.
    """
    specs = registry_for(capabilities.vcp_codes)
    return [spec for spec in specs if spec.safety is not Safety.DANGEROUS]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/lumen && python -m pytest tests/model/test_capability.py tests/model/test_monitor.py -v`
Expected: PASS (12 tests)

- [ ] **Step 5: Run the full suite**

Run: `cd ~/lumen && python -m pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd ~/lumen && git add lumen/model tests/model
git commit -m "feat: MCCS capability parsing and monitor descriptors"
```

---

### Task 4: The DDC backend protocol and the ddccontrol D-Bus implementation

**Files:**
- Create: `lumen/backends/base.py`
- Create: `lumen/backends/ddc/ddccontrol_dbus.py`
- Create: `lumen/backends/fakes.py`
- Create: `tests/backends/test_ddccontrol_dbus.py`
- Create: `tests/backends/test_fake_ddc.py`

**Interfaces:**
- Consumes: `lumen.model.monitor.{MonitorDescriptor, Capabilities, VcpValue}`, `lumen.model.capability.parse_capabilities`.
- Produces:
  - `class DdcError(Exception)` — a DDC/CI operation failed.
  - `class GammaError(Exception)` — a software gamma operation failed.
  - `class DdcBackend(Protocol)` with `list_monitors() -> list[MonitorDescriptor]`, `capabilities(monitor) -> Capabilities`, `get(monitor, code) -> VcpValue`, `set(monitor, code, value) -> None`.
  - `class GammaBackend(Protocol)` with `list_outputs() -> list[str]`, `read_ramp(output) -> tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]]`, `write_ramp(output, red, green, blue) -> None`.
  - `class DdcControlDBusBackend`: constructor `DdcControlDBusBackend(bus=None, service="ddccontrol.DDCControl", path="/ddccontrol/DDCControl")`. Imports `dbus` lazily inside `__init__` so the module imports without dbus-python installed.
  - `class FakeDdcBackend`: constructor `FakeDdcBackend(monitors=None, capabilities=None, values=None, fail_on=None)`.
  - `class FakeGammaBackend`: constructor `FakeGammaBackend(size=256, outputs=("fake-0",))`.

- [ ] **Step 1: Write the failing test**

`tests/backends/test_fake_ddc.py`:

```python
import pytest

from lumen.backends.base import DdcError
from lumen.backends.fakes import FakeDdcBackend
from lumen.model.monitor import MonitorDescriptor

MONITOR = MonitorDescriptor(identifier="fake:0", manufacturer="Acer", model="VG240Y")


def make_backend(**kwargs):
    return FakeDdcBackend(monitors=[MONITOR], **kwargs)


def test_lists_the_monitors_it_was_given():
    backend = make_backend()
    assert [m.identifier for m in backend.list_monitors()] == ["fake:0"]


def test_get_returns_the_stored_value_and_maximum():
    backend = make_backend(values={0x10: (40, 100)})
    result = backend.get(MONITOR, 0x10)
    assert result.value == 40
    assert result.maximum == 100
    assert result.supported is True


def test_get_of_unknown_code_is_unsupported_not_an_error():
    backend = make_backend()
    result = backend.get(MONITOR, 0xAB)
    assert result.supported is False


def test_set_updates_the_stored_value():
    backend = make_backend(values={0x10: (40, 100)})
    backend.set(MONITOR, 0x10, 60)
    assert backend.get(MONITOR, 0x10).value == 60


def test_set_raises_when_the_monitor_rejects_the_write():
    backend = make_backend(values={0x10: (40, 100)}, fail_on={0x10})
    with pytest.raises(DdcError):
        backend.set(MONITOR, 0x10, 60)
    assert backend.get(MONITOR, 0x10).value == 40, "a failed write must not change the value"


def test_set_of_a_value_outside_range_is_rejected():
    backend = make_backend(values={0x10: (40, 100)})
    with pytest.raises(DdcError):
        backend.set(MONITOR, 0x10, 500)
    with pytest.raises(DdcError):
        backend.set(MONITOR, 0x10, -1)


def test_set_honours_a_maximum_below_the_registry_assumption():
    """A monitor may report brightness maximum 50, not 100."""
    backend = make_backend(values={0x10: (10, 50)})
    backend.set(MONITOR, 0x10, 50)
    assert backend.get(MONITOR, 0x10).value == 50
    with pytest.raises(DdcError):
        backend.set(MONITOR, 0x10, 51)


def test_capabilities_reflect_the_configured_codes():
    backend = FakeDdcBackend(monitors=[MONITOR], capabilities={0x10: (), 0x12: ()})
    caps = backend.capabilities(MONITOR)
    assert 0x10 in caps.vcp_codes
    assert 0x12 in caps.vcp_codes
    assert 0x16 not in caps.vcp_codes
```

`tests/backends/test_ddccontrol_dbus.py`:

```python
"""The ddccontrol D-Bus backend, exercised against a fake bus.

The real service is exercised by the integration test (Task 12). These
tests pin the request and response handling, including the read-back
verification the spec requires.
"""

import pytest

from lumen.backends.base import DdcError
from lumen.backends.ddc.ddccontrol_dbus import DdcControlDBusBackend


class FakeBus:
    """Minimal stand-in for dbus.SystemBus()."""

    def __init__(self, values, capabilities="vcp(10 12 16) mccs_ver(2.2)"):
        self.values = values
        self.capabilities = capabilities
        self.written = []

    def get_object(self, service, path):
        return self

    def _dispatch(self, method, args):
        if method == "GetMonitors":
            # The real service returns arrays of one-byte structs.
            return (["dev:/dev/i2c-4"], [(1,)], ["Test Monitor"], [(0x80,)])
        if method == "OpenMonitor":
            return ("ACR0673", self.capabilities)
        if method == "GetControl":
            device, code = args
            if code not in self.values:
                # The real service reports result 0 for a code the
                # monitor does not support.
                return (0, 0, 0)
            value, maximum = self.values[code]
            return (1, value, maximum)
        if method == "SetControl":
            device, code, value = args
            self.written.append((code, value))
            self.values[code] = (value, self.values.get(code, (0, 100))[1])
            return None
        raise AssertionError(f"unexpected method {method}")


class FakeInterface:
    def __init__(self, bus):
        self.bus = bus

    def __getattr__(self, name):
        def call(*args, **kwargs):
            return self.bus._dispatch(name, args)

        return call


def make_backend(values=None, bus=None):
    bus = bus or FakeBus(values or {0x10: (40, 100), 0x12: (50, 100)})
    backend = DdcControlDBusBackend(bus=bus)
    backend._interface = FakeInterface(bus)
    return backend, bus


def test_lists_monitors_from_get_monitors():
    backend, _ = make_backend()
    monitors = backend.list_monitors()
    assert len(monitors) == 1
    assert monitors[0].identifier == "dev:/dev/i2c-4"
    assert monitors[0].backend_name == "ddccontrol"


def test_capabilities_are_parsed_from_open_monitor():
    backend, _ = make_backend()
    caps = backend.capabilities(backend.list_monitors()[0])
    assert 0x10 in caps.vcp_codes
    assert 0x12 in caps.vcp_codes


def test_get_returns_value_and_maximum():
    backend, _ = make_backend()
    result = backend.get(backend.list_monitors()[0], 0x10)
    assert result.value == 40
    assert result.maximum == 100


def test_get_of_an_unsupported_code_is_marked_unsupported():
    backend, _ = make_backend()
    result = backend.get(backend.list_monitors()[0], 0x50)
    assert result.supported is False


def test_set_writes_and_verifies():
    backend, bus = make_backend()
    backend.set(backend.list_monitors()[0], 0x10, 55)
    assert bus.written == [(0x10, 55)]


def test_set_raises_when_the_value_does_not_take():
    class RejectingBus(FakeBus):
        def _dispatch(self, method, args):
            if method == "SetControl":
                self.written.append((args[1], args[2]))
                return None  # the value does not change
            return super()._dispatch(method, args)

    bus = RejectingBus({0x10: (40, 100)})
    backend, _ = make_backend(bus=bus)
    monitor = backend.list_monitors()[0]
    with pytest.raises(DdcError):
        backend.set(monitor, 0x10, 55)
    assert bus.values[0x10][0] == 40


def test_set_rejects_a_value_above_the_reported_maximum():
    backend, _ = make_backend()
    monitor = backend.list_monitors()[0]
    with pytest.raises(DdcError):
        backend.set(monitor, 0x10, 500)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/lumen && python -m pytest tests/backends -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lumen.backends.base'`

- [ ] **Step 3: Write minimal implementation**

`lumen/backends/base.py`:

```python
"""Backend protocols.

This is the boundary between the portable core and real hardware. Every
platform-specific adapter implements these protocols, so the engine can
be tested against fakes and ported by writing new adapters.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from lumen.model.monitor import Capabilities, MonitorDescriptor, VcpValue


class DdcError(Exception):
    """A DDC/CI operation failed.

    Raised rather than swallowed: a calibration tool that silently fails
    is worse than one that admits it.
    """


class GammaError(Exception):
    """A software gamma operation failed."""


@runtime_checkable
class DdcBackend(Protocol):
    """Hardware control over DDC/CI."""

    def list_monitors(self) -> list[MonitorDescriptor]:
        """Every monitor this backend can reach."""

    def capabilities(self, monitor: MonitorDescriptor) -> Capabilities:
        """What the monitor advertises about itself."""

    def get(self, monitor: MonitorDescriptor, code: int) -> VcpValue:
        """Read one VCP."""

    def set(self, monitor: MonitorDescriptor, code: int, value: int) -> None:
        """Write one VCP.

        Must verify the write took effect and raise `DdcError` if it did
        not. Never leave a failed write looking like a success.
        """


@runtime_checkable
class GammaBackend(Protocol):
    """Software control over a display's gamma ramp."""

    def list_outputs(self) -> list[str]:
        """Names of the outputs whose gamma ramp can be controlled."""

    def read_ramp(
        self, output: str
    ) -> tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]]:
        """The red, green and blue ramps, each a tuple of 16-bit values."""

    def write_ramp(
        self,
        output: str,
        red: tuple[int, ...],
        green: tuple[int, ...],
        blue: tuple[int, ...],
    ) -> None:
        """Write the ramp.

        Must flush the request and verify the result, raising
        `GammaError` if the ramp did not change.
        """
```

`lumen/backends/fakes.py`:

```python
"""In-memory backends used by the test suite.

The engine is developed against these. They are how the core stays
testable without hardware, and how future platform ports are developed
on machines that do not have the target hardware attached.
"""

from __future__ import annotations

from lumen.backends.base import DdcError, GammaError
from lumen.model.monitor import Capabilities, MonitorDescriptor, VcpValue


class FakeDdcBackend:
    """A DDC backend backed by a dictionary."""

    def __init__(
        self,
        monitors: list[MonitorDescriptor] | None = None,
        capabilities: dict[int, tuple[int, ...]] | None = None,
        values: dict[int, tuple[int, int]] | None = None,
        fail_on: set[int] | None = None,
    ) -> None:
        self._monitors = list(monitors or [])
        self._capabilities = dict(capabilities or {})
        self._values = dict(values or {})
        self._fail_on = set(fail_on or ())

    def list_monitors(self) -> list[MonitorDescriptor]:
        return list(self._monitors)

    def capabilities(self, monitor: MonitorDescriptor) -> Capabilities:
        codes = self._capabilities.get(monitor.identifier, self._capabilities.get(0, ()))
        return Capabilities(vcp_codes=frozenset(int(c) for c in codes))

    def get(self, monitor: MonitorDescriptor, code: int) -> VcpValue:
        if code not in self._values:
            return VcpValue(code=code, value=0, maximum=0, supported=False)
        value, maximum = self._values[code]
        return VcpValue(code=code, value=value, maximum=maximum, supported=True)

    def set(self, monitor: MonitorDescriptor, code: int, value: int) -> None:
        if code in self._fail_on:
            raise DdcError(f"monitor rejected the write to 0x{code:02X}")
        current = self._values.get(code)
        maximum = current[1] if current else 100
        if value < 0 or value > maximum:
            raise DdcError(
                f"value {value} is outside the range 0-{maximum} of 0x{code:02X}"
            )
        self._values[code] = (value, maximum)


class FakeGammaBackend:
    """A gamma backend that stores ramps in memory."""

    def __init__(self, size: int = 256, outputs: tuple[str, ...] = ("fake-0",)) -> None:
        self.size = size
        self._outputs = list(outputs)
        self._ramps: dict[
            str, tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]]
        ] = {}
        for name in self._outputs:
            linear = tuple(i * 65535 // (size - 1) for i in range(size))
            self._ramps[name] = (linear, linear, linear)
        self.fail_on: set[str] = set()

    def list_outputs(self) -> list[str]:
        return list(self._outputs)

    def read_ramp(self, output: str):
        return self._ramps[output]

    def write_ramp(self, output: str, red, green, blue) -> None:
        if output in self.fail_on:
            raise GammaError(f"gamma write rejected for {output}")
        self._ramps[output] = (tuple(red), tuple(green), tuple(blue))
```

`lumen/backends/ddc/ddccontrol_dbus.py`:

```python
"""DDC/CI over the `ddccontrol` D-Bus service.

The service runs as root, and its system bus policy allows any user to
call it. That means Lumen needs no sudo, no udev rule and no group
membership - which is the reason this backend is the primary one on
Linux.

The `dbus` module is imported lazily so that this file can be imported
on a system without dbus-python installed.
"""

from __future__ import annotations

import time

from lumen.backends.base import DdcError
from lumen.model.capability import parse_capabilities
from lumen.model.monitor import Capabilities, MonitorDescriptor, VcpValue
from lumen.model.vcp_registry import spec_for

SERVICE = "ddccontrol.DDCControl"
PATH = "/ddccontrol/DDCControl"

# How many times to retry a write the monitor did not accept.
_WRITE_RETRIES = 3
_RETRY_DELAY_SECONDS = 0.25


def _first_byte(array, index: int) -> int:
    """Read the first byte of an element of a `a(y)` reply."""
    try:
        entry = array[index]
    except (IndexError, TypeError):
        return 0
    if isinstance(entry, (bytes, bytearray)):
        return entry[0] if entry else 0
    if isinstance(entry, (tuple, list)):
        return int(entry[0]) if entry else 0
    return int(entry)


class DdcControlDBusBackend:
    """Talks to `ddccontrol.DDCControl` over the system bus."""

    def __init__(self, bus=None, service: str = SERVICE, path: str = PATH) -> None:
        if bus is None:
            try:
                import dbus  # optional dependency, imported lazily
            except ImportError as exc:
                raise DdcError(
                    "dbus-python is required for the ddccontrol backend; "
                    "install python3-dbus or use another backend"
                ) from exc
            bus = dbus.SystemBus()
        self._bus = bus
        self._service = service
        self._path = path
        self._object = bus.get_object(service, path)
        # The interface must be named explicitly. A bare proxy looks the
        # method up by introspecting the object, and this service does not
        # answer introspection at that path.
        try:
            import dbus

            self._interface = dbus.Interface(self._object, service)
        except ImportError:  # pragma: no cover - only for fake buses
            self._interface = self._object
        self._capability_cache: dict[str, Capabilities] = {}
        self._identifiers: dict[str, str] = {}

    def _call(self, method: str, *args):
        return getattr(self._interface, method)(*args)

    def list_monitors(self) -> list[MonitorDescriptor]:
        devices, supported, names, digital = self._call("GetMonitors")
        monitors: list[MonitorDescriptor] = []
        for index, device in enumerate(devices):
            # The supported and digital arrays are arrays of one-byte
            # structs, so each entry needs unwrapping.
            is_supported = bool(_first_byte(supported, index))
            is_digital = bool(_first_byte(digital, index))
            name = names[index] if index < len(names) else ""
            monitors.append(
                MonitorDescriptor(
                    identifier=str(device),
                    model=str(name),
                    connection="digital" if is_digital else "analog",
                    backend_name="ddccontrol",
                )
            )
        return monitors

    def capabilities(self, monitor: MonitorDescriptor) -> Capabilities:
        cached = self._capability_cache.get(monitor.identifier)
        if cached is not None:
            return cached
        try:
            pnp_id, raw = self._call("OpenMonitor", monitor.identifier)
        except Exception as exc:
            raise DdcError(
                f"could not read capabilities from {monitor.identifier}: {exc}"
            ) from exc
        caps = parse_capabilities(str(raw))
        self._capability_cache[monitor.identifier] = caps
        # The PNP id identifies the panel: the first three characters are
        # the manufacturer, the rest the model code.
        pnp = str(pnp_id)
        if pnp and monitor.pnp_id is None:
            self._identifiers[monitor.identifier] = pnp
        return caps

    def describe(self, monitor: MonitorDescriptor) -> MonitorDescriptor:
        """A descriptor enriched with the monitor's PNP identity.

        The D-Bus service reports the monitor as a generic name, so the
        PNP id is used to recover the real manufacturer and model.
        """
        pnp = self._identifiers.get(monitor.identifier)
        if not pnp:
            self.capabilities(monitor)
            pnp = self._identifiers.get(monitor.identifier)
        if not pnp:
            return monitor
        manufacturer = pnp[:3].title() if len(pnp) > 3 else ""
        model = pnp[3:] if len(pnp) > 3 else pnp
        return MonitorDescriptor(
            identifier=monitor.identifier,
            manufacturer=manufacturer,
            model=model or monitor.model,
            serial=monitor.serial,
            pnp_id=pnp,
            edid_hash=monitor.edid_hash,
            connection=monitor.connection,
            backend_name=monitor.backend_name,
        )

    def get(self, monitor: MonitorDescriptor, code: int) -> VcpValue:
        try:
            result, value, maximum = self._call("GetControl", monitor.identifier, code)
        except Exception as exc:
            raise DdcError(
                f"could not read 0x{code:02X} from {monitor.identifier}: {exc}"
            ) from exc
        return VcpValue(
            code=code,
            value=int(value),
            maximum=int(maximum),
            supported=int(result) != 0,
        )

    def set(self, monitor: MonitorDescriptor, code: int, value: int) -> None:
        # Refuse an out-of-range value before sending it: some monitors
        # accept a value beyond their reported maximum and then behave
        # unpredictably. The minimum comes from the registry, because the
        # monitor only reports a maximum.
        reading = self.get(monitor, code)
        if reading.maximum:
            spec = spec_for(code)
            minimum = spec.minimum if spec is not None else 0
            if not (minimum <= value <= reading.maximum):
                raise DdcError(
                    f"0x{code:02X} must be between {minimum} and "
                    f"{reading.maximum}, not {value}"
                )

        last_error: Exception | None = None
        for _attempt in range(_WRITE_RETRIES):
            try:
                self._call("SetControl", monitor.identifier, code, int(value))
            except Exception as exc:
                last_error = exc
            else:
                time.sleep(_RETRY_DELAY_SECONDS)
                confirmed = self.get(monitor, code)
                if confirmed.value == int(value):
                    return
                last_error = DdcError(
                    f"monitor did not accept 0x{code:02X}={value} "
                    f"(read back {confirmed.value})"
                )
            time.sleep(_RETRY_DELAY_SECONDS)
        raise DdcError(
            f"could not set 0x{code:02X}={value} on {monitor.identifier}: {last_error}"
        )

```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/lumen && python -m pytest tests/backends -v`
Expected: PASS (16 tests)

- [ ] **Step 5: Run the full suite**

Run: `cd ~/lumen && python -m pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd ~/lumen && git add lumen/backends tests/backends
git commit -m "feat: DDC backend protocol with read-back verification"
```

---

### Task 5: The X11 gamma backend

**Files:**
- Create: `lumen/backends/gamma/xrandr.py`
- Create: `tests/backends/test_xrandr_gamma.py`

**Interfaces:**
- Consumes: `lumen.backends.base.GammaError`.
- Produces:
  - `class GammaUnavailable(Exception)` — no usable X display, or no RandR gamma support.
  - `class XRandRGammaBackend`: constructor `XRandRGammaBackend(display_name: str | None = None)`.
    - `list_outputs() -> list[str]`
    - `read_ramp(output) -> tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]]`
    - `write_ramp(output, red, green, blue) -> None` — flushes, syncs and verifies.

- [ ] **Step 1: Write the failing test**

`tests/backends/test_xrandr_gamma.py`:

```python
"""The XRandR gamma backend.

The critical behaviour pinned here is the flush-and-verify rule from the
spec (section 2.4.1): XRRSetCrtcGamma is an asynchronous request, and a
write that is not flushed is silently discarded. The backend must never
report success for a write that did not land.
"""

import pytest

from lumen.backends.base import GammaError
from lumen.backends.gamma.xrandr import GammaUnavailable, XRandRGammaBackend


class FakeXRandr:
    """Stands in for the RandR calls python-xlib makes."""

    def __init__(self, size=1024, accept=True):
        self.size = size
        self.accept = accept
        self.ramp = tuple(i * 65535 // (size - 1) for i in range(size))
        self.set_calls = 0
        self.flushed = 0
        self.synced = 0

    def xrandr_get_crtc_gamma_size(self, crtc):
        return self.size

    def xrandr_get_crtc_gamma(self, crtc):
        return _Ramp(self.ramp, self.ramp, self.ramp)

    def xrandr_set_crtc_gamma(self, crtc, size, red, green, blue):
        self.set_calls += 1
        if self.accept:
            self.ramp = tuple(red)

    def flush(self):
        self.flushed += 1

    def sync(self):
        self.synced += 1


class _Ramp:
    def __init__(self, red, green, blue):
        self.red = red
        self.green = green
        self.blue = blue


class FakeDisplay:
    def __init__(self, xrandr):
        self.xrandr_get_crtc_gamma_size = xrandr.xrandr_get_crtc_gamma_size
        self.xrandr_get_crtc_gamma = xrandr.xrandr_get_crtc_gamma
        self.xrandr_set_crtc_gamma = xrandr.xrandr_set_crtc_gamma
        self._xrandr = xrandr
        self.crtcs = {"HDMI-A-0": 78}

    def flush(self):
        self._xrandr.flush()

    def sync(self):
        self._xrandr.sync()


def make_backend(accept=True):
    xr = FakeXRandr(accept=accept)
    backend = XRandRGammaBackend.__new__(XRandRGammaBackend)
    backend._display = FakeDisplay(xr)
    backend._crtcs = {"HDMI-A-0": 78}
    backend._size = 1024
    backend._refresh_crtcs = lambda: None
    return backend, xr


def test_lists_outputs():
    backend, _ = make_backend()
    assert backend.list_outputs() == ["HDMI-A-0"]


def test_read_ramp_returns_three_channels():
    backend, _ = make_backend()
    red, green, blue = backend.read_ramp("HDMI-A-0")
    assert len(red) == 1024
    assert len(green) == 1024
    assert len(blue) == 1024


def test_write_ramp_flushes_and_syncs():
    """Without the flush the request never reaches the server and is lost."""
    backend, xr = make_backend()
    ramp = tuple(i * 65535 // 1023 for i in range(1024))
    backend.write_ramp("HDMI-A-0", ramp, ramp, ramp)
    assert xr.set_calls == 1
    assert xr.flushed >= 1
    assert xr.synced >= 1


def test_write_ramp_raises_when_the_ramp_did_not_change():
    """The server rejecting the write must not be reported as success.

    The ramp written here must differ from the ramp already in place,
    otherwise the read-back comparison could not tell the two apart.
    """
    backend, xr = make_backend(accept=False)
    ramp = tuple(min(65535, int(65535 * ((i / 1023) ** (1 / 1.8)))) for i in range(1024))
    assert ramp != xr.ramp, "the test ramp must differ from the current one"
    with pytest.raises(GammaError):
        backend.write_ramp("HDMI-A-0", ramp, ramp, ramp)


def test_write_to_unknown_output_raises():
    backend, _ = make_backend()
    ramp = tuple(i * 65535 // 1023 for i in range(1024))
    with pytest.raises(GammaError):
        backend.write_ramp("NOPE-0", ramp, ramp, ramp)


def test_write_with_a_mismatched_ramp_length_raises():
    backend, xr = make_backend()
    with pytest.raises(GammaError):
        backend.write_ramp("HDMI-A-0", (1, 2, 3), (1, 2, 3), (1, 2, 3))
    assert xr.set_calls == 0


def test_missing_display_raises_gamma_unavailable():
    with pytest.raises(GammaUnavailable):
        XRandRGammaBackend(display_name="/nonexistent-display")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/lumen && python -m pytest tests/backends/test_xrandr_gamma.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lumen.backends.gamma.xrandr'`

- [ ] **Step 3: Write minimal implementation**

`lumen/backends/gamma/xrandr.py`:

```python
"""Software gamma control over X11 RandR.

The RandR gamma ramp holds 1024 entries per channel on the reference
machine, an order of magnitude finer than the monitor's own DDC
controls.

IMPORTANT: XRRSetCrtcGamma is an asynchronous X request. It is only
transmitted when the connection is flushed. A write that is not flushed
is silently discarded and the ramp keeps its previous value. Every write
here is therefore flushed, synced and then verified by reading the ramp
back. Without this the tool would appear to work sometimes and silently
fail to apply a calibration at other times.

The CRTC for an output is resolved on every access rather than cached,
because display topology changes (hotplug, clone/extend, resolution
changes) invalidate any cached mapping.

python-xlib is used rather than raw ctypes because the RandR reply
structures are version dependent: their layout depends on the protocol
version the server negotiated. Getting that wrong does not raise an
error, it reads garbage and then writes the gamma ramp to the wrong
display.
"""

from __future__ import annotations

from lumen.backends.base import GammaError

# The number of entries the gamma ramp holds on the reference machine.
DEFAULT_RAMP_SIZE = 1024


class GammaUnavailable(Exception):
    """No usable X display, or no RandR gamma support."""


class XRandRGammaBackend:
    """Controls a display's gamma ramp through libXrandr."""

    def __init__(self, display_name: str | None = None) -> None:
        try:
            from Xlib import display as xdisplay
            from Xlib.ext import randr
        except ImportError as exc:
            raise GammaUnavailable(
                "python-xlib is required for the X11 gamma backend"
            ) from exc

        self._randr = randr
        try:
            self._display = xdisplay.Display(display_name)
        except Exception as exc:
            raise GammaUnavailable(
                f"could not open the X display {display_name or 'default'}: {exc}"
            ) from exc

        if not self._display.has_extension("RANDR"):
            raise GammaUnavailable("the X server does not support RandR")

        self._crtcs: dict[str, int] = {}
        self._size = DEFAULT_RAMP_SIZE
        self._refresh_crtcs()

    # -- topology -----------------------------------------------------------

    def _refresh_crtcs(self) -> None:
        """Map output names to CRTC ids, re-reading the current topology."""
        root = self._display.screen().root
        resources = self._randr.get_screen_resources(root)

        mapping: dict[str, int] = {}
        for output_id in resources.outputs:
            try:
                info = self._display.xrandr_get_output_info(output_id, 0)
            except Exception:
                continue
            if not info.crtc:
                continue
            mapping[info.name] = info.crtc

        self._crtcs = mapping

    def _crtc_for(self, output: str) -> int:
        self._refresh_crtcs()
        try:
            return self._crtcs[output]
        except KeyError:
            raise GammaError(f"no gamma-capable output named {output!r}") from None

    # -- GammaBackend protocol ---------------------------------------------

    def list_outputs(self) -> list[str]:
        self._refresh_crtcs()
        return sorted(self._crtcs)

    def read_ramp(
        self, output: str
    ) -> tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]]:
        crtc = self._crtc_for(output)
        size = self._display.xrandr_get_crtc_gamma_size(crtc)
        size = int(size.size) if hasattr(size, "size") else int(size)
        if size <= 0:
            raise GammaError(f"output {output!r} reports no gamma ramp")
        ramp = self._display.xrandr_get_crtc_gamma(crtc)
        return (
            tuple(int(value) for value in ramp.red),
            tuple(int(value) for value in ramp.green),
            tuple(int(value) for value in ramp.blue),
        )

    def write_ramp(
        self,
        output: str,
        red: tuple[int, ...],
        green: tuple[int, ...],
        blue: tuple[int, ...],
    ) -> None:
        crtc = self._crtc_for(output)
        size = self._display.xrandr_get_crtc_gamma_size(crtc)
        size = int(size.size) if hasattr(size, "size") else int(size)
        if size <= 0:
            raise GammaError(f"output {output!r} reports no gamma ramp")
        if not (len(red) == len(green) == len(blue) == size):
            raise GammaError(f"ramp length mismatch: expected {size} entries per channel")

        self._display.xrandr_set_crtc_gamma(
            crtc, size, tuple(red), tuple(green), tuple(blue)
        )

        # The request is asynchronous: without this flush it never reaches
        # the server and the ramp silently keeps its old value.
        self._display.flush()
        self._display.sync()

        observed = self.read_ramp(output)
        if observed[0] != tuple(red):
            raise GammaError(
                f"the gamma ramp for {output!r} did not take the written value"
            )

```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/lumen && python -m pytest tests/backends/test_xrandr_gamma.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Run the full suite**

Run: `cd ~/lumen && python -m pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd ~/lumen && git add lumen/backends tests/backends
git commit -m "feat: X11 RandR gamma backend with flush and verification"
```

---

### Task 6: The uniform Control interface

**Files:**
- Create: `lumen/engine/controls.py`
- Create: `tests/engine/test_controls.py`

**Interfaces:**
- Consumes: `lumen.backends.base.{DdcBackend, GammaBackend, DdcError, GammaError}`, `lumen.model.monitor.{MonitorDescriptor, VcpValue}`, `lumen.model.vcp_registry.{VcpSpec, Safety, Group}`.
- Produces:
  - `enum.Enum` `ControlKind` with members `VCP`, `GAMMA`, `PRESET`.
  - `class Control(Protocol)`: attributes `code: str`, `label: str`, `kind: ControlKind`, `minimum: float`, `maximum: float`, `step: float`; methods `read() -> float`, `write(value: float) -> None`, `available() -> bool`.
  - `class VcpControl`: constructor `VcpControl(backend: DdcBackend, monitor: MonitorDescriptor, spec: VcpSpec)`. `code` is `f"vcp_0x{spec.code:02X}"`.
  - `class PresetControl`: constructor `PresetControl(backend: DdcBackend, monitor: MonitorDescriptor, spec: VcpSpec, options: tuple[int, ...])`. `read()` returns the raw value; `write(value)` accepts a value from `options`.
  - `class GammaControl`: constructor `GammaControl(backend: GammaBackend, output: str, channel: str, exponent: float = 2.2)`. `channel` is one of `"red"`, `"green"`, `"blue"`.
  - `function build_vcp_controls(backend: DdcBackend, monitor: MonitorDescriptor, capabilities: Capabilities) -> list[Control]` — one control per supported spec, dangerous codes excluded, presets built where the capability string lists enumerated values.

- [ ] **Step 1: Write the failing test**

`tests/engine/test_controls.py`:

```python
import pytest

from lumen.backends.base import DdcError, GammaError
from lumen.backends.fakes import FakeDdcBackend, FakeGammaBackend
from lumen.engine.controls import (
    ControlKind,
    GammaControl,
    PresetControl,
    VcpControl,
    build_vcp_controls,
)
from lumen.model.capability import parse_capabilities
from lumen.model.monitor import MonitorDescriptor
from lumen.model.vcp_registry import spec_for

MONITOR = MonitorDescriptor(identifier="fake:0", model="VG240Y")

CAPS = parse_capabilities(
    "vcp(04 10 12 16 18 1A 6C 6E 70 14(05 06 08 0B) 60(01 0F 11)) mccs_ver(2.2)"
)

VALUES = {
    0x10: (40, 100),
    0x12: (50, 100),
    0x16: (50, 100),
    0x18: (50, 100),
    0x1A: (50, 100),
    0x6C: (50, 100),
    0x6E: (50, 100),
    0x70: (50, 100),
    0x14: (11, 13),
    0x60: (17, 0x11),
}


def make_ddc(**kwargs):
    return FakeDdcBackend(
        monitors=[MONITOR],
        capabilities={code: () for code in CAPS.vcp_codes},
        values=dict(VALUES),
        **kwargs,
    )


def test_build_vcp_controls_excludes_dangerous_codes():
    controls = build_vcp_controls(make_ddc(), MONITOR, CAPS)
    codes = [control.code for control in controls]
    assert "vcp_0x10" in codes
    assert "vcp_0x04" not in codes


def test_build_vcp_controls_uses_the_reported_maximum_not_the_registry_default():
    """The monitor reports brightness maximum 100, but a real panel may not."""
    backend = FakeDdcBackend(
        monitors=[MONITOR],
        capabilities={0x10: ()},
        values={0x10: (10, 50)},
    )
    controls = build_vcp_controls(backend, MONITOR, parse_capabilities("vcp(10)"))
    control = controls[0]
    assert control.maximum == 50
    assert control.minimum == 0


def test_build_vcp_controls_makes_a_preset_for_enumerated_codes():
    controls = build_vcp_controls(make_ddc(), MONITOR, CAPS)
    preset = next(c for c in controls if c.code == "vcp_0x14")
    assert isinstance(preset, PresetControl)
    assert preset.options == (0x05, 0x06, 0x08, 0x0B)


def test_a_non_enumerated_code_is_a_plain_control():
    controls = build_vcp_controls(make_ddc(), MONITOR, CAPS)
    brightness = next(c for c in controls if c.code == "vcp_0x10")
    assert isinstance(brightness, VcpControl)
    assert brightness.kind is ControlKind.VCP


def test_reading_a_control_returns_its_current_value():
    backend = make_ddc()
    control = VcpControl(backend, MONITOR, spec_for(0x10))
    assert control.read() == 40


def test_writing_a_control_changes_the_backend():
    backend = make_ddc()
    control = VcpControl(backend, MONITOR, spec_for(0x10))
    control.write(70)
    assert backend.get(MONITOR, 0x10).value == 70


def test_writing_a_control_below_the_minimum_is_refused():
    backend = make_ddc()
    control = VcpControl(backend, MONITOR, spec_for(0x10))
    with pytest.raises(DdcError):
        control.write(-1)


def test_writing_a_control_above_the_maximum_is_refused():
    backend = make_ddc()
    control = VcpControl(backend, MONITOR, spec_for(0x10))
    with pytest.raises(DdcError):
        control.write(101)


def test_an_unsupported_control_is_marked_unavailable():
    backend = FakeDdcBackend(monitors=[MONITOR], values={})
    control = VcpControl(backend, MONITOR, spec_for(0x10))
    assert control.available() is False


def test_a_preset_control_rejects_a_value_outside_its_options():
    backend = make_ddc()
    control = PresetControl(backend, MONITOR, spec_for(0x14), (0x05, 0x06, 0x08, 0x0B))
    with pytest.raises(DdcError):
        control.write(0x77)


def test_a_preset_control_writes_an_allowed_option():
    backend = make_ddc()
    control = PresetControl(backend, MONITOR, spec_for(0x14), (0x05, 0x06, 0x08, 0x0B))
    control.write(0x05)
    assert backend.get(MONITOR, 0x14).value == 0x05


def test_gamma_control_reads_and_writes_one_channel():
    backend = FakeGammaBackend(size=64)
    control = GammaControl(backend, "fake-0", "red")
    assert control.kind is ControlKind.GAMMA
    control.write(1.8)
    red, green, _blue = backend.read_ramp("fake-0")
    assert red != green  # only the red channel changed


def test_gamma_control_reads_back_the_exponent_it_wrote():
    """The exponent must survive a write and read cycle."""
    backend = FakeGammaBackend(size=256)
    control = GammaControl(backend, "fake-0", "red")
    control.write(1.8)
    assert abs(control.read() - 1.8) < 0.05


def test_gamma_control_reads_back_a_neutral_exponent():
    backend = FakeGammaBackend(size=256)
    control = GammaControl(backend, "fake-0", "green")
    assert abs(control.read() - 1.0) < 0.05


def test_gamma_control_is_unavailable_without_a_gamma_backend():
    class NoGamma:
        def list_outputs(self):
            return []

        def read_ramp(self, output):
            raise GammaError("no gamma")

        def write_ramp(self, output, red, green, blue):
            raise GammaError("no gamma")

    control = GammaControl(NoGamma(), "fake-0", "red")
    assert control.available() is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/lumen && python -m pytest tests/engine/test_controls.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lumen.engine.controls'`

- [ ] **Step 3: Write minimal implementation**

`lumen/engine/controls.py`:

```python
"""The uniform control interface.

Every adjustable value the user can touch - a DDC VCP inside the
monitor, an enumerated preset, or one channel of a software gamma ramp -
is exposed as a `Control`. The wizard and the live panel work with
`Control` objects only, so neither has to know whether a value lives in
the hardware or in the X server.

Controls refuse values outside their range rather than clamping them, so
a mistyped or out-of-range value is reported instead of quietly applied
as something else.
"""

from __future__ import annotations

import math
from enum import Enum
from typing import Protocol, runtime_checkable

from lumen.backends.base import DdcBackend, DdcError, GammaBackend, GammaError
from lumen.model.capability import supported_specs
from lumen.model.monitor import Capabilities, MonitorDescriptor
from lumen.model.vcp_registry import Safety, VcpSpec


class ControlKind(Enum):
    """Where a control's value actually lives."""

    VCP = "vcp"
    GAMMA = "gamma"
    PRESET = "preset"


@runtime_checkable
class Control(Protocol):
    """One adjustable value."""

    code: str
    label: str
    kind: ControlKind
    minimum: float
    maximum: float
    step: float

    def read(self) -> float:
        """The current value."""

    def write(self, value: float) -> None:
        """Set the value. Raises on failure or an out-of-range value."""

    def available(self) -> bool:
        """Whether the control can currently be read and written."""


class VcpControl:
    """A single DDC/CI control inside the monitor."""

    kind = ControlKind.VCP

    def __init__(self, backend: DdcBackend, monitor: MonitorDescriptor, spec: VcpSpec) -> None:
        self._backend = backend
        self._monitor = monitor
        self._spec = spec
        self.code = f"vcp_0x{spec.code:02X}"
        self.label = spec.name
        self.step = 1.0
        self.minimum = float(spec.minimum)
        self.maximum = float(spec.maximum)

    def _reading(self):
        return self._backend.get(self._monitor, self._spec.code)

    def available(self) -> bool:
        try:
            return self._reading().supported
        except DdcError:
            return False

    def read(self) -> float:
        reading = self._reading()
        if not reading.supported:
            raise DdcError(f"the monitor does not support 0x{self._spec.code:02X}")
        # The monitor's own reported maximum wins over the registry's
        # assumption: panels differ, and a slider must reflect reality.
        if reading.maximum > 0:
            self.maximum = float(reading.maximum)
        return float(reading.value)

    def write(self, value: float) -> None:
        target = int(round(value))
        if target < self.minimum or target > self.maximum:
            raise DdcError(
                f"{self.label} must be between {int(self.minimum)} and "
                f"{int(self.maximum)}, not {target}"
            )
        self._backend.set(self._monitor, self._spec.code, target)


class PresetControl:
    """A DDC/CI control with a fixed list of permitted values."""

    kind = ControlKind.PRESET

    def __init__(
        self,
        backend: DdcBackend,
        monitor: MonitorDescriptor,
        spec: VcpSpec,
        options: tuple[int, ...],
    ) -> None:
        self._backend = backend
        self._monitor = monitor
        self._spec = spec
        self.options = tuple(options)
        self.code = f"vcp_0x{spec.code:02X}"
        self.label = spec.name
        self.step = 1.0
        self.minimum = float(min(options)) if options else 0.0
        self.maximum = float(max(options)) if options else 0.0

    def available(self) -> bool:
        try:
            return self._backend.get(self._monitor, self._spec.code).supported
        except DdcError:
            return False

    def read(self) -> float:
        reading = self._backend.get(self._monitor, self._spec.code)
        if not reading.supported:
            raise DdcError(f"the monitor does not support 0x{self._spec.code:02X}")
        return float(reading.value)

    def write(self, value: float) -> None:
        target = int(round(value))
        if target not in self.options:
            allowed = ", ".join(f"0x{option:02X}" for option in self.options)
            raise DdcError(
                f"{self.label} must be one of {allowed}, not 0x{target:02X}"
            )
        self._backend.set(self._monitor, self._spec.code, target)


class GammaControl:
    """One channel of a display's software gamma ramp."""

    kind = ControlKind.GAMMA

    def __init__(
        self,
        backend: GammaBackend,
        output: str,
        channel: str,
        exponent: float = 2.2,
    ) -> None:
        if channel not in ("red", "green", "blue"):
            raise ValueError(f"unknown gamma channel {channel!r}")
        self._backend = backend
        self._output = output
        self._channel = channel
        self._exponent = exponent
        self.code = f"gamma_{channel}"
        self.label = f"Gamma {channel.capitalize()}"
        self.minimum = 1.0
        self.maximum = 3.0
        self.step = 0.01

    def _index(self) -> int:
        return {"red": 0, "green": 1, "blue": 2}[self._channel]

    def available(self) -> bool:
        try:
            self._backend.read_ramp(self._output)
        except GammaError:
            return False
        return True

    def read(self) -> float:
        ramp = self._backend.read_ramp(self._output)
        channel = ramp[self._index()]
        size = len(channel)
        if size < 4:
            raise GammaError(f"the gamma ramp for {self._output!r} is too short")

        # The ramp maps an input fraction to 65535 * fraction ** (1/g).
        # Taking two points a quarter and three quarters of the way along
        # recovers g from the logarithm of the output ratio, and is far
        # less sensitive to rounding than two adjacent entries.
        lower_index = size // 4
        upper_index = (size * 3) // 4
        lower_fraction = lower_index / (size - 1)
        upper_fraction = upper_index / (size - 1)
        lower_output = channel[lower_index]
        upper_output = channel[upper_index]
        if upper_output <= 0 or lower_output <= 0:
            raise GammaError("the gamma ramp has no usable midtones")
        # The upper point always has the larger output, so the ratios are
        # taken upper-over-lower to keep them above one.
        output_ratio = upper_output / lower_output
        if output_ratio <= 1.0:
            return 1.0
        exponent = math.log(upper_fraction / lower_fraction) / math.log(output_ratio)
        return float(round(exponent, 3))

    def write(self, value: float) -> None:
        if value < self.minimum or value > self.maximum:
            raise GammaError(
                f"gamma must be between {self.minimum} and {self.maximum}, not {value}"
            )
        ramp = self._backend.read_ramp(self._output)
        channels = [list(ramp[0]), list(ramp[1]), list(ramp[2])]
        size = len(channels[0])
        target = channels[self._index()]
        for position in range(size):
            fraction = position / (size - 1) if size > 1 else 0.0
            target[position] = int(round(65535 * (fraction ** (1.0 / value))))
        channels[self._index()] = target
        self._backend.write_ramp(
            self._output,
            tuple(channels[0]),
            tuple(channels[1]),
            tuple(channels[2]),
        )


def build_vcp_controls(
    backend: DdcBackend,
    monitor: MonitorDescriptor,
    capabilities: Capabilities,
) -> list[Control]:
    """Build the controls a monitor supports.

    Dangerous codes are excluded, and a code whose capability string lists
    enumerated values becomes a `PresetControl` rather than a slider.
    """
    controls: list[Control] = []
    for spec in supported_specs(capabilities):
        if spec.safety is Safety.DANGEROUS:
            continue
        options = capabilities.vcp_values.get(spec.code, ())
        if options:
            controls.append(PresetControl(backend, monitor, spec, options))
        else:
            controls.append(VcpControl(backend, monitor, spec))
    return controls
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/lumen && python -m pytest tests/engine/test_controls.py -v`
Expected: PASS (14 tests)

- [ ] **Step 5: Run the full suite**

Run: `cd ~/lumen && python -m pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd ~/lumen && git add lumen/engine tests/engine
git commit -m "feat: uniform control interface over DDC, presets and gamma"
```

---

### Task 7: Gamma curve maths and the profile model

**Files:**
- Create: `lumen/engine/curves.py`
- Create: `lumen/model/profile.py`
- Create: `tests/engine/test_curves.py`
- Create: `tests/model/test_profile.py`

**Interfaces:**
- Consumes: `lumen.model.monitor.MonitorDescriptor`.
- Produces:
  - `dataclass(frozen=True) class GammaCurve`: fields `exponent: float = 2.2`, `red_exponent: float | None = None`, `green_exponent: float | None = None`, `blue_exponent: float | None = None`, `black_lift: int = 0`, `white_trim: int = 0`.
    - `property effective_exponents -> tuple[float, float, float]`.
  - `function build_ramp(size: int, exponent: float, black_lift: int = 0, white_trim: int = 0) -> tuple[int, ...]` — a 16-bit ramp of `size` entries.
  - `function build_channel_ramps(size: int, curve: GammaCurve) -> tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]]`.
  - `function is_linear(ramp: tuple[int, ...], size: int) -> bool`.
  - `dataclass class Profile`: fields `name: str`, `created: str`, `monitor: MonitorDescriptor`, `ddc: dict[int, int]`, `gamma: GammaCurve`, `notes: str = ""`.
    - `to_dict() -> dict` and `classmethod from_dict(data: dict) -> Profile`.
  - `function profile_matches_monitor(profile: Profile, monitor: MonitorDescriptor) -> bool`.
  - `function save_profile(profile: Profile, directory: Path) -> Path` and `function load_profile(path: Path) -> Profile` — JSON on disk.
  - `function profile_slug(name: str) -> str` — the filename a profile is stored under.
  - `function profile_directory() -> Path` — `~/.config/lumen/profiles`, honouring `XDG_CONFIG_HOME`.

- [ ] **Step 1: Write the failing test**

`tests/engine/test_curves.py`:

```python
import pytest

from lumen.engine.curves import build_channel_ramps, build_ramp, is_linear


def test_ramp_has_the_requested_number_of_entries():
    assert len(build_ramp(256, 2.2)) == 256


def test_a_linear_ramp_is_detected():
    ramp = build_ramp(256, 1.0)
    assert is_linear(ramp, 256)


def test_a_gamma_two_point_two_ramp_is_not_linear():
    ramp = build_ramp(256, 2.2)
    assert not is_linear(ramp, 256)


def test_ramp_starts_at_black_and_ends_at_white():
    ramp = build_ramp(256, 2.2)
    assert ramp[0] == 0
    assert ramp[-1] == 65535


def test_ramp_is_monotonically_increasing():
    ramp = build_ramp(256, 1.8)
    assert all(ramp[i] <= ramp[i + 1] for i in range(len(ramp) - 1))


def test_a_higher_exponent_lightens_the_midtones():
    """Gamma 2.2 should sit above gamma 1.8 in the midtones.

    The ramp maps an input value to an output value, so a larger
    exponent raises the output for a given input - the midtones get
    brighter, not darker.
    """
    lower = build_ramp(256, 1.8)
    higher = build_ramp(256, 2.2)
    midpoint = 128
    assert higher[midpoint] > lower[midpoint]


def test_black_lift_raises_the_shadow_end():
    plain = build_ramp(256, 2.2)
    lifted = build_ramp(256, 2.2, black_lift=1000)
    assert lifted[0] == 1000
    assert lifted[-1] == plain[-1]


def test_white_trim_lowers_the_highlight_end():
    plain = build_ramp(256, 2.2)
    trimmed = build_ramp(256, 2.2, white_trim=2000)
    assert trimmed[-1] == 65535 - 2000


def test_black_lift_and_white_trim_never_cross():
    ramp = build_ramp(256, 2.2, black_lift=20000, white_trim=20000)
    assert all(ramp[i] <= ramp[i + 1] for i in range(len(ramp) - 1))


def test_channel_ramps_use_per_channel_exponents():
    from lumen.model.profile import GammaCurve

    curve = GammaCurve(exponent=2.2, red_exponent=1.8)
    red, green, blue = build_channel_ramps(64, curve)
    assert red != green
    assert green == blue


def test_channel_ramps_default_to_the_shared_exponent():
    from lumen.model.profile import GammaCurve

    red, green, blue = build_channel_ramps(64, GammaCurve(exponent=2.2))
    assert red == green == blue
```

`tests/model/test_profile.py`:

```python
import json

import pytest

from lumen.model.monitor import MonitorDescriptor
from lumen.model.profile import (
    GammaCurve,
    Profile,
    load_profile,
    profile_directory,
    profile_matches_monitor,
    save_profile,
)

MONITOR = MonitorDescriptor(
    identifier="dev:/dev/i2c-4",
    manufacturer="Acer",
    model="VG240Y",
    serial="0x01122d3a",
    pnp_id="ACR0673",
    edid_hash="abc123",
    connection="HDMI",
    backend_name="ddccontrol",
)


def make_profile(**kwargs):
    defaults = {
        "name": "Evening",
        "created": "2026-11-11T20:00:00",
        "monitor": MONITOR,
        "ddc": {0x10: 60, 0x12: 55, 0x16: 52},
        "gamma": GammaCurve(exponent=2.1),
    }
    defaults.update(kwargs)
    return Profile(**defaults)


def test_profile_round_trips_through_a_dict():
    profile = make_profile()
    restored = Profile.from_dict(profile.to_dict())
    assert restored.name == profile.name
    assert restored.ddc == profile.ddc
    assert restored.gamma.exponent == profile.gamma.exponent
    assert restored.monitor == profile.monitor


def test_profile_round_trips_through_a_file(tmp_path):
    profile = make_profile()
    path = save_profile(profile, tmp_path)
    assert path.exists()
    restored = load_profile(path)
    assert restored.ddc == profile.ddc


def test_saved_profile_is_readable_json(tmp_path):
    path = save_profile(make_profile(), tmp_path)
    data = json.loads(path.read_text())
    assert data["name"] == "Evening"
    assert data["ddc"]["16"] == 52


def test_profile_filename_is_a_slug(tmp_path):
    path = save_profile(make_profile(name="Evening Reading"), tmp_path)
    assert path.name == "evening-reading.json"


def test_a_profile_matches_its_own_monitor():
    assert profile_matches_monitor(make_profile(), MONITOR)


def test_a_profile_does_not_match_a_different_monitor():
    other = MonitorDescriptor(identifier="other", model="Something Else")
    assert not profile_matches_monitor(make_profile(), other)


def test_a_profile_matches_a_monitor_with_the_same_model_and_serial():
    same_look = MonitorDescriptor(
        identifier="dev:/dev/i2c-9",
        manufacturer="Acer",
        model="VG240Y",
        serial="0x01122d3a",
        pnp_id="ACR0673",
        edid_hash="abc123",
        connection="HDMI",
        backend_name="ddccontrol",
    )
    assert profile_matches_monitor(make_profile(), same_look)


def test_profile_directory_follows_xdg_config_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert profile_directory() == tmp_path / "lumen" / "profiles"


def test_profile_directory_defaults_under_home(monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert profile_directory().parts[-2:] == ("lumen", "profiles")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/lumen && python -m pytest tests/engine/test_curves.py tests/model/test_profile.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lumen.engine.curves'`

- [ ] **Step 3: Write minimal implementation**

`lumen/engine/curves.py`:

```python
"""Gamma curve maths.

A gamma ramp maps an input value to an output value. The exponent
controls how the midtones are distributed: a value below 2.2 lightens
them, a value above 2.2 deepens them.

Two extra adjustments ride on top of the exponent:

- black lift raises the shadow end, so the darkest step is not crushed
- white trim lowers the highlight end, so the brightest step does not
  bloom

Both are applied in a way that cannot cross: the ramp stays monotonically
increasing whatever the user asks for.
"""

from __future__ import annotations

MAX_VALUE = 65535


def build_ramp(
    size: int,
    exponent: float,
    black_lift: int = 0,
    white_trim: int = 0,
) -> tuple[int, ...]:
    """Build a 16-bit gamma ramp of `size` entries."""
    if size < 2:
        raise ValueError("a gamma ramp needs at least two entries")
    if exponent <= 0:
        raise ValueError("the gamma exponent must be positive")

    values: list[int] = []
    for position in range(size):
        fraction = position / (size - 1)
        values.append(int(round(MAX_VALUE * (fraction ** (1.0 / exponent)))))

    if black_lift:
        values = [min(MAX_VALUE, value + black_lift) for value in values]
    if white_trim:
        values = [max(0, value - white_trim) for value in values]

    # Keep the ramp monotonic even if the adjustments overlap.
    for index in range(1, len(values)):
        if values[index] < values[index - 1]:
            values[index] = values[index - 1]
    return tuple(values)


def build_channel_ramps(size: int, curve) -> tuple[tuple[int, ...], ...]:
    """Build the red, green and blue ramps described by a `GammaCurve`."""
    from lumen.model.profile import GammaCurve

    if not isinstance(curve, GammaCurve):
        raise TypeError("expected a GammaCurve")
    red_exponent, green_exponent, blue_exponent = curve.effective_exponents
    return (
        build_ramp(size, red_exponent, curve.black_lift, curve.white_trim),
        build_ramp(size, green_exponent, curve.black_lift, curve.white_trim),
        build_ramp(size, blue_exponent, curve.black_lift, curve.white_trim),
    )


def is_linear(ramp: tuple[int, ...], size: int) -> bool:
    """Whether a ramp is the identity ramp."""
    if len(ramp) != size:
        return False
    for position, value in enumerate(ramp):
        expected = int(position * MAX_VALUE / (size - 1))
        if abs(value - expected) > 1:
            return False
    return True
```

`lumen/model/profile.py`:

```python
"""The saved calibration profile.

A profile stores intent rather than raw values, so it stays meaningful
when applied to different hardware: which VCPs to set, and the shape of
the gamma curve. The concrete ramp is always re-derived from those
parameters, so a profile is readable, diffable and small.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .monitor import MonitorDescriptor


@dataclass(frozen=True)
class GammaCurve:
    """The shape of the gamma ramp, per channel."""

    exponent: float = 2.2
    red_exponent: float | None = None
    green_exponent: float | None = None
    blue_exponent: float | None = None
    black_lift: int = 0
    white_trim: int = 0

    @property
    def effective_exponents(self) -> tuple[float, float, float]:
        """The exponent for each channel, falling back to the shared one."""
        red = self.red_exponent if self.red_exponent is not None else self.exponent
        green = self.green_exponent if self.green_exponent is not None else self.exponent
        blue = self.blue_exponent if self.blue_exponent is not None else self.exponent
        return (red, green, blue)

    def to_dict(self) -> dict:
        return {
            "exponent": self.exponent,
            "red_exponent": self.red_exponent,
            "green_exponent": self.green_exponent,
            "blue_exponent": self.blue_exponent,
            "black_lift": self.black_lift,
            "white_trim": self.white_trim,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GammaCurve":
        return cls(
            exponent=float(data.get("exponent", 2.2)),
            red_exponent=data.get("red_exponent"),
            green_exponent=data.get("green_exponent"),
            blue_exponent=data.get("blue_exponent"),
            black_lift=int(data.get("black_lift", 0)),
            white_trim=int(data.get("white_trim", 0)),
        )


@dataclass
class Profile:
    """A saved calibration."""

    name: str
    created: str
    monitor: MonitorDescriptor
    ddc: dict[int, int] = field(default_factory=dict)
    gamma: GammaCurve = field(default_factory=GammaCurve)
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "created": self.created,
            "monitor": {
                "identifier": self.monitor.identifier,
                "manufacturer": self.monitor.manufacturer,
                "model": self.monitor.model,
                "serial": self.monitor.serial,
                "pnp_id": self.monitor.pnp_id,
                "edid_hash": self.monitor.edid_hash,
                "connection": self.monitor.connection,
                "backend_name": self.monitor.backend_name,
            },
            "ddc": {f"{code:02X}": value for code, value in sorted(self.ddc.items())},
            "gamma": self.gamma.to_dict(),
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Profile":
        monitor_data = data.get("monitor", {})
        monitor = MonitorDescriptor(
            identifier=monitor_data.get("identifier", ""),
            manufacturer=monitor_data.get("manufacturer", ""),
            model=monitor_data.get("model", ""),
            serial=monitor_data.get("serial", ""),
            pnp_id=monitor_data.get("pnp_id"),
            edid_hash=monitor_data.get("edid_hash"),
            connection=monitor_data.get("connection", ""),
            backend_name=monitor_data.get("backend_name", ""),
        )
        ddc = {
            int(code, 16) if isinstance(code, str) else int(code): int(value)
            for code, value in (data.get("ddc") or {}).items()
        }
        return cls(
            name=data.get("name", ""),
            created=data.get("created", ""),
            monitor=monitor,
            ddc=ddc,
            gamma=GammaCurve.from_dict(data.get("gamma", {})),
            notes=data.get("notes", ""),
        )


def profile_matches_monitor(profile: Profile, monitor: MonitorDescriptor) -> bool:
    """Whether a profile was made for this monitor.

    Two monitors of the same model and serial are the same panel, even if
    they sit behind different connectors. Anything else is a mismatch.
    """
    if profile.monitor.edid_hash and monitor.edid_hash:
        return profile.monitor.edid_hash == monitor.edid_hash
    if profile.monitor.serial and monitor.serial:
        return (
            profile.monitor.serial == monitor.serial
            and profile.monitor.model == monitor.model
        )
    return profile.monitor.identifier == monitor.identifier


def profile_directory() -> Path:
    """Where profiles are stored, honouring XDG_CONFIG_HOME."""
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "lumen" / "profiles"


def profile_slug(name: str) -> str:
    """The filename a profile with this name is stored under."""
    cleaned = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return cleaned or "profile"


def save_profile(profile: Profile, directory: Path | None = None) -> Path:
    """Write a profile to disk as JSON."""
    target_dir = Path(directory) if directory is not None else profile_directory()
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{profile_slug(profile.name)}.json"
    path.write_text(json.dumps(profile.to_dict(), indent=2) + "\n", encoding="utf-8")
    return path


def load_profile(path: Path) -> Profile:
    """Read a profile from disk."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return Profile.from_dict(data)


def new_profile(
    name: str,
    monitor: MonitorDescriptor,
    ddc: dict[int, int] | None = None,
    gamma: GammaCurve | None = None,
    notes: str = "",
) -> Profile:
    """Build a profile stamped with the current time."""
    return Profile(
        name=name,
        created=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        monitor=monitor,
        ddc=dict(ddc or {}),
        gamma=gamma or GammaCurve(),
        notes=notes,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/lumen && python -m pytest tests/engine/test_curves.py tests/model/test_profile.py -v`
Expected: PASS (18 tests)

- [ ] **Step 5: Run the full suite**

Run: `cd ~/lumen && python -m pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd ~/lumen && git add lumen/model lumen/engine tests
git commit -m "feat: gamma curve maths and the saved profile model"
```

---

### Task 8: RGB channel mapping and its verification

**Files:**
- Create: `lumen/model/channels.py`
- Create: `tests/model/test_channels.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `enum.Enum` `ChannelOrder` with members `RGB` and `RBG` — the two orders seen in the wild.
  - `dataclass(frozen=True) class ChannelMapping`: fields `order: ChannelOrder = ChannelOrder.RGB`, `verified: bool = False`.
    - `property permutation -> tuple[str, str, str]` — which physical channel each logical channel maps to.
    - `property swapped -> bool`.
  - `function mapping_for(order: ChannelOrder) -> ChannelMapping`.
  - `function detect_mapping_from_capabilities(capabilities: Capabilities) -> ChannelMapping | None` — returns None when the capability string does not list the gain codes in a known order.
  - `function apply_permutation(values: dict[str, int], mapping: ChannelMapping) -> dict[str, int]` — permute a set of per-channel values.

- [ ] **Step 1: Write the failing test**

`tests/model/test_channels.py`:

```python
from lumen.model.capability import parse_capabilities
from lumen.model.channels import (
    ChannelOrder,
    apply_permutation,
    detect_mapping_from_capabilities,
    mapping_for,
)

REFERENCE = "vcp(04 10 12 14(05 06 08 0B) 16 18 1A 59 5A 5B 5C 5D 5E 60(01 0F 11) 62 6C 6E 70) mccs_ver(2.2)"


def test_default_mapping_is_rgb_and_unverified():
    mapping = mapping_for(ChannelOrder.RGB)
    assert mapping.order is ChannelOrder.RGB
    assert mapping.verified is False


def test_rgb_permutation_is_the_identity():
    assert mapping_for(ChannelOrder.RGB).permutation == ("red", "green", "blue")


def test_rbg_permutation_swaps_green_and_blue():
    assert mapping_for(ChannelOrder.RBG).permutation == ("red", "blue", "green")


def test_swapped_mapping_reports_itself():
    assert mapping_for(ChannelOrder.RBG).swapped is True
    assert mapping_for(ChannelOrder.RGB).swapped is False


def test_capability_string_in_rgb_order_is_detected():
    caps = parse_capabilities(REFERENCE)
    mapping = detect_mapping_from_capabilities(caps)
    assert mapping is not None
    assert mapping.order is ChannelOrder.RGB


def test_capability_string_with_swapped_gains_is_detected():
    caps = parse_capabilities("vcp(16 1A 18) mccs_ver(2.2)")
    mapping = detect_mapping_from_capabilities(caps)
    assert mapping is not None
    assert mapping.order is ChannelOrder.RBG


def test_a_capability_string_without_gain_codes_detects_nothing():
    caps = parse_capabilities("vcp(10 12) mccs_ver(2.2)")
    assert detect_mapping_from_capabilities(caps) is None


def test_apply_permutation_leaves_rgb_values_alone():
    values = {"red": 50, "green": 51, "blue": 52}
    assert apply_permutation(values, mapping_for(ChannelOrder.RGB)) == values


def test_apply_permutation_swaps_green_and_blue_for_rbg():
    values = {"red": 50, "green": 51, "blue": 52}
    result = apply_permutation(values, mapping_for(ChannelOrder.RBG))
    assert result == {"red": 50, "green": 52, "blue": 51}


def test_apply_permutation_leaves_other_keys_alone():
    values = {"red": 1, "green": 2, "blue": 3, "black_level": 9}
    result = apply_permutation(values, mapping_for(ChannelOrder.RBG))
    assert result["black_level"] == 9
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/lumen && python -m pytest tests/model/test_channels.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lumen.model.channels'`

- [ ] **Step 3: Write minimal implementation**

`lumen/model/channels.py`:

```python
"""RGB channel mapping.

The DDC/CI standard defines 0x16 as red gain, 0x18 as green gain and
0x1A as blue gain. Some monitors transpose green and blue, and their
capability string lists the codes in that transposed order.

The capability string is the only reliable evidence available, because
a DDC change is applied by the monitor after the framebuffer leaves the
GPU - software cannot observe which channel actually moved. So the
mapping is detected where possible, and the wizard asks the user to
confirm it visually.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .capability import Capabilities

# The gain codes in the order the standard defines them.
_GAIN_CODES = (0x16, 0x18, 0x1A)


class ChannelOrder(Enum):
    """The order the monitor's gain codes are listed in."""

    RGB = "rgb"
    RBG = "rbg"


@dataclass(frozen=True)
class ChannelMapping:
    """How logical channels map onto physical ones."""

    order: ChannelOrder = ChannelOrder.RGB
    verified: bool = False

    @property
    def permutation(self) -> tuple[str, str, str]:
        """The physical channel behind each logical red, green and blue."""
        if self.order is ChannelOrder.RBG:
            return ("red", "blue", "green")
        return ("red", "green", "blue")

    @property
    def swapped(self) -> bool:
        """Whether green and blue are transposed."""
        return self.order is ChannelOrder.RBG


def mapping_for(order: ChannelOrder) -> ChannelMapping:
    """A mapping for a given order, not yet confirmed by the user."""
    return ChannelMapping(order=order, verified=False)


def detect_mapping_from_capabilities(capabilities: Capabilities) -> ChannelMapping | None:
    """Infer the channel order from the capability string, if it can be.

    The codes are located in the raw string so that the order they
    appear in is preserved. Returns None when the monitor does not
    advertise all three gain codes, because then the order cannot be
    established.
    """
    if not all(code in capabilities.vcp_codes for code in _GAIN_CODES):
        return None

    raw = capabilities.raw.upper()
    positions: list[tuple[int, int]] = []
    for code in _GAIN_CODES:
        token = f"{code:02X}"
        index = raw.find(token)
        if index < 0:
            return None
        positions.append((index, code))
    positions.sort()
    ordered = tuple(code for _index, code in positions)
    if ordered == _GAIN_CODES:
        return mapping_for(ChannelOrder.RGB)
    if ordered == (0x16, 0x1A, 0x18):
        return mapping_for(ChannelOrder.RBG)
    return None


def apply_permutation(values: dict[str, int], mapping: ChannelMapping) -> dict[str, int]:
    """Permute the per-channel values according to the mapping.

    Keys that are not red, green or blue are passed through unchanged.
    """
    if not mapping.swapped:
        return dict(values)
    result = dict(values)
    if "green" in values and "blue" in values:
        result["green"], result["blue"] = values["blue"], values["green"]
    return result
```

- [ ] **Step 3: Run test to verify it passes**

Run: `cd ~/lumen && python -m pytest tests/model/test_channels.py -v`
Expected: PASS (9 tests)

- [ ] **Step 4: Run the full suite**

Run: `cd ~/lumen && python -m pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd ~/lumen && git add lumen/model tests/model
git commit -m "feat: RGB channel mapping with capability-based detection"
```

---

### Task 9: The calibration wizard

**Files:**
- Create: `lumen/engine/calibration.py`
- Create: `tests/engine/test_calibration.py`

**Interfaces:**
- Consumes: `lumen.engine.controls.{Control, ControlKind, build_vcp_controls}`, `lumen.model.capability.supported_specs`, `lumen.model.monitor.{MonitorDescriptor, Capabilities}`, `lumen.model.vcp_registry.{Group, Safety}`.
- Produces:
  - `dataclass(frozen=True) class CalibrationStep`: fields `key: str`, `title: str`, `instruction: str`, `pattern: str`, `control_codes: tuple[str, ...]`, `requires: tuple[str, ...]`.
  - `enum.Enum` `Pattern` with members `FLAT_GREY`, `WHITE_PATCH`, `NEAR_BLACK_RAMP`, `GREYSCALE_RAMP`, `COLOUR_PATCHES`, `PRIMARY_PATCHES`.
  - `function steps_for(capabilities: Capabilities) -> list[CalibrationStep]` — the wizard steps this monitor can actually run.
  - `function steps_with_gamma(capabilities: Capabilities) -> list[CalibrationStep]` — the same, with the software gamma curve step inserted after the colour temperature step.
  - `class CalibrationSession`: constructor `CalibrationSession(backend, monitor, capabilities, gamma_backend=None, gamma_output=None)`.
    - `property steps -> list[CalibrationStep]`
    - `property current_step -> CalibrationStep`
    - `function controls_for(step: CalibrationStep) -> list[Control]`
    - `function advance() -> None`
    - `function back() -> None`
    - `property finished -> bool`

- [ ] **Step 1: Write the failing test**

`tests/engine/test_calibration.py`:

```python
import pytest

from lumen.backends.fakes import FakeDdcBackend, FakeGammaBackend
from lumen.engine.calibration import (
    CalibrationSession,
    Pattern,
    steps_for,
    steps_with_gamma,
)
from lumen.model.capability import parse_capabilities
from lumen.model.monitor import MonitorDescriptor

MONITOR = MonitorDescriptor(identifier="fake:0", model="VG240Y")

FULL = parse_capabilities(
    "vcp(04 10 12 16 18 1A 6C 6E 70 14(05 06 08 0B) 59 5A 5B 5C 5D 5E "
    "9B 9C 9D 9E 9F A0 60(01 0F 11)) mccs_ver(2.2)"
)

MINIMAL = parse_capabilities("vcp(10) mccs_ver(2.2)")

VALUES = {
    0x10: (40, 100),
    0x12: (50, 100),
    0x16: (50, 100),
    0x18: (50, 100),
    0x1A: (50, 100),
    0x6C: (50, 100),
    0x6E: (50, 100),
    0x70: (50, 100),
    0x14: (11, 13),
    0x59: (50, 100),
    0x5A: (50, 100),
    0x5B: (50, 100),
    0x5C: (50, 100),
    0x5D: (50, 100),
    0x5E: (50, 100),
    0x9B: (50, 100),
    0x9C: (50, 100),
    0x9D: (50, 100),
    0x9E: (50, 100),
    0x9F: (50, 100),
    0xA0: (50, 100),
}


def make_session(capabilities=FULL, **kwargs):
    backend = FakeDdcBackend(
        monitors=[MONITOR],
        capabilities={code: () for code in capabilities.vcp_codes},
        values=dict(VALUES),
    )
    return CalibrationSession(backend, MONITOR, capabilities, **kwargs)


def test_a_full_monitor_gets_the_whole_wizard():
    steps = steps_for(FULL)
    keys = [step.key for step in steps]
    assert "baseline" in keys
    assert "white_point" in keys
    assert "black_point" in keys
    assert "colour_temperature" in keys
    assert "saturation" in keys
    assert "verify_channels" in keys


def test_the_black_point_is_checked_a_second_time():
    """Brightness interacts with the black level, so it is set twice."""
    steps = steps_for(FULL)
    keys = [step.key for step in steps]
    assert keys.count("black_point") == 2
    first = keys.index("black_point")
    second = keys.index("black_point", first + 1)
    assert second > keys.index("baseline")


def test_the_second_black_point_pass_says_so():
    steps = steps_for(FULL)
    black_steps = [step for step in steps if step.key == "black_point"]
    assert "again" in black_steps[1].instruction.lower()


def test_the_black_point_comes_after_the_baseline():
    """Raising brightness re-crushes shadows, so black is set after it."""
    steps = steps_for(FULL)
    keys = [step.key for step in steps]
    assert keys.index("baseline") < keys.index("black_point")
    assert keys.index("black_point") < keys.index("colour_temperature")


def test_a_minimal_monitor_skips_the_steps_it_cannot_run():
    steps = steps_for(MINIMAL)
    keys = [step.key for step in steps]
    assert "baseline" in keys
    assert "white_point" not in keys
    assert "black_point" not in keys
    assert "gamma_curve" not in keys


def test_a_monitor_with_no_usable_controls_gets_no_steps():
    assert steps_for(parse_capabilities("")) == []


def test_gamma_step_is_skipped_without_a_gamma_backend():
    session = make_session()
    keys = [step.key for step in session.steps]
    assert "gamma_curve" not in keys


def test_gamma_step_is_present_with_a_gamma_backend():
    session = make_session(gamma_backend=FakeGammaBackend(), gamma_output="fake-0")
    keys = [step.key for step in session.steps]
    assert "gamma_curve" in keys


def test_the_gamma_step_follows_the_colour_temperature_step():
    steps = steps_with_gamma(FULL)
    keys = [step.key for step in steps]
    assert keys.index("gamma_curve") == keys.index("colour_temperature") + 1


def test_the_session_starts_on_the_first_step():
    session = make_session()
    assert session.current_step is session.steps[0]


def test_advancing_moves_to_the_next_step():
    session = make_session()
    first = session.current_step
    session.advance()
    assert session.current_step is not first


def test_advancing_past_the_last_step_finishes_the_session():
    session = make_session()
    for _ in range(len(session.steps)):
        session.advance()
    assert session.finished is True


def test_going_back_returns_to_the_previous_step():
    session = make_session()
    first = session.current_step
    session.advance()
    session.back()
    assert session.current_step is first


def test_going_back_on_the_first_step_does_nothing():
    session = make_session()
    first = session.current_step
    session.back()
    assert session.current_step is first


def test_the_white_point_step_exposes_the_gain_controls():
    session = make_session()
    step = next(s for s in session.steps if s.key == "white_point")
    controls = session.controls_for(step)
    codes = {control.code for control in controls}
    assert "vcp_0x16" in codes
    assert "vcp_0x18" in codes
    assert "vcp_0x1A" in codes


def test_the_black_point_step_exposes_the_black_level_controls():
    session = make_session()
    step = next(s for s in session.steps if s.key == "black_point")
    codes = {control.code for control in session.controls_for(step)}
    assert "vcp_0x6C" in codes
    assert "vcp_0x6E" in codes
    assert "vcp_0x70" in codes


def test_the_saturation_step_exposes_the_saturation_controls():
    session = make_session()
    step = next(s for s in session.steps if s.key == "saturation")
    codes = {control.code for control in session.controls_for(step)}
    assert "vcp_0x59" in codes
    assert "vcp_0xA0" in codes


def test_the_black_point_step_uses_the_near_black_pattern():
    session = make_session()
    step = next(s for s in session.steps if s.key == "black_point")
    assert step.pattern is Pattern.NEAR_BLACK_RAMP


def test_every_step_carries_an_instruction_for_the_user():
    for step in steps_for(FULL):
        assert step.instruction.strip()
        assert step.title.strip()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/lumen && python -m pytest tests/engine/test_calibration.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lumen.engine.calibration'`

- [ ] **Step 3: Write minimal implementation**

`lumen/engine/calibration.py`:

```python
"""The calibration wizard.

The wizard is a sequence of steps. Each step names the controls it
exposes, the test pattern to display, and what the user should look for.

Steps are skipped when the monitor does not support the controls they
need. A projector that only offers brightness runs a short wizard; a
full desktop monitor runs the whole thing. The wizard therefore adapts
to the hardware rather than assuming a fixed control set.

The black point deliberately appears twice. Brightness interacts with
the black level - raising brightness after setting a black level
re-crushes the shadows - so the black point is set after the baseline
and then checked again.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from lumen.engine.controls import Control, build_vcp_controls
from lumen.model.monitor import Capabilities, MonitorDescriptor

BRIGHTNESS = 0x10
CONTRAST = 0x12
GAIN_CODES = (0x16, 0x18, 0x1A)
BLACK_CODES = (0x6C, 0x6E, 0x70)
PRESET = 0x14
COLOUR_TEMPERATURE = 0x0C
SATURATION_CODES = (0x59, 0x5A, 0x5B, 0x5C, 0x5D, 0x5E)
HUE_CODES = (0x9B, 0x9C, 0x9D, 0x9E, 0x9F, 0xA0)


class Pattern(Enum):
    """The test pattern shown while a step is in progress."""

    FLAT_GREY = "flat_grey"
    WHITE_PATCH = "white_patch"
    NEAR_BLACK_RAMP = "near_black_ramp"
    GREYSCALE_RAMP = "greyscale_ramp"
    COLOUR_PATCHES = "colour_patches"
    PRIMARY_PATCHES = "primary_patches"


@dataclass(frozen=True)
class CalibrationStep:
    """One stage of the wizard."""

    key: str
    title: str
    instruction: str
    pattern: Pattern
    control_codes: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()


def _vcp_code(code: int) -> str:
    return f"vcp_0x{code:02X}"


def steps_for(capabilities: Capabilities) -> list[CalibrationStep]:
    """The wizard steps this monitor can actually run."""
    codes = capabilities.vcp_codes
    steps: list[CalibrationStep] = []

    baseline_codes = tuple(
        _vcp_code(code) for code in (BRIGHTNESS, CONTRAST) if code in codes
    )
    if baseline_codes:
        steps.append(
            CalibrationStep(
                key="baseline",
                title="Set the overall level",
                instruction=(
                    "Set brightness and contrast to a comfortable starting point. "
                    "Ignore colour for now - that comes later."
                ),
                pattern=Pattern.FLAT_GREY,
                control_codes=baseline_codes,
            )
        )

    white_codes = tuple(_vcp_code(code) for code in GAIN_CODES if code in codes)
    if white_codes:
        steps.append(
            CalibrationStep(
                key="white_point",
                title="Set the white point",
                instruction=(
                    "Raise the white point until the patch is as bright as you want "
                    "without the panel blooming. Then adjust each channel until the "
                    "patch looks neutral to you."
                ),
                pattern=Pattern.WHITE_PATCH,
                control_codes=white_codes,
            )
        )

    black_codes = tuple(_vcp_code(code) for code in BLACK_CODES if code in codes)
    if black_codes:
        steps.append(
            CalibrationStep(
                key="black_point",
                title="Set the black point",
                instruction=(
                    "Set the black level so the darkest step is truly black, but the "
                    "second step is still distinguishable. Adjust each channel so the "
                    "shadows have no colour cast."
                ),
                pattern=Pattern.NEAR_BLACK_RAMP,
                control_codes=black_codes,
            )
        )
        steps.append(
            CalibrationStep(
                key="black_point",
                title="Check the black point again",
                instruction=(
                    "Changing the brightness in step one re-crushes the shadows, so "
                    "check the black point again. The darkest step should still be "
                    "truly black, and the second step still distinguishable."
                ),
                pattern=Pattern.NEAR_BLACK_RAMP,
                control_codes=black_codes,
            )
        )

    temperature_codes = tuple(
        _vcp_code(code)
        for code in (PRESET, COLOUR_TEMPERATURE)
        if code in codes
    )
    if temperature_codes:
        steps.append(
            CalibrationStep(
                key="colour_temperature",
                title="Set the colour temperature",
                instruction=(
                    "Pick the preset closest to what you want, then fine-tune with the "
                    "per-channel gains until the ramp looks neutral."
                ),
                pattern=Pattern.GREYSCALE_RAMP,
                control_codes=temperature_codes,
            )
        )

    saturation_codes = tuple(
        _vcp_code(code) for code in SATURATION_CODES + HUE_CODES if code in codes
    )
    if saturation_codes:
        steps.append(
            CalibrationStep(
                key="saturation",
                title="Check the colours",
                instruction=(
                    "Confirm each colour looks right and that the hues point the right "
                    "way. Most people leave these at the default."
                ),
                pattern=Pattern.COLOUR_PATCHES,
                control_codes=saturation_codes,
            )
        )

    # The channel check only makes sense when the monitor has per-channel
    # gain controls - there is nothing to verify otherwise.
    if any(code in codes for code in GAIN_CODES):
        steps.append(
            CalibrationStep(
                key="verify_channels",
                title="Verify the colours",
                instruction=(
                    "Confirm each patch is the colour you expect. Some monitors swap "
                    "green and blue."
                ),
                pattern=Pattern.PRIMARY_PATCHES,
            )
        )

    return steps


def steps_with_gamma(capabilities: Capabilities) -> list[CalibrationStep]:
    """The wizard steps including the software gamma curve step."""
    steps = steps_for(capabilities)
    gamma_step = CalibrationStep(
        key="gamma_curve",
        title="Shape the curve",
        instruction=(
            "Adjust the curve so the midtones feel right - a higher value lightens "
            "them, a lower one deepens them. This is the fine control the hardware "
            "cannot do."
        ),
        pattern=Pattern.GREYSCALE_RAMP,
    )
    result: list[CalibrationStep] = []
    for step in steps:
        result.append(step)
        if step.key == "colour_temperature":
            result.append(gamma_step)
    if gamma_step not in result:
        result.append(gamma_step)
    return result


class CalibrationSession:
    """A wizard run against one monitor."""

    def __init__(
        self,
        backend,
        monitor: MonitorDescriptor,
        capabilities: Capabilities,
        gamma_backend=None,
        gamma_output: str | None = None,
    ) -> None:
        self._backend = backend
        self._monitor = monitor
        self._capabilities = capabilities
        self._gamma_backend = gamma_backend
        self._gamma_output = gamma_output
        self._controls = build_vcp_controls(backend, monitor, capabilities)
        self._index = 0
        if gamma_backend is not None and gamma_output is not None:
            self._steps = steps_with_gamma(capabilities)
        else:
            self._steps = steps_for(capabilities)

    @property
    def steps(self) -> list[CalibrationStep]:
        return list(self._steps)

    @property
    def current_step(self) -> CalibrationStep:
        if not self._steps:
            raise IndexError("this monitor has no calibration steps")
        return self._steps[min(self._index, len(self._steps) - 1)]

    @property
    def finished(self) -> bool:
        return self._index >= len(self._steps)

    def advance(self) -> None:
        if self._index < len(self._steps):
            self._index += 1

    def back(self) -> None:
        if self._index > 0:
            self._index -= 1

    def controls_for(self, step: CalibrationStep) -> list[Control]:
        """The controls a step exposes, in registry order."""
        wanted = set(step.control_codes)
        return [control for control in self._controls if control.code in wanted]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/lumen && python -m pytest tests/engine/test_calibration.py -v`
Expected: PASS (19 tests)

- [ ] **Step 5: Run the full suite**

Run: `cd ~/lumen && python -m pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd ~/lumen && git add lumen/engine tests/engine
git commit -m "feat: calibration wizard that adapts to the monitor's controls"
```

---

### Task 10: Profile application, snapshots and rollback

**Files:**
- Create: `lumen/engine/apply.py`
- Create: `tests/engine/test_apply.py`

**Interfaces:**
- Consumes: `lumen.engine.controls.{Control, ControlKind, build_vcp_controls}`, `lumen.engine.curves.build_channel_ramps`, `lumen.model.profile.{Profile, GammaCurve}`, `lumen.model.monitor.MonitorDescriptor`.
- Produces:
  - `dataclass(frozen=True) class Snapshot`: fields `ddc: dict[int, int]`, `gamma: tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]] | None`, `output: str | None`.
  - `class ProfileApplier`: constructor `ProfileApplier(backend, monitor, capabilities, gamma_backend=None, gamma_output=None)`.
    - `function capture() -> Snapshot` — records every writable control's current value and the current gamma ramp.
    - `function apply(profile: Profile) -> list[str]` — applies a profile, returns a list of human-readable notes about what was skipped.
    - `function restore(snapshot: Snapshot) -> None` — puts everything back.
    - `function current_values() -> dict[int, int]`.

- [ ] **Step 1: Write the failing test**

`tests/engine/test_apply.py`:

```python
import pytest

from lumen.backends.fakes import FakeDdcBackend, FakeGammaBackend
from lumen.engine.apply import ProfileApplier
from lumen.model.capability import parse_capabilities
from lumen.model.monitor import MonitorDescriptor
from lumen.model.profile import GammaCurve, Profile

MONITOR = MonitorDescriptor(identifier="fake:0", model="VG240Y")

CAPS = parse_capabilities(
    "vcp(04 10 12 16 18 1A 6C 6E 70 14(05 06 08 0B) 60(01 0F 11)) mccs_ver(2.2)"
)

VALUES = {
    0x10: (40, 100),
    0x12: (50, 100),
    0x16: (50, 100),
    0x18: (50, 100),
    0x1A: (50, 100),
    0x6C: (50, 100),
    0x6E: (50, 100),
    0x70: (50, 100),
    0x14: (11, 13),
}


def make_applier(**kwargs):
    backend = FakeDdcBackend(
        monitors=[MONITOR],
        capabilities={code: () for code in CAPS.vcp_codes},
        values=dict(VALUES),
    )
    return ProfileApplier(backend, MONITOR, CAPS, **kwargs), backend


def make_profile(**kwargs):
    defaults = {
        "name": "Test",
        "created": "2026-11-11T20:00:00",
        "monitor": MONITOR,
        "ddc": {0x10: 70, 0x12: 55, 0x16: 52},
        "gamma": GammaCurve(exponent=2.2),
    }
    defaults.update(kwargs)
    return Profile(**defaults)


def test_apply_sets_the_values_in_the_profile():
    applier, backend = make_applier()
    applier.apply(make_profile())
    assert backend.get(MONITOR, 0x10).value == 70
    assert backend.get(MONITOR, 0x12).value == 55
    assert backend.get(MONITOR, 0x16).value == 52


def test_apply_is_idempotent():
    applier, backend = make_applier()
    profile = make_profile()
    applier.apply(profile)
    applier.apply(profile)
    assert backend.get(MONITOR, 0x10).value == 70


def test_apply_skips_a_code_the_monitor_does_not_support():
    applier, backend = make_applier()
    notes = applier.apply(make_profile(ddc={0x10: 70, 0xAB: 20}))
    assert backend.get(MONITOR, 0x10).value == 70
    assert any("0xAB" in note for note in notes)


def test_apply_never_touches_a_dangerous_code():
    applier, backend = make_applier()
    notes = applier.apply(make_profile(ddc={0x04: 1, 0x10: 70}))
    assert backend.get(MONITOR, 0x10).value == 70
    assert backend.get(MONITOR, 0x04).supported is False, "0x04 must never be written"
    assert any("0x04" in note for note in notes)


def test_apply_refuses_the_screen_blanking_code():
    applier, backend = make_applier()
    applier.apply(make_profile(ddc={0x8D: 1}))
    assert backend.get(MONITOR, 0x8D).supported is False


def test_apply_reports_a_failure_rather_than_raising():
    backend = FakeDdcBackend(
        monitors=[MONITOR],
        capabilities={code: () for code in CAPS.vcp_codes},
        values=dict(VALUES),
        fail_on={0x10},
    )
    applier = ProfileApplier(backend, MONITOR, CAPS)
    notes = applier.apply(make_profile())
    assert any("0x10" in note for note in notes)


def test_capture_records_the_current_values():
    applier, _ = make_applier()
    snapshot = applier.capture()
    assert snapshot.ddc[0x10] == 40
    assert snapshot.ddc[0x12] == 50


def test_capture_records_the_gamma_ramp():
    gamma = FakeGammaBackend(size=64)
    applier, _ = make_applier(gamma_backend=gamma, gamma_output="fake-0")
    snapshot = applier.capture()
    assert snapshot.gamma is not None
    assert len(snapshot.gamma[0]) == 64
    assert snapshot.output == "fake-0"


def test_restore_puts_the_values_back():
    applier, backend = make_applier()
    snapshot = applier.capture()
    applier.apply(make_profile())
    applier.restore(snapshot)
    assert backend.get(MONITOR, 0x10).value == 40
    assert backend.get(MONITOR, 0x16).value == 50


def test_restore_puts_the_gamma_ramp_back():
    gamma = FakeGammaBackend(size=64)
    applier, _ = make_applier(gamma_backend=gamma, gamma_output="fake-0")
    snapshot = applier.capture()
    applier.apply(make_profile(gamma=GammaCurve(exponent=1.6)))
    assert gamma.read_ramp("fake-0")[0] != snapshot.gamma[0]
    applier.restore(snapshot)
    assert gamma.read_ramp("fake-0")[0] == snapshot.gamma[0]


def test_apply_writes_the_gamma_curve_when_a_gamma_backend_is_present():
    gamma = FakeGammaBackend(size=64)
    applier, _ = make_applier(gamma_backend=gamma, gamma_output="fake-0")
    applier.apply(make_profile(gamma=GammaCurve(exponent=1.8)))
    red, _green, _blue = gamma.read_ramp("fake-0")
    assert red[32] > red[16]


def test_apply_without_a_gamma_backend_notes_the_gamma_was_skipped():
    applier, _ = make_applier()
    notes = applier.apply(make_profile())
    assert any("gamma" in note.lower() for note in notes)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/lumen && python -m pytest tests/engine/test_apply.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lumen.engine.apply'`

- [ ] **Step 3: Write minimal implementation**

`lumen/engine/apply.py`:

```python
"""Applying, capturing and restoring a calibration.

The applier is deliberately forgiving and deliberately honest:

- a code the monitor does not support is skipped, and said so
- a write the monitor refuses is reported, not raised, so one bad
  control does not abandon the whole calibration
- dangerous codes are never written, whatever a profile asks for
- applying the same profile twice produces the same state

The snapshot is what makes a cancelled wizard safe. It records every
writable control and the whole gamma ramp, so restoring puts the display
back exactly as it was.
"""

from __future__ import annotations

from dataclasses import dataclass

from lumen.backends.base import DdcError, GammaError
from lumen.engine.controls import build_vcp_controls
from lumen.engine.curves import build_channel_ramps
from lumen.model.capability import supported_specs
from lumen.model.monitor import Capabilities, MonitorDescriptor
from lumen.model.profile import Profile
from lumen.model.vcp_registry import Safety


@dataclass(frozen=True)
class Snapshot:
    """A record of the display's state, used to restore it."""

    ddc: dict[int, int]
    gamma: tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]] | None = None
    output: str | None = None


class ProfileApplier:
    """Applies profiles to one monitor."""

    def __init__(
        self,
        backend,
        monitor: MonitorDescriptor,
        capabilities: Capabilities,
        gamma_backend=None,
        gamma_output: str | None = None,
    ) -> None:
        self._backend = backend
        self._monitor = monitor
        self._capabilities = capabilities
        self._gamma_backend = gamma_backend
        self._gamma_output = gamma_output
        self._dangerous = self._dangerous_codes()

    # -- reading ------------------------------------------------------------

    def _writable_codes(self) -> list[int]:
        return [
            spec.code
            for spec in supported_specs(self._capabilities)
            if spec.safety is not Safety.DANGEROUS
        ]

    def _dangerous_codes(self) -> set[int]:
        """Codes that must never be written, whatever a profile asks for."""
        return {
            spec.code
            for spec in supported_specs(self._capabilities)
            if spec.safety is Safety.DANGEROUS
        }

    def current_values(self) -> dict[int, int]:
        """Every writable control's current value."""
        values: dict[int, int] = {}
        for code in self._writable_codes():
            try:
                reading = self._backend.get(self._monitor, code)
            except DdcError:
                continue
            if reading.supported:
                values[code] = reading.value
        return values

    def capture(self) -> Snapshot:
        """Record the current state so it can be restored later."""
        gamma = None
        output = None
        if self._gamma_backend is not None and self._gamma_output is not None:
            try:
                gamma = self._gamma_backend.read_ramp(self._gamma_output)
                output = self._gamma_output
            except GammaError:
                gamma = None
        return Snapshot(ddc=self.current_values(), gamma=gamma, output=output)

    # -- writing ------------------------------------------------------------

    def apply(self, profile: Profile) -> list[str]:
        """Apply a profile, returning notes about anything skipped."""
        notes: list[str] = []
        supported = set(self._writable_codes())

        for code, value in sorted(profile.ddc.items()):
            if code not in supported:
                notes.append(
                    f"skipped 0x{code:02X}: this monitor does not support it"
                )
                continue
            if code in self._dangerous:
                notes.append(
                    f"refused 0x{code:02X}: that control can blank the screen "
                    "or destroy the monitor's settings"
                )
                continue
            try:
                self._backend.set(self._monitor, code, value)
            except DdcError as exc:
                notes.append(f"could not set 0x{code:02X}: {exc}")

        if self._gamma_backend is not None and self._gamma_output is not None:
            size = self._ramp_size()
            if size:
                red, green, blue = build_channel_ramps(size, profile.gamma)
                try:
                    self._gamma_backend.write_ramp(
                        self._gamma_output, red, green, blue
                    )
                except GammaError as exc:
                    notes.append(f"could not set the gamma curve: {exc}")
        else:
            notes.append("skipped the gamma curve: no software gamma is available")

        return notes

    def restore(self, snapshot: Snapshot) -> None:
        """Put the display back the way the snapshot found it."""
        for code, value in sorted(snapshot.ddc.items()):
            try:
                self._backend.set(self._monitor, code, value)
            except DdcError:
                continue

        if (
            snapshot.gamma is not None
            and self._gamma_backend is not None
            and snapshot.output is not None
        ):
            try:
                self._gamma_backend.write_ramp(snapshot.output, *snapshot.gamma)
            except GammaError:
                pass

    def _ramp_size(self) -> int:
        if self._gamma_backend is None or self._gamma_output is None:
            return 0
        try:
            ramp = self._gamma_backend.read_ramp(self._gamma_output)
        except GammaError:
            return 0
        return len(ramp[0]) if ramp and ramp[0] else 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/lumen && python -m pytest tests/engine/test_apply.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Run the full suite**

Run: `cd ~/lumen && python -m pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd ~/lumen && git add lumen/engine tests/engine
git commit -m "feat: profile application with capture and exact rollback"
```

---

### Task 11: Backend discovery and gamma ownership

**Files:**
- Create: `lumen/backends/registry.py`
- Create: `lumen/engine/conflict.py`
- Create: `tests/backends/test_registry.py`
- Create: `tests/engine/test_conflict.py`

**Interfaces:**
- Consumes: `lumen.backends.base.{DdcBackend, GammaBackend, DdcError}`, `lumen.backends.ddc.ddccontrol_dbus.DdcControlDBusBackend`, `lumen.backends.gamma.xrandr.{XRandRGammaBackend, GammaUnavailable}`.
- Produces:
  - `dataclass(frozen=True) class BackendAvailability`: fields `ddc: bool`, `gamma: bool`, `ddc_reason: str`, `gamma_reason: str`.
  - `function detect_backends() -> BackendAvailability` — probes what this machine offers.
  - `function make_ddc_backend() -> DdcBackend` — returns the first working DDC backend, raising `DdcError` if none works.
  - `function make_gamma_backend() -> GammaBackend | None` — returns a gamma backend, or None if the platform has none.
  - `class GammaOwner`: constructor `GammaOwner(gamma_backend, output, expected_ramp)`.
    - `function claim() -> None` — writes the expected ramp.
    - `function has_drifted() -> bool` — whether the ramp no longer matches.
    - `function reassert() -> bool` — rewrite the ramp if it drifted, returning whether it did.
  - `class NightLightController`: constructor `NightLightController(gsettings_runner=None)`.
    - `function is_available() -> bool`
    - `function is_scheduled() -> bool`
    - `function disable_schedule() -> tuple[bool, str]` — returns (changed, previous value).
    - `function restore_schedule(previous: str) -> bool`.

- [ ] **Step 1: Write the failing test**

`tests/backends/test_registry.py`:

```python
import pytest

from lumen.backends.base import DdcError
from lumen.backends.registry import (
    BackendAvailability,
    detect_backends,
    make_ddc_backend,
    make_gamma_backend,
)


def test_availability_reports_a_reason_when_ddc_is_missing():
    availability = BackendAvailability(
        ddc=False, gamma=False, ddc_reason="no backend", gamma_reason="no backend"
    )
    assert availability.ddc is False
    assert availability.ddc_reason


def test_make_ddc_backend_raises_when_nothing_works(monkeypatch):
    monkeypatch.setattr(
        "lumen.backends.registry.make_ddc_backend",
        lambda: (_ for _ in ()).throw(DdcError("nothing works")),
    )
    with pytest.raises(DdcError):
        make_ddc_backend()


def test_detect_backends_never_raises():
    """Detection must degrade gracefully, not crash the application."""
    availability = detect_backends()
    assert isinstance(availability, BackendAvailability)
    assert isinstance(availability.ddc, bool)
    assert isinstance(availability.gamma, bool)


def test_make_gamma_backend_returns_none_or_a_backend():
    backend = make_gamma_backend()
    assert backend is None or hasattr(backend, "read_ramp")
```

`tests/engine/test_conflict.py`:

```python
import pytest

from lumen.backends.fakes import FakeGammaBackend
from lumen.engine.conflict import GammaOwner, NightLightController


def linear(size):
    ramp = tuple(i * 65535 // (size - 1) for i in range(size))
    return ramp


def test_claim_writes_the_expected_ramp():
    backend = FakeGammaBackend(size=64)
    owner = GammaOwner(backend, "fake-0", (linear(64), linear(64), linear(64)))
    owner.claim()
    assert backend.read_ramp("fake-0")[0] == linear(64)


def test_no_drift_when_nothing_else_touches_the_ramp():
    backend = FakeGammaBackend(size=64)
    owner = GammaOwner(backend, "fake-0", (linear(64), linear(64), linear(64)))
    owner.claim()
    assert owner.has_drifted() is False


def test_drift_is_detected_when_another_program_rewrites_the_ramp():
    backend = FakeGammaBackend(size=64)
    owner = GammaOwner(backend, "fake-0", (linear(64), linear(64), linear(64)))
    owner.claim()
    other = tuple(0 for _ in range(64))
    backend.write_ramp("fake-0", other, other, other)
    assert owner.has_drifted() is True


def test_reassert_puts_the_ramp_back():
    backend = FakeGammaBackend(size=64)
    expected = (linear(64), linear(64), linear(64))
    owner = GammaOwner(backend, "fake-0", expected)
    owner.claim()
    other = tuple(0 for _ in range(64))
    backend.write_ramp("fake-0", other, other, other)
    assert owner.reassert() is True
    assert backend.read_ramp("fake-0")[0] == linear(64)
    assert owner.has_drifted() is False


def test_reassert_does_nothing_when_there_is_no_drift():
    backend = FakeGammaBackend(size=64)
    owner = GammaOwner(backend, "fake-0", (linear(64), linear(64), linear(64)))
    owner.claim()
    assert owner.reassert() is False


def test_night_light_is_detected_as_available():
    controller = NightLightController(gsettings_runner=lambda *a: "false")
    assert controller.is_available() is True


def test_night_light_schedule_is_read():
    controller = NightLightController(gsettings_runner=lambda *a: "true")
    assert controller.is_scheduled() is True


def test_night_light_schedule_can_be_disabled_and_restored():
    values = {"current": "true"}

    def runner(*args):
        if "get" in args[0]:
            return values["current"]
        values["current"] = "false"
        return ""

    controller = NightLightController(gsettings_runner=runner)
    changed, previous = controller.disable_schedule()
    assert changed is True
    assert previous == "true"
    assert controller.restore_schedule(previous) is True
    assert values["current"] == "true"


def test_a_system_without_gsettings_is_unavailable():
    """A runner that answers nothing models a machine with no gsettings."""
    controller = NightLightController(gsettings_runner=lambda *a: "")
    assert controller.is_available() is False
    assert controller.is_scheduled() is False


def test_disabling_an_unavailable_schedule_is_a_no_op():
    controller = NightLightController(gsettings_runner=lambda *a: "")
    changed, previous = controller.disable_schedule()
    assert changed is False
    assert previous == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/lumen && python -m pytest tests/backends/test_registry.py tests/engine/test_conflict.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lumen.backends.registry'`

- [ ] **Step 3: Write minimal implementation**

`lumen/backends/registry.py`:

```python
"""Backend discovery.

Lumen must work on machines where only some of the hardware paths are
available. Discovery therefore never raises: it reports what it found
and why, so the application can tell the user plainly rather than
failing with a stack trace.

The ddccontrol D-Bus service is tried first because it needs no
privileges. A ddcutil-based backend is the fallback for systems where
that daemon is not installed.
"""

from __future__ import annotations

from dataclasses import dataclass

from lumen.backends.base import DdcBackend, DdcError, GammaBackend


@dataclass(frozen=True)
class BackendAvailability:
    """What this machine offers."""

    ddc: bool
    gamma: bool
    ddc_reason: str = ""
    gamma_reason: str = ""


def _try_ddccontrol():
    from lumen.backends.ddc.ddccontrol_dbus import DdcControlDBusBackend

    backend = DdcControlDBusBackend()
    backend.list_monitors()
    return backend


def _try_ddcutil():
    import shutil
    import subprocess

    executable = shutil.which("ddcutil")
    if executable is None:
        raise DdcError("ddcutil is not installed")

    class DdcUtilBackend:
        """Drives the ddcutil command line tool."""

        def __init__(self) -> None:
            self._executable = executable

        def _run(self, *args: str) -> str:
            result = subprocess.run(
                [self._executable, *args],
                capture_output=True,
                text=True,
                timeout=20,
            )
            if result.returncode != 0:
                raise DdcError(result.stderr.strip() or "ddcutil failed")
            return result.stdout

        def list_monitors(self):
            from lumen.model.monitor import MonitorDescriptor

            output = self._run("detect", "--brief")
            monitors = []
            for line in output.splitlines():
                if "connected" in line:
                    identifier = line.split()[0]
                    monitors.append(
                        MonitorDescriptor(
                            identifier=identifier, backend_name="ddcutil"
                        )
                    )
            if not monitors:
                raise DdcError("ddcutil found no connected monitors")
            return monitors

        def capabilities(self, monitor):
            from lumen.model.capability import parse_capabilities

            output = self._run("capabilities", "--brief", monitor.identifier)
            return parse_capabilities(output)

        def get(self, monitor, code):
            from lumen.model.monitor import VcpValue

            output = self._run("getvcp", "--brief", monitor.identifier, f"0x{code:02X}")
            parts = output.replace("=", " ").split()
            value, maximum = 0, 0
            for part in parts:
                try:
                    number = int(part)
                except ValueError:
                    continue
                if maximum == 0:
                    value = number
                else:
                    maximum = number
                    break
            return VcpValue(code=code, value=value, maximum=maximum, supported=True)

        def set(self, monitor, code, value):
            self._run("setvcp", monitor.identifier, f"0x{code:02X}", str(value))
            confirmed = self.get(monitor, code)
            if confirmed.value != value:
                raise DdcError(
                    f"monitor did not accept 0x{code:02X}={value} "
                    f"(read back {confirmed.value})"
                )

    backend = DdcUtilBackend()
    backend.list_monitors()
    return backend


_DDC_BACKENDS = (_try_ddccontrol, _try_ddcutil)


def detect_backends() -> BackendAvailability:
    """Report what this machine offers, without raising."""
    ddc = False
    ddc_reason = ""
    for factory in _DDC_BACKENDS:
        try:
            factory()
        except Exception as exc:
            ddc_reason = str(exc) or factory.__name__
        else:
            ddc = True
            ddc_reason = factory.__name__
            break

    gamma = False
    gamma_reason = ""
    try:
        from lumen.backends.gamma.xrandr import XRandRGammaBackend

        backend = XRandRGammaBackend()
        backend.list_outputs()
    except Exception as exc:
        gamma_reason = str(exc) or "no gamma backend"
    else:
        gamma = True
        gamma_reason = "xrandr"

    return BackendAvailability(
        ddc=ddc,
        gamma=gamma,
        ddc_reason=ddc_reason,
        gamma_reason=gamma_reason,
    )


def make_ddc_backend() -> DdcBackend:
    """The first DDC backend that works."""
    failures: list[str] = []
    for factory in _DDC_BACKENDS:
        try:
            return factory()
        except Exception as exc:
            failures.append(f"{factory.__name__}: {exc}")
    raise DdcError(
        "no DDC/CI backend could be started:\n" + "\n".join(failures)
    )


def make_gamma_backend() -> GammaBackend | None:
    """A gamma backend, or None when the platform has none."""
    try:
        from lumen.backends.gamma.xrandr import XRandRGammaBackend

        return XRandRGammaBackend()
    except Exception:
        return None
```

`lumen/engine/conflict.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/lumen && python -m pytest tests/backends/test_registry.py tests/engine/test_conflict.py -v`
Expected: PASS (12 tests)

- [ ] **Step 5: Run the full suite**

Run: `cd ~/lumen && python -m pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd ~/lumen && git add lumen/backends lumen/engine tests
git commit -m "feat: backend discovery and gamma ownership arbitration"
```

---

### Task 12: The command line interface

**Files:**
- Create: `lumen/cli.py`
- Create: `tests/test_cli.py`

**Interfaces:**
- Consumes: `lumen.backends.registry.{detect_backends, make_ddc_backend, make_gamma_backend}`, `lumen.engine.apply.ProfileApplier`, `lumen.model.profile.{Profile, load_profile, save_profile, profile_directory, profile_matches_monitor, new_profile}`.
- Produces:
  - `function main(argv: list[str] | None = None) -> int` — the `lumenctl` entry point.
  - Subcommands: `detect`, `get <code>`, `set <code> <value>`, `profiles list`, `profiles show <name>`, `profiles apply <name>`, `profiles rm <name>`, `capture`, `restore <path>`, `doctor`.

- [ ] **Step 1: Write the failing test**

`tests/test_cli.py`:

```python
import json

import pytest

from lumen.backends.fakes import FakeDdcBackend
from lumen.cli import main
from lumen.model.monitor import MonitorDescriptor

MONITOR = MonitorDescriptor(identifier="fake:0", model="VG240Y")


class FakeBackendFactory:
    def __init__(self):
        self.backend = FakeDdcBackend(
            monitors=[MONITOR],
            capabilities={0x10: (), 0x12: ()},
            values={0x10: (40, 100), 0x12: (50, 100)},
        )

    def __call__(self):
        return self.backend


@pytest.fixture
def fake_backend(monkeypatch):
    factory = FakeBackendFactory()
    monkeypatch.setattr("lumen.cli.make_ddc_backend", factory)
    return factory.backend


def test_detect_prints_the_monitor(fake_backend, capsys):
    assert main(["detect"]) == 0
    out = capsys.readouterr().out
    assert "fake:0" in out


def test_doctor_reports_backends(fake_backend, capsys):
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "DDC" in out


def test_get_prints_a_value(fake_backend, capsys):
    assert main(["get", "0x10"]) == 0
    out = capsys.readouterr().out
    assert "40" in out


def test_set_writes_a_value(fake_backend, capsys):
    assert main(["set", "0x10", "70"]) == 0
    assert fake_backend.get(MONITOR, 0x10).value == 70


def test_set_rejects_a_value_outside_the_range(fake_backend, capsys):
    assert main(["set", "0x10", "500"]) != 0


def test_an_unknown_command_returns_a_failure(capsys):
    assert main(["nonsense"]) != 0


def test_capture_writes_a_snapshot(tmp_path, fake_backend, monkeypatch, capsys):
    target = tmp_path / "snapshot.json"
    assert main(["capture", "--output", str(target)]) == 0
    assert target.exists()
    data = json.loads(target.read_text())
    assert data["ddc"]["10"] == 40


def test_capture_then_restore_puts_the_values_back(tmp_path, fake_backend, capsys):
    target = tmp_path / "snapshot.json"
    assert main(["capture", "--output", str(target)]) == 0
    assert main(["set", "0x10", "70"]) == 0
    assert fake_backend.get(MONITOR, 0x10).value == 70
    assert main(["restore", str(target)]) == 0
    assert fake_backend.get(MONITOR, 0x10).value == 40


def test_profiles_list_is_empty_when_there_are_none(tmp_path, monkeypatch, fake_backend, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert main(["profiles", "list"]) == 0
    assert "No profiles" in capsys.readouterr().out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/lumen && python -m pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lumen.cli'`

- [ ] **Step 3: Write minimal implementation**

`lumen/cli.py`:

```python
"""The command line interface.

The CLI shares the engine with the graphical application, so there is one
implementation of "set brightness" rather than two. It is also what the
login service uses, and the first thing to run when something is not
working: `lumenctl doctor` reports which backends were found and why.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from lumen.backends.base import DdcError
from lumen.backends.registry import detect_backends, make_ddc_backend, make_gamma_backend
from lumen.engine.apply import ProfileApplier
from lumen.model.profile import (
    load_profile,
    profile_directory,
    profile_matches_monitor,
    profile_slug,
)


def _parse_code(text: str) -> int:
    return int(text, 16) if text.lower().startswith("0x") else int(text, 16)


def _applier():
    backend = make_ddc_backend()
    monitors = backend.list_monitors()
    if not monitors:
        raise DdcError("no monitors were found")
    monitor = monitors[0]
    capabilities = backend.capabilities(monitor)
    gamma = make_gamma_backend()
    output = None
    if gamma is not None:
        outputs = gamma.list_outputs()
        output = outputs[0] if outputs else None
    return ProfileApplier(backend, monitor, capabilities, gamma, output), monitor


def _cmd_detect(_args) -> int:
    backend = make_ddc_backend()
    monitors = backend.list_monitors()
    if not monitors:
        print("No monitors were found.")
        return 1
    for monitor in monitors:
        capabilities = backend.capabilities(monitor)
        print(f"{monitor.identifier}  {monitor.model or 'unknown model'}")
        print(f"  connection: {monitor.connection or 'unknown'}")
        print(f"  MCCS version: {capabilities.mccs_version or 'unknown'}")
        codes = sorted(capabilities.vcp_codes)
        print(f"  {len(codes)} VCP codes supported")
        for code in codes:
            print(f"    0x{code:02X}")
    return 0


def _cmd_get(args) -> int:
    backend = make_ddc_backend()
    monitor = backend.list_monitors()[0]
    value = backend.get(monitor, _parse_code(args.code))
    print(f"0x{value.code:02X} = {value.value} (maximum {value.maximum})")
    return 0


def _cmd_set(args) -> int:
    backend = make_ddc_backend()
    monitor = backend.list_monitors()[0]
    code = _parse_code(args.code)
    reading = backend.get(monitor, code)
    if reading.maximum and not (0 <= args.value <= reading.maximum):
        print(
            f"0x{code:02X} must be between 0 and {reading.maximum}, not {args.value}",
            file=sys.stderr,
        )
        return 1
    backend.set(monitor, code, args.value)
    print(f"0x{code:02X} = {args.value}")
    return 0


def _cmd_profiles(args) -> int:
    directory = profile_directory()
    if args.action == "list":
        paths = sorted(directory.glob("*.json")) if directory.exists() else []
        if not paths:
            print("No profiles yet. Run the calibration wizard to create one.")
            return 0
        for path in paths:
            profile = load_profile(path)
            print(f"{profile.name}  ({path.name})")
        return 0

    if args.action == "show":
        path = directory / f"{profile_slug(args.name)}.json"
        if not path.exists():
            print(f"No profile called {args.name!r}.", file=sys.stderr)
            return 1
        print(json.dumps(load_profile(path).to_dict(), indent=2))
        return 0

    if args.action == "rm":
        path = directory / f"{profile_slug(args.name)}.json"
        if not path.exists():
            print(f"No profile called {args.name!r}.", file=sys.stderr)
            return 1
        path.unlink()
        print(f"Removed {args.name}.")
        return 0

    if args.action == "apply":
        path = directory / f"{profile_slug(args.name)}.json"
        if not path.exists():
            print(f"No profile called {args.name!r}.", file=sys.stderr)
            return 1
        profile = load_profile(path)
        applier, monitor = _applier()
        if not profile_matches_monitor(profile, monitor):
            print(
                f"Warning: this profile was made for a different monitor "
                f"({profile.monitor.model or 'unknown'}).",
                file=sys.stderr,
            )
        notes = applier.apply(profile)
        for note in notes:
            print(note)
        print(f"Applied {profile.name}.")
        return 0

    return 1


def _cmd_capture(args) -> int:
    applier, _monitor = _applier()
    snapshot = applier.capture()
    target = Path(args.output) if args.output else Path("lumen-snapshot.json")
    payload = {
        "ddc": {f"{code:02X}": value for code, value in sorted(snapshot.ddc.items())},
        "output": snapshot.output,
        "gamma": None,
    }
    if snapshot.gamma is not None:
        payload["gamma"] = [list(channel) for channel in snapshot.gamma]
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {target}.")
    return 0


def _cmd_restore(args) -> int:
    from lumen.engine.apply import Snapshot

    payload = json.loads(Path(args.path).read_text(encoding="utf-8"))
    ddc = {int(code, 16): int(value) for code, value in payload.get("ddc", {}).items()}
    raw_gamma = payload.get("gamma")
    gamma = None
    if raw_gamma:
        gamma = tuple(tuple(int(value) for value in channel) for channel in raw_gamma)
    applier, _monitor = _applier()
    applier.restore(Snapshot(ddc=ddc, gamma=gamma, output=payload.get("output")))
    print(f"Restored {args.path}.")
    return 0


def _cmd_doctor(_args) -> int:
    availability = detect_backends()
    print("DDC/CI backend:")
    print(f"  available: {availability.ddc}")
    print(f"  detail:    {availability.ddc_reason}")
    print("Software gamma:")
    print(f"  available: {availability.gamma}")
    print(f"  detail:    {availability.gamma_reason}")
    print("Session:")
    print(f"  type:      {__import__('os').environ.get('XDG_SESSION_TYPE', 'unknown')}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lumenctl", description="Guided display calibration")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("detect", help="list monitors and their capabilities").set_defaults(func=_cmd_detect)
    sub.add_parser("doctor", help="report which backends are available").set_defaults(func=_cmd_doctor)

    get_parser = sub.add_parser("get", help="read a VCP value")
    get_parser.add_argument("code", help="the VCP code, for example 0x10")
    get_parser.set_defaults(func=_cmd_get)

    set_parser = sub.add_parser("set", help="write a VCP value")
    set_parser.add_argument("code", help="the VCP code, for example 0x10")
    set_parser.add_argument("value", type=int, help="the new value")
    set_parser.set_defaults(func=_cmd_set)

    profiles = sub.add_parser("profiles", help="manage saved profiles")
    profiles.add_argument("action", choices=["list", "show", "apply", "rm"])
    profiles.add_argument("name", nargs="?", default="")
    profiles.set_defaults(func=_cmd_profiles)

    capture = sub.add_parser("capture", help="record the current display state")
    capture.add_argument("--output", default="", help="where to write the snapshot")
    capture.set_defaults(func=_cmd_capture)

    restore = sub.add_parser("restore", help="put a captured state back")
    restore.add_argument("path", help="the snapshot file")
    restore.set_defaults(func=_cmd_restore)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exit_error:
        # argparse exits with 0 for --help and 2 for a bad command.
        return int(exit_error.code or 0)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    try:
        return args.func(args)
    except DdcError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # pragma: no cover - a last resort
        print(f"error: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/lumen && python -m pytest tests/test_cli.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Run the full suite**

Run: `cd ~/lumen && python -m pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd ~/lumen && git add lumen/cli.py tests/test_cli.py
git commit -m "feat: lumenctl command line interface"
```

---

### Task 13: Integration test against the real hardware

**Files:**
- Create: `tests/integration/test_real_hardware.py`
- Create: `tests/integration/conftest.py`

**Interfaces:**
- Consumes: `lumen.backends.ddc.ddccontrol_dbus.DdcControlDBusBackend`, `lumen.backends.gamma.xrandr.XRandRGammaBackend`, `lumen.engine.apply.ProfileApplier`, `lumen.model.profile.{Profile, new_profile}`.
- Produces: a pytest module marked `integration` that is skipped when the hardware is absent, and that never leaves the display changed.

- [ ] **Step 1: Write the test**

`tests/integration/conftest.py`:

```python
import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "integration: needs real display hardware; read-only by default"
    )


@pytest.fixture(scope="session")
def ddc_backend():
    """The real DDC backend, or skip when the service is not running."""
    from lumen.backends.base import DdcError
    from lumen.backends.ddc.ddccontrol_dbus import DdcControlDBusBackend

    try:
        backend = DdcControlDBusBackend()
        backend.list_monitors()
    except Exception as exc:
        pytest.skip(f"no DDC backend available: {exc}")
    return backend


@pytest.fixture(scope="session")
def monitor(ddc_backend):
    monitors = ddc_backend.list_monitors()
    if not monitors:
        pytest.skip("no monitors were found")
    return monitors[0]
```

`tests/integration/test_real_hardware.py`:

```python
"""Read-only checks against the real display.

These tests never write to the hardware unless the caller passes
--run-write-tests, and even then every value they touch is restored
before the test ends.
"""

import os

import pytest

from lumen.engine.apply import ProfileApplier

pytestmark = pytest.mark.integration


def test_the_backend_lists_at_least_one_monitor(ddc_backend):
    monitors = ddc_backend.list_monitors()
    assert len(monitors) >= 1


def test_the_monitor_advertises_its_capabilities(ddc_backend, monitor):
    capabilities = ddc_backend.capabilities(monitor)
    assert capabilities.vcp_codes
    assert 0x10 in capabilities.vcp_codes, "every monitor must offer brightness"


def test_reading_brightness_succeeds(ddc_backend, monitor):
    value = ddc_backend.get(monitor, 0x10)
    assert value.supported is True
    assert 0 <= value.value <= value.maximum


def test_the_reported_maximum_is_plausible(ddc_backend, monitor):
    """A brightness maximum of zero would break every slider."""
    value = ddc_backend.get(monitor, 0x10)
    assert value.maximum > 0


def test_the_applier_can_capture_and_restore(ddc_backend, monitor):
    capabilities = ddc_backend.capabilities(monitor)
    applier = ProfileApplier(ddc_backend, monitor, capabilities)
    snapshot = applier.capture()
    assert 0x10 in snapshot.ddc
    applier.restore(snapshot)
    after = ddc_backend.get(monitor, 0x10)
    assert after.value == snapshot.ddc[0x10]


@pytest.mark.skipif(
    not os.environ.get("LUMEN_RUN_WRITE_TESTS"),
    reason="set LUMEN_RUN_WRITE_TESTS=1 to allow writes to real hardware",
)
def test_a_write_and_restore_leaves_the_display_unchanged(ddc_backend, monitor):
    """Write a different value, then put the original back.

    This test changes what the user sees. It is opt-in through
    LUMEN_RUN_WRITE_TESTS, and it always restores the original value.
    """
    capabilities = ddc_backend.capabilities(monitor)
    applier = ProfileApplier(ddc_backend, monitor, capabilities)
    snapshot = applier.capture()
    original = snapshot.ddc[0x10]

    try:
        target = 50 if original != 50 else 60
        ddc_backend.set(monitor, 0x10, target)
        assert ddc_backend.get(monitor, 0x10).value == target
    finally:
        ddc_backend.set(monitor, 0x10, original)

    assert ddc_backend.get(monitor, 0x10).value == original
```

- [ ] **Step 2: Run the test to verify it passes or skips cleanly**

Run: `cd ~/lumen && python -m pytest tests/integration -v`
Expected: PASS, or SKIP with a clear reason if the service is not running

- [ ] **Step 3: Run the full suite**

Run: `cd ~/lumen && python -m pytest -q`
Expected: PASS

- [ ] **Step 4: Verify the display was left exactly as it was found**

Run:
```bash
export DISPLAY=:0
python3 - <<'EOF'
import dbus
bus = dbus.SystemBus()
obj = bus.get_object('ddccontrol.DDCControl', '/ddccontrol/DDCControl')
iface = dbus.Interface(obj, 'ddccontrol.DDCControl')
for code in (0x10, 0x12):
    print(f"0x{code:02X} = {int(iface.GetControl('dev:/dev/i2c-4', code)[1])}")
EOF
```
Expected: the same values as before the suite ran

- [ ] **Step 5: Commit**

```bash
cd ~/lumen && git add tests/integration
git commit -m "test: read-only integration checks against real hardware"
```

---

### Task 14: Documentation and packaging

**Files:**
- Create: `README.md`
- Create: `LICENSE`
- Create: `docs/architecture.md`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: the whole project.
- Produces: a README that states what the tool does and how to run it; an MIT licence; an architecture note explaining the layering rule that keeps the core portable; and packaging metadata with the console script entry point.

- [ ] **Step 1: Write the README**

`README.md`:

```markdown
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
a hardcoded list.

## Running it

```bash
python -m pytest              # the test suite
lumenctl doctor               # what this machine offers
lumenctl detect               # your monitors and their capabilities
lumenctl profiles list        # saved calibrations
```

The graphical application is built on GTK4 and libadwaita.

## Privileges

None needed. Lumen talks to the `ddccontrol` D-Bus service, whose policy
allows any user to read and write display controls. There is no `sudo`
step, no `udev` rule and no system-wide change.

## Platform support

Linux on X11 today. The core is written to be portable: the model and
engine layers import nothing platform-specific, so a Windows or macOS
version is a matter of writing new backends. Software gamma is not
available on GNOME/Wayland, where Lumen falls back to hardware control
only and says so.

## Licence

MIT. See `LICENSE`.
```

- [ ] **Step 2: Write the licence**

`LICENSE`:

```
MIT License

Copyright (c) 2026 Lumen contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

- [ ] **Step 3: Write the architecture note**

`docs/architecture.md`:

```markdown
# Architecture

Lumen is four layers. Dependencies point strictly downward.

```
ui/         GTK4 + libadwaita: wizard, live panel, test patterns
engine/     calibration logic, curve maths, apply and rollback
model/      pure data: the VCP registry, capabilities, profiles
backends/   the only code that touches real hardware
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
it is a VCP inside the monitor, an enumerated preset, or one channel of a
software gamma ramp. The wizard and the live panel work with `Control`
objects only, so there is one implementation of "set brightness" rather
than one per layer.

## Two rules that are easy to get wrong

**Every write is verified.** A DDC write is read back; a gamma write is
flushed, synced and read back. `XRRSetCrtcGamma` is an asynchronous X
request: without an explicit flush it never reaches the server and the
ramp silently keeps its old value. A tool that reported success without
checking would work sometimes and silently fail at other times.

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
user's, guided by a test pattern. What the software does reliably is
discover the controls, report their true ranges, remember the choices,
and put everything back if the user cancels.
```

- [ ] **Step 4: Update the packaging metadata**

Replace `pyproject.toml` with:

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "lumen"
version = "0.1.0"
description = "Guided display calibration"
readme = "README.md"
requires-python = ">=3.12"
license = { file = "LICENSE" }
authors = [{ name = "Lumen contributors" }]
keywords = ["display", "monitor", "calibration", "ddc", "gamma"]
classifiers = [
    "Environment :: X11 Applications :: GTK",
    "License :: OSI Approved :: MIT License",
    "Operating System :: POSIX :: Linux",
    "Programming Language :: Python :: 3.12",
    "Topic :: System :: Hardware",
]
dependencies = []

[project.optional-dependencies]
gui = ["PyGObject>=3.48"]
dev = ["pytest>=8"]

[project.scripts]
lumenctl = "lumen.cli:main"

[project.urls]
Homepage = "https://github.com/lumen-cal/lumen"

[tool.setuptools.packages.find]
include = ["lumen*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
markers = ["integration: needs real display hardware"]
```

- [ ] **Step 5: Verify the package installs and the console script works**

Run:
```bash
cd ~/lumen && python -m pip install -e . && lumenctl --help
```
Expected: the help text is printed and the exit status is 0

- [ ] **Step 6: Run the full suite one final time**

Run: `cd ~/lumen && python -m pytest -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
cd ~/lumen && git add README.md LICENSE docs pyproject.toml
git commit -m "docs: README, architecture note and MIT licence"
```

---

## Implementation record

The plan above covers the portable core. The guided interface and the
login service were built as well, and are recorded here so the plan
matches the repository.

### Task 15: The GTK4 interface

**Files:** `lumen/ui/{app,wizard,panel,patterns,widgets,nightlight}.py`,
`tests/ui/{test_patterns,test_widgets,test_nightlight}.py`

The wizard window shows a test pattern and the controls for the current
step; the live panel gives day-to-day control. The patterns are drawn
with Cairo and tested headlessly by rendering them into an offscreen
surface.

Two defects found while building this, both worth recording:

- **Refreshing a slider wrote to the monitor.** Setting a slider's value
  emits `value-changed`, which the wizard treated as the user moving
  the slider. Every redraw therefore wrote the value back over the bus:
  425 milliseconds per control, one to five seconds per wizard step,
  and the display changed without the user asking. The update is now
  blocked while a slider is being refreshed, with a regression test.
- **Reading a control costs about fifty milliseconds.** The values are
  cached, read once in the background, and the rollback snapshot is
  taken in the background too. The wizard now opens in about fifteen
  milliseconds and each step takes one to four.

### Task 16: The login service

**Files:** `lumen/service/daemon.py`, `systemd/lumen-service.service`,
`tests/service/test_daemon.py`

The service applies the saved calibration at login and again after the
display wakes, because the DDC settings live in the monitor and survive
on their own while the software gamma ramp lives in the X server and
does not. It watches DPMS rather than the kernel, because that is when
the X server discards the ramp.

### Task 17: Night light ownership

**Files:** `lumen/ui/nightlight.py`, `tests/ui/test_nightlight.py`

GNOME's Night Light holds the same gamma ramp the calibration needs.
The application takes ownership of it while it runs and puts the
schedule back on exit. The user asked for no night light, so the
schedule is switched off rather than fought with.
