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

from gi.repository import Adw, Gtk  # noqa: E402

from lumen.engine.apply import ProfileApplier  # noqa: E402
from lumen.engine.calibration import CalibrationSession  # noqa: E402
from lumen.model.profile import GammaCurve, new_profile  # noqa: E402

from .widgets import ControlList, GroupHeader, PatternArea  # noqa: E402


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
        self._snapshot = applier.capture()
        self._dirty = False

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
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        controls = self._session.controls_for(step)
        if controls:
            box.append(GroupHeader("Controls"))
            box.append(ControlList(controls, self._on_control_changed))
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
        if self._dirty:
            self._applier.restore(self._snapshot)
        self.close()

    def _finish(self) -> None:
        """Save the calibration and close."""
        monitor = self._applier.monitor
        values = self._applier.current_values()
        profile = new_profile(
            name="Calibration",
            monitor=monitor,
            ddc=values,
            gamma=GammaCurve(),
        )
        if self._on_finished is not None:
            self._on_finished(profile)
        self.close()
