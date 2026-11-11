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
