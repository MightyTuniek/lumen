"""Optional colord adapter; dbus-python is loaded only when requested."""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path


class IccError(RuntimeError):
    pass


class ColordBackend:
    SERVICE = "org.freedesktop.ColorManager"
    ROOT = "/org/freedesktop/ColorManager"

    def __init__(self, bus=None):
        try:
            import dbus
            self._dbus = dbus
            self.bus = bus or dbus.SystemBus()
            self.manager = self._interface(self.ROOT, self.SERVICE)
            self.manager.GetDevicesByKind("display")
        except Exception as error:
            raise IccError(f"ICC activation requires colord and dbus-python: {error}") from error
        self.inhibited = set()

    def _interface(self, path, interface):
        return self._dbus.Interface(self.bus.get_object(self.SERVICE, path), interface)

    def device(self, path):
        return self._interface(path, self.SERVICE + ".Device")

    def properties(self, path, interface):
        return dict(self._interface(path, "org.freedesktop.DBus.Properties").GetAll(interface))

    def find_device(self, output: str, edid_hash: str | None = None) -> str | None:
        found = []
        for path in self.manager.GetDevicesByKind("display"):
            props = self.properties(path, self.SERVICE + ".Device")
            metadata = props.get("Metadata", {})
            if metadata.get("XRANDR_name") != output:
                continue
            recorded_hash = metadata.get("OutputEdidMd5") or metadata.get("EDID_md5")
            if edid_hash and recorded_hash and recorded_hash != edid_hash:
                continue
            found.append(str(path))
        return found[0] if len(found) == 1 else None

    def current(self, device: str):
        try:
            return str(self.device(device).GetProfileForQualifiers(["*"]))
        except Exception as error:
            if "NotFound" in str(error) or "NothingMatched" in str(error):
                return None
            raise IccError(f"Could not capture the active ICC profile: {error}") from error

    def inhibit(self, device: str):
        self.device(device).ProfilingInhibit()
        self.inhibited.add(device)

    def uninhibit(self, device: str):
        if device in self.inhibited:
            self.device(device).ProfilingUninhibit()
            self.inhibited.remove(device)

    def install(self, device: str, path: Path):
        """Publish a persistent user profile, then change the association."""
        previous = self.current(device)
        data = path.read_bytes()
        from lumen.engine.measurement import validate_icc
        validate_icc(data)
        checksum = hashlib.md5(data).hexdigest()
        directory = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "icc"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"lumen-{checksum}.icc"
        if not target.exists():
            temporary = target.with_suffix(".tmp")
            try:
                shutil.copyfile(path, temporary)
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
        profile = None
        added = False
        try:
            try:
                profile = self.manager.FindProfileById(f"icc-{checksum}")
            except Exception:
                with target.open("rb") as stream:
                    profile = self.manager.CreateProfileWithFd(
                        f"icc-{checksum}", "disk", self._dbus.types.UnixFd(stream.fileno()),
                        {"Filename": str(target), "Qualifier": "RGB.."})
            props = self.properties(device, self.SERVICE + ".Device")
            if str(profile) not in [str(item) for item in props.get("Profiles", [])]:
                self.device(device).AddProfile("hard", profile)
                added = True
            self.device(device).MakeProfileDefault(profile)
            # Callers release profiling inhibition before installation; query
            # the actual default rather than assuming array order.
            if self.current(device) != str(profile):
                raise IccError("colord did not make the ICC profile default")
            return str(profile)
        except Exception as error:
            failures = []
            if added:
                try:
                    self.device(device).RemoveProfile(profile)
                except Exception as restore_error:
                    failures.append(str(restore_error))
            if previous:
                try:
                    self.device(device).MakeProfileDefault(previous)
                except Exception as restore_error:
                    failures.append(str(restore_error))
            suffix = " Restoration failed: " + "; ".join(failures) if failures else ""
            raise IccError(f"ICC activation failed: {error}.{suffix}") from error

    def restore(self, device, previous, installed=None):
        if installed and installed != previous:
            self.device(device).RemoveProfile(installed)
        if previous:
            self.device(device).MakeProfileDefault(previous)
