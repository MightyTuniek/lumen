"""Portable measurement, calibration target and display identity data."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Instrument:
    identifier: str
    name: str
    port: int


@dataclass(frozen=True)
class DisplayTarget:
    output: str
    edid_hash: str | None = None
    argyll_display: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class CalibrationTarget:
    white_point: str = "D65"
    temperature: float = 6500
    luminance: float = 120
    response: str = "gamma"
    gamma: float = 2.2
    mode: str = "quick"

    def __post_init__(self) -> None:
        if self.white_point not in ("D65", "D50", "native", "custom"):
            raise ValueError("Unknown white point")
        if self.response not in ("gamma", "srgb") or self.mode not in ("quick", "detailed"):
            raise ValueError("Unknown response or profiling mode")
        for value, low, high in ((self.temperature, 4000, 10000),
                                 (self.luminance, 50, 400), (self.gamma, 1.6, 2.6)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
                raise ValueError(f"Target must be between {low} and {high}")

    @property
    def xy(self) -> tuple[float, float] | None:
        if self.white_point == "native":
            return None
        if self.white_point == "D65":
            return (0.3127, 0.3290)
        if self.white_point == "D50":
            return (0.34567, 0.35850)
        t = self.temperature
        x = (-4.607e9 / t**3 + 2.9678e6 / t**2 + 99.11 / t + 0.244063
             if t <= 7000 else -2.0064e9 / t**3 + 1.9018e6 / t**2 + 247.48 / t + 0.237040)
        return x, -3 * x*x + 2.87*x - 0.275

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Reading:
    x: float
    y: float
    z: float

    def __post_init__(self) -> None:
        if any(not math.isfinite(v) or v < 0 for v in (self.x, self.y, self.z)):
            raise ValueError("Invalid XYZ reading")

    @property
    def xy(self) -> tuple[float, float]:
        total = self.x + self.y + self.z
        if total <= 0:
            raise ValueError("Black has no measurable chromaticity")
        return self.x / total, self.y / total

    @property
    def uv(self) -> tuple[float, float]:
        return xy_to_uv(*self.xy)


def xy_to_uv(x: float, y: float) -> tuple[float, float]:
    denominator = -2*x + 12*y + 3
    return 4*x / denominator, 9*y / denominator


@dataclass(frozen=True)
class SampledCurves:
    """Absolute normalized RGB lookup tables on a uniform input grid."""

    red: tuple[float, ...]
    green: tuple[float, ...]
    blue: tuple[float, ...]

    def __post_init__(self) -> None:
        if len(self.red) < 2 or not len(self.red) == len(self.green) == len(self.blue):
            raise ValueError("Calibration requires three equally sized curves")
        for channel in (self.red, self.green, self.blue):
            if any(isinstance(v, bool) or not math.isfinite(v) or not 0 <= v <= 1 for v in channel):
                raise ValueError("Calibration samples must be finite and normalized")
            if any(b < a for a, b in zip(channel, channel[1:])):
                raise ValueError("Calibration curves must be monotonic")

    def ramps(self, size: int) -> tuple[tuple[int, ...], ...]:
        if size < 2:
            raise ValueError("Gamma ramp is too short")
        result = []
        for channel in (self.red, self.green, self.blue):
            ramp = []
            for i in range(size):
                position = i * (len(channel) - 1) / (size - 1)
                left = min(int(position), len(channel) - 2)
                value = channel[left] + (channel[left + 1] - channel[left]) * (position - left)
                ramp.append(round(value * 65535))
            result.append(tuple(ramp))
        return tuple(result)

    def to_dict(self) -> dict:
        return {name: list(getattr(self, name)) for name in ("red", "green", "blue")}

    @classmethod
    def from_dict(cls, data: dict) -> SampledCurves:
        return cls(*(tuple(data[name]) for name in ("red", "green", "blue")))


@dataclass(frozen=True)
class Progress:
    stage: str
    message: str
    fraction: float | None = None
    prompt: bool = False
