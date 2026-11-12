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

from lumen.engine.controls import Control, ControlKind, GammaControl, build_vcp_controls
from lumen.model.monitor import Capabilities, MonitorDescriptor
from lumen.model.profile import GammaCurve

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
                    "Changing the brightness in the first step re-crushes the "
                    "shadows, so check the black point again. The darkest step should "
                    "still be truly black, and the second step still "
                    "distinguishable."
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
        control_codes=("gamma_red", "gamma_green", "gamma_blue"),
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
            self._controls.extend(
                GammaControl(gamma_backend, gamma_output, channel)
                for channel in ("red", "green", "blue")
            )
        else:
            self._steps = steps_for(capabilities)

    @property
    def steps(self) -> list[CalibrationStep]:
        return list(self._steps)

    @property
    def controls(self) -> list[Control]:
        """Every control this monitor supports."""
        return list(self._controls)

    @property
    def gamma_curve(self) -> GammaCurve:
        """The gamma exponents currently shown by the controls."""
        values = {
            control.code: control.peek()
            for control in self._controls
            if control.kind is ControlKind.GAMMA
        }
        return GammaCurve(
            exponent=1.0,
            red_exponent=values.get("gamma_red"),
            green_exponent=values.get("gamma_green"),
            blue_exponent=values.get("gamma_blue"),
        )

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
