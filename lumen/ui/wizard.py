"""The calibration wizard window.

The wizard walks the user through the steps that make sense for their
monitor. Each step shows a test pattern, explains what to look for, and
exposes the controls for that step.

Cancelling restores everything: a snapshot is taken when the wizard
opens, and it is put back if the user backs out.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk  # noqa: E402

from lumen.engine.apply import ProfileApplier  # noqa: E402
from lumen.engine.calibration import CalibrationSession  # noqa: E402
from lumen.model.profile import GammaCurve, new_profile, save_profile  # noqa: E402

from .widgets import (  # noqa: E402
    ControlList,
    GroupHeader,
    PatternArea,
    read_controls,
)


class WizardWindow(Adw.ApplicationWindow):
    """The guided calibration flow."""

    def __init__(
        self,
        application,
        session: CalibrationSession,
        applier: ProfileApplier,
        on_finished=None,
    ) -> None:
        super().__init__(application=application, title="Lumen calibration")
        self.set_default_size(1000, 720)
        self._session = session
        self._applier = applier
        self._on_finished = on_finished
        # The snapshot reads every control over the bus, which takes about
        # a second. It is captured in the background so the wizard opens
        # at once; cancelling before it finishes restores what is known.
        self._snapshot = None
        self._dirty = False
        self._sliders = []

        self._header = Adw.HeaderBar()
        self._header.set_title_widget(
            Adw.WindowTitle(title="Calibration", subtitle="")
        )
        self._cancel_button = Gtk.Button(label="Cancel")
        self._cancel_button.connect("clicked", self._on_cancel)
        self._header.pack_start(self._cancel_button)

        self._back_button = Gtk.Button(label="Back")
        self._back_button.connect("clicked", self._on_back)
        self._header.pack_start(self._back_button)

        self._next_button = Gtk.Button(label="Next")
        self._next_button.add_css_class("suggested-action")
        self._next_button.connect("clicked", self._on_next)
        self._header.pack_end(self._next_button)

        self._pattern = PatternArea(session.current_step.pattern)
        self._pattern.set_size_request(640, 320)

        self._title = GroupHeader("")
        self._instruction = Gtk.Label(label="")
        self._instruction.set_wrap(True)
        self._instruction.set_xalign(0.0)

        self._controls_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        content.set_margin_start(12)
        content.set_margin_end(12)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        top.append(self._pattern)
        self._controls_scroll = Gtk.ScrolledWindow()
        self._controls_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._controls_scroll.set_child(self._controls_box)
        self._controls_scroll.set_hexpand(True)
        top.append(self._controls_scroll)
        content.append(top)

        content.append(self._title)
        content.append(self._instruction)

        # A ToolbarView is what carries a header bar in libadwaita.
        view = Adw.ToolbarView()
        view.add_top_bar(self._header)
        view.set_content(content)
        self.set_content(view)
        self._show_current_step()

        # Reading every control over the bus takes about a second, so it
        # happens once in the background. The values populate the
        # controls' caches, which the sliders and the rollback snapshot
        # then read from - rather than each making its own trip.
        read_controls(self._session.controls, self._on_controls_ready)

    def _on_controls_ready(self) -> None:
        """Called once the controls have their values.

        This arrives on a worker thread, so the work is moved onto the
        main thread before anything is touched.
        """
        GLib.idle_add(self._on_controls_ready_main)

    def _on_controls_ready_main(self) -> bool:
        # The controls now know their values, so the rollback snapshot
        # can be built from them without another trip over the bus.
        self._snapshot = self._applier.snapshot_from(self._session.controls)
        for slider in self._sliders:
            slider.refresh()
        return False

    # -- navigation ---------------------------------------------------------

    def _show_current_step(self) -> None:
        step = self._session.current_step
        self._pattern.set_pattern(step.pattern)
        self._title.set_label(step.title)
        self._instruction.set_label(step.instruction)
        self._header.set_title_widget(
            Adw.WindowTitle(
                title="Calibration",
                subtitle=f"Step {self._session.steps.index(step) + 1} of "
                f"{len(self._session.steps)}",
            )
        )

        self._rebuild_controls(step)

        self._back_button.set_sensitive(self._session.current_step is not None)
        last = self._session.current_step is self._session.steps[-1]
        self._next_button.set_label("Finish" if last else "Next")

    def _rebuild_controls(self, step) -> None:
        """Show the controls for a step.

        The controls are built once and reused, because building them
        means reading the monitor, and each DDC/CI read costs about fifty
        milliseconds. Rebuilding them on every step made the wizard feel
        sluggish.
        """
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        controls = self._session.controls_for(step)
        self._sliders = []
        if controls:
            box.append(GroupHeader("Controls"))
            control_list = ControlList(controls, self._on_control_changed)
            self._sliders = control_list.sliders
            box.append(control_list)
        self._controls_box = box
        self._controls_scroll.set_child(box)

    def _on_control_changed(self, control, value) -> None:
        try:
            control.write(value)
        except Exception as error:
            self._report(error)
            return
        self._dirty = True

    def _report(self, error) -> None:
        dialog = Adw.AlertDialog(
            heading="That change did not apply",
            body=str(error),
        )
        dialog.add_response("close", "Close")
        dialog.present(self)

    # -- actions ------------------------------------------------------------

    def _on_next(self) -> None:
        if self._session.finished:
            self._finish()
            return
        self._session.advance()
        if self._session.finished:
            self._finish()
            return
        self._show_current_step()

    def _on_back(self) -> None:
        self._session.back()
        self._show_current_step()

    def _on_cancel(self) -> None:
        if self._dirty and self._snapshot is not None:
            self._applier.restore(self._snapshot)
        self.close()

    def _finish(self) -> None:
        """Save the calibration and close.

        The values are taken from the controls, which already know what
        they are, rather than read from the bus again.
        """
        monitor = self._applier.monitor
        values = self._applier.current_values(self._session.controls)
        profile = new_profile(
            name="Calibration",
            monitor=monitor,
            ddc=values,
            gamma=GammaCurve(),
        )
        # The profile is saved here rather than left to the caller, so
        # the wizard always leaves a calibration behind.
        try:
            path = save_profile(profile)
        except OSError as error:
            self._report(error)
            return
        if self._on_finished is not None:
            self._on_finished(profile)
        self.close()
