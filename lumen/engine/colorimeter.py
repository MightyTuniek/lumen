"""Guided measured calibration, hardware optimization, review and rollback."""

from __future__ import annotations

import json
import math
import re
import shutil
import tempfile
import sys
from contextlib import ExitStack
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from lumen.engine.cancellation import Cancellation, Cancelled
from lumen.backends.locking import display_lock
from lumen.engine.apply import Snapshot
from lumen.engine.controls import ControlKind, build_vcp_controls
from lumen.engine.measurement import load_calibration, read_cgats, validate_icc, write_verification_chart
from lumen.model.colorimeter import CalibrationTarget, DisplayTarget, Instrument, Progress, Reading, xy_to_uv
from lumen.model.profile import new_profile, profile_directory, save_profile


class MeasurementBackend(Protocol):
    def measure(self, instrument: Instrument, display_type: str, correction: str | None) -> Reading: ...
    def run(self, tool: str, *args: str, **kwargs) -> str: ...
    def instrument_args(self, instrument: Instrument, display_type: str, correction: str | None) -> list[str]: ...


class IccInstaller(Protocol):
    def current(self, device: str) -> str | None: ...
    def inhibit(self, device: str) -> None: ...
    def uninhibit(self, device: str) -> None: ...
    def install(self, device: str, path: Path) -> str: ...
    def restore(self, device: str, previous: str | None, installed: str | None) -> None: ...


def white_error(reading: Reading, target: CalibrationTarget) -> float:
    if target.xy is None:
        return 0.0
    return math.dist(reading.uv, xy_to_uv(*target.xy))


def score(reading: Reading, target: CalibrationTarget) -> float:
    return ((reading.y / target.luminance - 1) / 0.03)**2 + (white_error(reading, target) / 0.003)**2


def meets_target(reading: Reading, target: CalibrationTarget) -> bool:
    return abs(reading.y / target.luminance - 1) <= 0.03 and white_error(reading, target) <= 0.003


def adjust_hardware(controls, measure, target, token, on_reading=lambda reading: None):
    """Bounded brightness bisection and RGB coordinate descent, eight sweeps."""
    controls = {getattr(c, "vcp_code", None): c for c in controls}
    current = measure()
    on_reading(current)
    best = current
    values = {code: c.read() for code, c in controls.items()}
    best_values = dict(values)
    steps = {code: max(1, round((c.maximum - c.minimum) / 16)) for code, c in controls.items()}

    def sample():
        nonlocal current, best, best_values
        token.check()
        token.wait(0.25)
        current = measure()
        on_reading(current)
        if score(current, target) < score(best, target):
            best, best_values = current, dict(values)
        return current

    try:
        for _ in range(8):
            token.check()
            if meets_target(current, target):
                break
            brightness = controls.get(0x10)
            if brightness:
                low, high = math.ceil(brightness.minimum), math.floor(brightness.maximum)
                for _ in range(12):
                    token.check()
                    if abs(current.y / target.luminance - 1) <= 0.03 or low > high:
                        break
                    value = (low + high) // 2
                    brightness.write(value)
                    values[0x10] = value
                    sample()
                    if current.y < target.luminance:
                        low = value + 1
                    else:
                        high = value - 1
            if target.xy is not None:
                for code in (0x16, 0x18, 0x1A):
                    control = controls.get(code)
                    if control is None:
                        continue
                    start, starting_error = values[code], score(current, target)
                    winner, winner_reading = start, current
                    for direction in (-1, 1):
                        value = min(control.maximum, max(control.minimum, start + direction * steps[code]))
                        if value == start:
                            continue
                        control.write(value)
                        values[code] = value
                        trial = sample()
                        if score(trial, target) < score(winner_reading, target):
                            winner, winner_reading = value, trial
                    control.write(winner)
                    values[code], current = winner, winner_reading
                    if score(current, target) >= starting_error:
                        steps[code] = max(1, steps[code] // 2)
        # Return to the best *joint* brightness/gain combination, not the
        # final search trial. Re-measure to expose drift and noise honestly.
        for code, value in best_values.items():
            controls[code].write(value)
        current = measure()
        on_reading(current)
        return current
    except Exception:
        # A session snapshot, rather than the optimizer's best state, is
        # restored by the owner when a write/measurement fails.
        raise


@dataclass
class CalibrationResult:
    directory: Path
    curves: object
    report: dict
    warnings: list[str]


class ColorimeterSession:
    """One transaction. Enter, run, review, save/activate, then exit on one worker."""

    def __init__(self, applier, argyll: MeasurementBackend, instrument: Instrument,
                 display: DisplayTarget, target: CalibrationTarget, *, display_type="",
                 correction=None, automatic=True, colord: IccInstaller | None = None,
                 colord_device=None, token=None, on_event=None, on_prompt=None,
                 on_manual=None, prepare_patch=None, finish_patch=None):
        self.applier, self.argyll, self.instrument = applier, argyll, instrument
        self.display, self.target = display, target
        self.display_type, self.correction, self.automatic = display_type, correction, automatic
        self.colord, self.colord_device = colord, colord_device
        self.token = token or Cancellation()
        self.on_event = on_event or (lambda event: None)
        self.on_prompt = on_prompt or (lambda message, token: False)
        self.on_manual = on_manual or (lambda reading, token: True)
        self.prepare_patch = prepare_patch or (lambda token: None)
        self.finish_patch = finish_patch or (lambda token: None)
        self.stack = ExitStack()
        self.snapshot = None
        self.previous_icc = None
        self.previous_atoms = None
        self.installed = None
        self.committed = False
        self.changed = False
        self.saved_path = None
        self.result = None
        self.warnings = []
        self.controls = []
        self.directory = None

    def __enter__(self):
        try:
            self.token.check()
            if not self.display.argyll_display or not self.applier.gamma_backend or not self.applier.gamma_output:
                raise ValueError("Select an identified X11 display with gamma access")
            self.stack.enter_context(display_lock(self.applier.gamma_output))
            self.stack.enter_context(display_lock(None, self.applier.monitor.identifier))
            for control in build_vcp_controls(self.applier.backend, self.applier.monitor, self.applier.capabilities):
                if control.kind is ControlKind.VCP and control.vcp_code in (0x10, 0x16, 0x18, 0x1A):
                    try:
                        control.read()
                    except Exception:
                        self.warnings.append(f"{control.label} is unavailable; use the monitor menu")
                    else:
                        self.controls.append(control)
            if hasattr(self.applier.gamma_backend, "validate_output"):
                self.applier.gamma_backend.validate_output(self.applier.gamma_output)
            if hasattr(self.applier.gamma_backend, "capture_icc"):
                self.previous_atoms = self.applier.gamma_backend.capture_icc(self.applier.gamma_output)
            gamma = self.applier.gamma_backend.read_ramp(self.applier.gamma_output)
            if len(gamma) != 3 or len(gamma[0]) < 2 or not len(gamma[0]) == len(gamma[1]) == len(gamma[2]):
                raise ValueError("Display has no usable RGB gamma ramps")
            self.snapshot = Snapshot(self.applier.current_values(self.controls), gamma, self.applier.gamma_output)
            if self.colord and self.colord_device:
                self.previous_icc = self.colord.current(self.colord_device)
            self.directory = Path(tempfile.mkdtemp(prefix="lumen-colorimeter-"))
            return self
        except Exception:
            self.__exit__(*sys.exc_info())
            raise

    def measure(self):
        self.token.wait(0.2)
        reading = self.argyll.measure(self.instrument, self.display_type, self.correction)
        self.on_event(Progress("reading", f"{reading.y:.1f} cd/m² · x {reading.xy[0]:.4f}, y {reading.xy[1]:.4f}"))
        return reading

    def _readback(self, curves):
        backend, output = self.applier.gamma_backend, self.applier.gamma_output
        expected = curves.ramps(len(self.snapshot.gamma[0]))
        backend.write_ramp(output, *expected)
        if backend.read_ramp(output) != expected:
            raise ValueError("Display rejected the measured RGB calibration")

    def run(self) -> CalibrationResult:
        self.prepare_patch(self.token)
        # Opening/calibrating the instrument must succeed before the first
        # gamma or monitor write. This also checks USB permission failures.
        self.on_event(Progress("instrument", "Checking instrument access"))
        self.measure()
        self.changed = True
        if self.colord and self.colord_device:
            self.colord.inhibit(self.colord_device)
        size = len(self.snapshot.gamma[0])
        linear = tuple(round(i * 65535 / (size - 1)) for i in range(size))
        self.applier.gamma_backend.write_ramp(self.applier.gamma_output, linear, linear, linear)
        if self.applier.gamma_backend.read_ramp(self.applier.gamma_output) != (linear,)*3:
            raise ValueError("Could not establish a linear measurement baseline")
        self.on_event(Progress("hardware", "Measuring and adjusting monitor brightness and white balance"))
        if self.automatic and self.controls:
            try:
                baseline = adjust_hardware(self.controls, self.measure, self.target, self.token)
            except Cancelled:
                raise
            except Exception as error:
                failures = self.applier.restore(self.snapshot)
                if failures:
                    raise RuntimeError("; ".join(failures)) from error
                self.applier.gamma_backend.write_ramp(self.applier.gamma_output, linear, linear, linear)
                self.warnings.append(f"Automatic adjustment failed: {error}. Manual setup was used.")
                baseline = self.measure()
                while not self.on_manual(baseline, self.token):
                    baseline = self.measure()
        else:
            baseline = self.measure()
        missing = not self.automatic or {c.vcp_code for c in self.controls} != {0x10, 0x16, 0x18, 0x1A}
        if missing:
            self.warnings.append("Some monitor settings were adjusted manually; Lumen cannot restore or reapply manual changes")
            while not self.on_manual(baseline, self.token):
                baseline = self.measure()
        if not meets_target(baseline, self.target):
            self.warnings.append("Hardware setup did not reach the requested luminance or white point")
            if not self.on_prompt("The requested targets were not reached. Continue with the achieved baseline? Cancel to revise targets.", self.token):
                raise Cancelled("Revise the targets and try again")
        self.finish_patch(self.token)
        self.token.check()
        common = [f"-d{self.display.argyll_display}", "-N", *self.argyll.instrument_args(
            self.instrument, self.display_type, self.correction)]
        args = ["-v", "-m", "-ql" if self.target.mode == "quick" else "-qh", *common,
                f"-b{self.target.luminance}", "-gs" if self.target.response == "srgb" else f"-G{self.target.gamma}"]
        if self.target.xy is not None:
            args.append("-w" + ",".join(str(value) for value in self.target.xy))
        self.on_event(Progress("calibrate", "Calibrating grayscale and tone response"))
        self.argyll.run("dispcal", *args, "-o", "display.icc", "display", cwd=self.directory,
                        timeout=7200, log=self.directory / "argyll.log")
        curves = load_calibration(self.directory / "display.cal")
        self._readback(curves)
        if self.target.mode == "detailed":
            self.on_event(Progress("profile", "Measuring 729 profiling patches"))
            self.argyll.run("targen", "-v", "-d3", "-l300", "-e4", "-B4", "-g33", "-s17", "-f729", "profile",
                            cwd=self.directory, timeout=600, interactive=False, log=self.directory / "argyll.log")
            self.argyll.run("dispread", "-v", *common, "-k", "display.cal", "profile",
                            cwd=self.directory, timeout=7200, log=self.directory / "argyll.log")
            self.argyll.run("colprof", "-v", "-qm", "-aX", "-D", "Lumen measured display", "profile",
                            cwd=self.directory, timeout=1800, interactive=False, log=self.directory / "argyll.log")
            generated = self.directory / "profile.icc"
            if not generated.exists():
                generated = self.directory / "profile.icm"
            shutil.copyfile(generated, self.directory / "display.icc")
        self.on_event(Progress("verify", "Measuring 51 independent verification patches"))
        write_verification_chart(self.directory / "verify.ti1")
        self.argyll.run("dispread", "-v", "-w", *common, "-k", "display.cal", "verify",
                        cwd=self.directory, timeout=1800, log=self.directory / "argyll.log")
        text = self.argyll.run("profcheck", "-k", "verify.ti3", "display.icc", cwd=self.directory,
                               timeout=120, interactive=False, log=self.directory / "argyll.log")
        average = re.search(r"(?:average|avg\.?)[^\d\n]*([\d.]+)", text, re.I)
        maximum = re.search(r"(?:maximum|max\.?)[^\d\n]*([\d.]+)", text, re.I)
        if not average or not maximum:
            raise ValueError("Could not read CIEDE2000 verification results")
        _, rows = read_cgats(self.directory / "verify.ti3")
        if len(rows) != 51:
            raise ValueError("Incomplete independent verification measurements")
        rows.sort(key=lambda row: int(row["SAMPLE_ID"]))
        from lumen.engine.measurement import verification_patches
        for row, expected_patch in zip(rows, verification_patches()):
            if any(abs(float(row["RGB_" + channel]) / 100 - expected) > 1e-5
                   for channel, expected in zip("RGB", expected_patch)):
                raise ValueError("Verification chart does not match its measured data")
        white = Reading(*(float(rows[20]["XYZ_" + c]) for c in "XYZ"))
        black = float(rows[0]["XYZ_Y"])
        gray = []
        for index, row in enumerate(rows[:21]):
            level = index / 20
            expected = ((level / 12.92 if level <= 0.04045 else ((level + 0.055) / 1.055)**2.4)
                        if self.target.response == "srgb" else level**self.target.gamma)
            gray.append({"input": level, "luminance": float(row["XYZ_Y"]),
                         "expected_luminance": black + (white.y - black) * expected})
        report = {"target": self.target.to_dict(), "white_luminance": white.y, "white_xy": white.xy,
                  "white_uv_distance": white_error(white, self.target), "black_luminance": black,
                  "grayscale": gray,
                  "grayscale_max_error_percent": max(abs(row["luminance"] - row["expected_luminance"])
                      / max(white.y - black, 1e-9) * 100 for row in gray),
                  "average_delta_e00": float(average[1]), "maximum_delta_e00": float(maximum[1])}
        if not meets_target(white, self.target):
            self.warnings.append("Final measured white does not meet the requested targets")
        if report["average_delta_e00"] > 2 or report["maximum_delta_e00"] > 5:
            self.warnings.append("ICC verification exceeds average ΔE00 2 or maximum ΔE00 5")
        if (not all(math.isfinite(report[key]) for key in ("white_luminance", "black_luminance", "grayscale_max_error_percent", "average_delta_e00", "maximum_delta_e00"))
                or any(not math.isfinite(row["luminance"]) for row in gray)):
            raise ValueError("Non-finite verification results")
        self._readback(curves)
        icc = (self.directory / "display.icc").read_bytes()
        validate_icc(icc)
        report["warnings"] = self.warnings
        (self.directory / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        self.result = CalibrationResult(self.directory, curves, report, list(self.warnings))
        return self.result

    def save(self, name: str):
        self.token.check()
        if self.saved_path is not None:
            return self.saved_path
        if self.result is None:
            raise ValueError("Run and verify calibration before saving")
        calibration_id = uuid4().hex
        root = profile_directory().parent / "calibrations"
        root.mkdir(parents=True, exist_ok=True)
        staged = root / ("." + calibration_id)
        destination = root / calibration_id
        try:
            shutil.copytree(self.directory, staged)
            staged.rename(destination)
            profile = new_profile(name.strip() or "Colorimeter calibration", self.applier.monitor,
                                  ddc=self.applier.current_values(self.controls))
            profile.measured_curves = self.result.curves
            profile.display_target = self.display
            profile.icc_path = str(destination / "display.icc")
            profile.calibration = {"id": calibration_id, "instrument": asdict(self.instrument),
                                   "report": self.result.report, "targets": self.target.to_dict()}
            # Unique filenames avoid replacing any existing named calibration.
            profile_staging = root.parent / "profiles" / ("." + calibration_id)
            temporary_profile = save_profile(profile, profile_staging)
            final = profile_directory() / f"colorimeter-{calibration_id}.json"
            temporary_profile.replace(final)
            self.saved_path, self.profile = final, profile
            shutil.rmtree(profile_staging, ignore_errors=True)
            return final
        except Exception:
            shutil.rmtree(staged, ignore_errors=True)
            if self.saved_path is None:
                shutil.rmtree(destination, ignore_errors=True)
                shutil.rmtree(root.parent / "profiles" / ("." + calibration_id), ignore_errors=True)
            raise

    def activate(self):
        self.token.check()
        if self.saved_path is None:
            raise ValueError("Save the calibration before activating")
        if not self.colord or not self.colord_device:
            raise RuntimeError("No matching colord display is available. Calibration is saved; install colord, then retry or export the ICC profile.")
        # End inhibition before reading/assigning defaults. The original ramp
        # remains captured until the entire activation has succeeded.
        self.colord.uninhibit(self.colord_device)
        try:
            self.installed = self.colord.install(self.colord_device, Path(self.profile.icc_path))
            self.applier.gamma_backend.publish_icc(self.applier.gamma_output, Path(self.profile.icc_path).read_bytes())
            self._readback(self.result.curves)
            self.token.check()
            from lumen.model.settings import load_settings, save_settings
            save_settings(replace(load_settings(), default_profile=self.saved_path.name))
            self.committed = True
        except Exception:
            # Roll back partial association/atom changes immediately, so Retry
            # never starts from a partially activated desktop configuration.
            if self.installed:
                self.colord.restore(self.colord_device, self.previous_icc, self.installed)
                self.installed = None
            if self.previous_atoms is not None:
                self.applier.gamma_backend.restore_icc(self.applier.gamma_output, self.previous_atoms)
            self.colord.inhibit(self.colord_device)
            raise

    def __exit__(self, kind, error, traceback):
        failures = []
        try:
            if self.colord and self.colord_device:
                try:
                    self.colord.uninhibit(self.colord_device)
                except Exception as failure:
                    failures.append(f"Could not release colord inhibition: {failure}")
            if not self.committed and self.changed and self.snapshot is not None:
                if self.installed and self.colord:
                    try:
                        self.colord.restore(self.colord_device, self.previous_icc, self.installed)
                    except Exception as failure:
                        failures.append(f"ICC restoration failed: {failure}")
                if self.previous_atoms is not None:
                    try:
                        self.applier.gamma_backend.restore_icc(self.applier.gamma_output, self.previous_atoms)
                    except Exception as failure:
                        failures.append(f"ICC property restoration failed: {failure}")
                failures.extend(self.applier.restore(self.snapshot))
        finally:
            self.stack.close()
            if self.directory:
                shutil.rmtree(self.directory, ignore_errors=True)
        if failures:
            raise RuntimeError("; ".join(failures)) from error
