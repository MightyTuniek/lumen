"""Application preferences, separate from display calibration profiles."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from .profile import profile_directory


@dataclass(frozen=True)
class Settings:
    """Preferences shared by the GUI and the background service."""

    appearance: str = "system"
    opening_screen: str = "wizard"
    default_profile: str | None = None
    apply_at_login: bool = True
    reapply_on_wake: bool = True
    manage_night_light: bool = True

    def __post_init__(self) -> None:
        if self.appearance not in ("system", "light", "dark"):
            raise ValueError("Unknown appearance")
        if self.opening_screen not in ("wizard", "panel"):
            raise ValueError("Unknown opening screen")
        if self.default_profile is not None:
            name = self.default_profile
            if (
                not isinstance(name, str) or not name.endswith(".json")
                or name in (".json", "..json") or "/" in name or "\\" in name
            ):
                raise ValueError("Default profile must be a JSON filename")
        for name in ("apply_at_login", "reapply_on_wake", "manage_night_light"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be a boolean")


def settings_path() -> Path:
    """The preferences file, honouring XDG_CONFIG_HOME."""
    return profile_directory().parent / "settings.json"


def load_settings(path: Path | None = None) -> Settings:
    """Read valid fields; missing or damaged preferences use defaults."""
    path = path if path is not None else settings_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Settings must be a JSON object")
    except FileNotFoundError:
        return Settings()
    except (OSError, ValueError) as error:
        logging.getLogger(__name__).warning("Could not read preferences: %s", error)
        return Settings()
    valid = {}
    for name in Settings.__dataclass_fields__:
        if name in data:
            try:
                Settings(**{name: data[name]})
            except (ValueError, TypeError):
                continue
            valid[name] = data[name]
    return Settings(**valid)


def save_settings(settings: Settings, path: Path | None = None) -> None:
    """Atomically replace preferences, leaving the old file on failure."""
    path = path if path is not None else settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(asdict(settings), indent=2) + "\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def selected_profile_path(settings: Settings | None = None) -> Path | None:
    """Resolve the shared default; never silently replace a missing selection."""
    settings = settings if settings is not None else load_settings()
    directory = profile_directory()
    if settings.default_profile is not None:
        path = directory / settings.default_profile
        return path if path.is_file() else None
    return next(iter(sorted(directory.glob("*.json"))), None)
