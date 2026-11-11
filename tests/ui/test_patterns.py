"""Test pattern rendering.

The patterns are drawn with Cairo, so they can be rendered into an
offscreen surface and inspected without a display.
"""

import cairo

from lumen.engine.calibration import Pattern
from lumen.ui.patterns import PatternRenderer


def render(pattern: Pattern, width: int = 200, height: int = 100):
    """Render a pattern and return the Cairo surface it was drawn on."""
    surface = cairo.ImageSurface(cairo.FORMAT_RGB24, width, height)
    context = cairo.Context(surface)
    PatternRenderer(pattern).draw(context, width, height)
    surface.flush()
    return surface


def pixel_at(surface, x: int, y: int) -> tuple[int, int, int]:
    data = surface.get_data()
    stride = surface.get_stride()
    offset = y * stride + x * 4
    # Cairo's RGB24 stores the channels little endian as B, G, R, unused.
    blue = data[offset]
    green = data[offset + 1]
    red = data[offset + 2]
    return (red, green, blue)


def test_the_flat_grey_pattern_is_grey():
    surface = render(Pattern.FLAT_GREY)
    red, green, blue = pixel_at(surface, 100, 50)
    assert red == green == blue


def test_the_white_patch_pattern_has_a_white_centre_and_black_edge():
    surface = render(Pattern.WHITE_PATCH)
    assert pixel_at(surface, 100, 50) == (255, 255, 255)
    assert pixel_at(surface, 2, 2) == (0, 0, 0)


def test_the_greyscale_ramp_runs_from_black_to_white():
    surface = render(Pattern.GREYSCALE_RAMP, width=256, height=20)
    assert pixel_at(surface, 2, 10)[0] < 10
    assert pixel_at(surface, 250, 10)[0] > 245


def test_the_greyscale_ramp_is_monotonic():
    surface = render(Pattern.GREYSCALE_RAMP, width=128, height=10)
    values = [pixel_at(surface, x, 5)[0] for x in range(128)]
    assert all(values[i] <= values[i + 1] for i in range(len(values) - 1))


def test_the_near_black_ramp_stays_dark():
    """The shadow ramp must stay in the bottom of the range.

    The pattern is framed by a red border, so the ramp is sampled from
    inside it.
    """
    surface = render(Pattern.NEAR_BLACK_RAMP, width=256, height=20)
    for x in range(4, 252, 16):
        red, _green, _blue = pixel_at(surface, x, 10)
        assert red < 60, "the near-black ramp must stay dark"


def test_the_near_black_ramp_is_monotonic():
    surface = render(Pattern.NEAR_BLACK_RAMP, width=128, height=10)
    values = [pixel_at(surface, x, 5)[0] for x in range(4, 124)]
    assert all(values[i] <= values[i + 1] for i in range(len(values) - 1))


def test_the_near_black_ramp_is_framed_so_the_border_is_distinguishable():
    """The border tells the user where the edge of the ramp is."""
    surface = render(Pattern.NEAR_BLACK_RAMP, width=128, height=10)
    assert pixel_at(surface, 0, 5) == (255, 0, 0)


def test_the_primary_patches_are_pure_red_green_and_blue():
    surface = render(Pattern.PRIMARY_PATCHES, width=300, height=50)
    assert pixel_at(surface, 50, 25) == (255, 0, 0)
    assert pixel_at(surface, 150, 25) == (0, 255, 0)
    assert pixel_at(surface, 250, 25) == (0, 0, 255)


def test_the_colour_patches_render_all_six():
    surface = render(Pattern.COLOUR_PATCHES, width=300, height=200)
    seen = set()
    for y in (50, 150):
        for x in (50, 150, 250):
            seen.add(pixel_at(surface, x, y))
    assert len(seen) == 6


def test_rendering_a_zero_sized_area_does_not_raise():
    surface = cairo.ImageSurface(cairo.FORMAT_RGB24, 1, 1)
    context = cairo.Context(surface)
    PatternRenderer(Pattern.FLAT_GREY).draw(context, 0, 0)


def test_every_pattern_renders_without_raising():
    for pattern in Pattern:
        render(pattern)
