"""Display discovery shared by the GUI, CLI and login service."""

from __future__ import annotations

from dataclasses import dataclass, replace

from lumen.backends.base import DdcError
from lumen.backends.registry import make_ddc_backend, make_gamma_backend
from lumen.model.colorimeter import DisplayTarget
from lumen.model.monitor import Capabilities, MonitorDescriptor, VcpValue


class NoDdcBackend:
    def list_monitors(self):
        return []

    def capabilities(self, monitor):
        return Capabilities()

    def get(self, monitor, code):
        return VcpValue(code, 0, 0, False)

    def set(self, monitor, code, value):
        raise DdcError("This display has no DDC controls")


@dataclass
class DisplayChoice:
    monitor: MonitorDescriptor
    backend: object
    capabilities: Capabilities
    gamma_backend: object | None
    output: str | None

    def applier(self):
        from lumen.engine.apply import ProfileApplier
        return ProfileApplier(self.backend, self.monitor, self.capabilities,
                              self.gamma_backend, self.output)


def discover_displays(ddc=None, gamma=None) -> tuple[list[DisplayChoice], list[tuple]]:
    """Return output choices and unpaired DDC devices for explicit selection."""
    if ddc is None:
        try:
            ddc = make_ddc_backend()
        except DdcError:
            ddc = NoDdcBackend()
    gamma = gamma if gamma is not None else make_gamma_backend()
    monitors = []
    for monitor in ddc.list_monitors():
        try:
            caps = ddc.capabilities(monitor)
            monitor = ddc.describe(monitor) if hasattr(ddc, "describe") else monitor
        except DdcError:
            continue
        monitors.append((monitor, caps))
    outputs = gamma.describe_outputs() if gamma is not None and hasattr(gamma, "describe_outputs") else []
    if not outputs and gamma is not None:
        outputs = [MonitorDescriptor(identifier=f"output:{name}", connection=name, backend_name="xrandr")
                   for name in gamma.list_outputs()]
    choices = []
    paired = set()
    for output in outputs:
        matches = [(m, c) for m, c in monitors if
                   (m.edid_hash and m.edid_hash == output.edid_hash) or
                   (m.connection == output.connection and not (m.edid_hash and output.edid_hash and m.edid_hash != output.edid_hash))]
        # Duplicate EDIDs/connector matches are ambiguous, including clones.
        if len(matches) == 1:
            monitor, caps = matches[0]
            paired.add(monitor.identifier)
            monitor = replace(monitor, edid_hash=output.edid_hash or monitor.edid_hash,
                              connection=output.connection, serial=monitor.serial or output.serial)
            choices.append(DisplayChoice(monitor, ddc, caps, gamma, output.connection))
        else:
            choices.append(DisplayChoice(output, NoDdcBackend(), Capabilities(), gamma, output.connection))
    unpaired = [(m, c, ddc) for m, c in monitors if m.identifier not in paired]
    if not choices:
        choices = [DisplayChoice(m, ddc, c, None, None) for m, c in monitors]
    return choices, unpaired


def applier_for_profile(profile):
    """Resolve measured calibrations by physical identity, never output order."""
    from lumen.model.profile import profile_matches_monitor
    choices, _ = discover_displays()
    if profile.measured_curves is not None:
        target = profile.display_target
        if target is None:
            raise DdcError("Measured profile has no display identity")
        matches = [choice for choice in choices if choice.output and (
            choice.monitor.edid_hash == target.edid_hash if target.edid_hash
            else choice.output == target.output and profile_matches_monitor(profile, choice.monitor))]
        if len(matches) != 1:
            raise DdcError("The calibrated display is missing or ambiguous; select its mapping in Colorimeter")
        choice = matches[0]
        # A manually selected DDC pairing is retained by its stable backend ID.
        if profile.ddc and isinstance(choice.backend, NoDdcBackend):
            _, unpaired = discover_displays()
            candidates = [(m, c, b) for m, c, b in unpaired if profile_matches_monitor(profile, m)]
            if len(candidates) != 1:
                raise DdcError("The calibrated monitor's DDC connection is missing or ambiguous")
            choice.monitor, choice.capabilities, choice.backend = candidates[0]
        return choice.applier()
    if not choices:
        raise DdcError("No display could be reached")
    matches = [choice for choice in choices if profile_matches_monitor(profile, choice.monitor)]
    return (matches[0] if len(matches) == 1 else choices[0]).applier()


def apply_saved_profile(profile, applier=None):
    """Apply measured curves and, when enabled, their matching desktop ICC."""
    from pathlib import Path
    from lumen.backends.locking import display_lock
    from lumen.model.settings import load_settings

    applier = applier or applier_for_profile(profile)
    with display_lock(applier.gamma_output, applier.monitor.identifier), display_lock(None, applier.monitor.identifier):
        notes = applier.apply(profile)
        if profile.measured_curves is None or any(note.startswith(("refused", "could not")) for note in notes):
            return notes
        if not load_settings().colorimeter_activate_icc:
            return notes
        try:
            from lumen.backends.colorimeter.colord import ColordBackend
            colord = ColordBackend()
            device = colord.find_device(applier.gamma_output, profile.display_target.edid_hash)
            if not device or not profile.icc_path:
                raise RuntimeError("No matching colord display or saved ICC asset")
            path = Path(profile.icc_path)
            colord.install(device, path)
            applier.gamma_backend.publish_icc(applier.gamma_output, path.read_bytes())
            # Desktop loaders may write the same vcgt; always finish with our
            # absolute, verified samples, including after login and DPMS wake.
            size = len(applier.gamma_backend.read_ramp(applier.gamma_output)[0])
            applier.gamma_backend.write_ramp(applier.gamma_output, *profile.measured_curves.ramps(size))
        except Exception as error:
            notes.append(f"Calibration applied; ICC activation unavailable: {error}")
        return notes
