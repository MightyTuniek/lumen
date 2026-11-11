"""Test pattern rendering.

The wizard shows a pattern while a step is in progress so the user has
something concrete to judge. The patterns are drawn with Cairo into a
GTK4 drawing area, which keeps them pixel-exact and resolution
independent.

The patterns are deliberately plain. The job is to make a judgement
about the image, not to admire a picture.
"""

from __future__ import annotations

from lumen.engine.calibration import Pattern


class PatternRenderer:
    """Draws a test pattern into a Cairo context."""

    def __init__(self, pattern: Pattern) -> None:
        self.pattern = pattern

    def draw(self, cr, width: int, height: int) -> None:
        """Render the pattern across the given area."""
        if width <= 0 or height <= 0:
            return
        handler = {
            Pattern.FLAT_GREY: self._flat_grey,
            Pattern.WHITE_PATCH: self._white_patch,
            Pattern.NEAR_BLACK_RAMP: self._near_black_ramp,
            Pattern.GREYSCALE_RAMP: self._greyscale_ramp,
            Pattern.COLOUR_PATCHES: self._colour_patches,
            Pattern.PRIMARY_PATCHES: self._primary_patches,
        }[self.pattern]
        handler(cr, width, height)

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _fill(cr, width, height, red, green, blue) -> None:
        cr.set_source_rgb(red, green, blue)
        cr.rectangle(0, 0, width, height)
        cr.fill()

    @staticmethod
    def _label(cr, text: str, x: float, y: float, size: int = 14) -> None:
        cr.set_source_rgb(0.5, 0.5, 0.5)
        cr.set_font_size(size)
        cr.move_to(x, y)
        cr.show_text(text)

    # -- patterns -----------------------------------------------------------

    def _flat_grey(self, cr, width, height) -> None:
        """A mid grey field: the reference for judging brightness."""
        self._fill(cr, width, height, 0.5, 0.5, 0.5)

    def _white_patch(self, cr, width, height) -> None:
        """A white patch on black: for judging the white point."""
        self._fill(cr, width, height, 0.0, 0.0, 0.0)
        margin = int(min(width, height) * 0.2)
        cr.set_source_rgb(1.0, 1.0, 1.0)
        cr.rectangle(margin, margin, width - margin * 2, height - margin * 2)
        cr.fill()

    def _near_black_ramp(self, cr, width, height) -> None:
        """Steps near black: for judging whether shadows are crushed."""
        steps = 16
        step_width = width / steps
        # The values stay in the bottom of the range, because that is
        # where the eye can tell a crushed shadow from a real one.
        for index in range(steps):
            level = (index / (steps - 1)) ** 2 * 0.12
            cr.set_source_rgb(level, level, level)
            cr.rectangle(index * step_width, 0, step_width + 1, height)
            cr.fill()
        cr.set_source_rgb(1.0, 0.0, 0.0)
        cr.set_line_width(2)
        cr.rectangle(1, 1, width - 2, height - 2)
        cr.stroke()

    def _greyscale_ramp(self, cr, width, height) -> None:
        """The full greyscale ramp: for judging tone and neutrality."""
        steps = 64
        step_width = width / steps
        for index in range(steps):
            level = index / (steps - 1)
            cr.set_source_rgb(level, level, level)
            cr.rectangle(index * step_width, 0, step_width + 1, height)
            cr.fill()

    def _colour_patches(self, cr, width, height) -> None:
        """Colour patches: for judging saturation and hue."""
        colours = [
            ("red", (0.75, 0.1, 0.1)),
            ("yellow", (0.75, 0.7, 0.1)),
            ("green", (0.15, 0.6, 0.15)),
            ("cyan", (0.15, 0.6, 0.6)),
            ("blue", (0.15, 0.15, 0.7)),
            ("magenta", (0.7, 0.15, 0.7)),
        ]
        columns = 3
        rows = 2
        cell_width = width / columns
        cell_height = height / rows
        for index, (_name, (red, green, blue)) in enumerate(colours):
            column = index % columns
            row = index // columns
            cr.set_source_rgb(red, green, blue)
            cr.rectangle(
                column * cell_width,
                row * cell_height,
                cell_width,
                cell_height,
            )
            cr.fill()

    def _primary_patches(self, cr, width, height) -> None:
        """Pure red, green and blue: for checking the channel mapping."""
        columns = 3
        cell_width = width / columns
        for index, (red, green, blue) in enumerate(
            [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)]
        ):
            cr.set_source_rgb(red, green, blue)
            cr.rectangle(index * cell_width, 0, cell_width, height)
            cr.fill()
