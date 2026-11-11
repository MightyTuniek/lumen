"""Gamma curve maths.

A gamma ramp maps an input value to an output value. The exponent
controls how the midtones are distributed: a value below 2.2 deepens
them, a value above 2.2 lightens them.

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
