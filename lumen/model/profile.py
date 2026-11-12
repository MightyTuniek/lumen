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
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .monitor import MonitorDescriptor
from .colorimeter import DisplayTarget, SampledCurves


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
    measured_curves: SampledCurves | None = None
    display_target: DisplayTarget | None = None
    calibration: dict | None = None
    icc_path: str | None = None

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
            "measured_curves": self.measured_curves.to_dict() if self.measured_curves else None,
            "display_target": self.display_target.to_dict() if self.display_target else None,
            "calibration": self.calibration,
            "icc_path": self.icc_path,
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
            measured_curves=(SampledCurves.from_dict(data["measured_curves"])
                             if data.get("measured_curves") else None),
            display_target=(DisplayTarget(**data["display_target"])
                            if data.get("display_target") else None),
            calibration=data.get("calibration"),
            icc_path=data.get("icc_path"),
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
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target_dir,
                                         delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(profile.to_dict(), indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
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
