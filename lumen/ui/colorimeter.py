"""Native guided colorimeter setup, instrument prompts and measured review."""

from __future__ import annotations

import threading
import time
from dataclasses import replace
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from lumen.backends.colorimeter.argyll import ArgyllBackend, Cancellation, Cancelled, ProcessRunner, availability
from lumen.backends.colorimeter.colord import ColordBackend
from lumen.backends.displays import discover_displays
from lumen.engine.colorimeter import ColorimeterSession
from lumen.model.colorimeter import CalibrationTarget, DisplayTarget, Progress
from lumen.model.profile import load_profile
from lumen.model.settings import load_settings


class ColorimeterWindow(Adw.ApplicationWindow):
    def __init__(self, application, parent=None, on_finished=None):
        super().__init__(application=application, title="Colorimeter calibration")
        self.set_default_size(760, 850)
        if parent:
            self.set_transient_for(parent)
            self.set_modal(True)
        self._on_finished = on_finished
        self._closed = False
        self._running = False
        self._token = Cancellation()
        self._decision = threading.Event()
        self._command = None
        self._choices = []
        self._unpaired = []
        self._instruments = []
        self._argyll_displays = {}
        self._argyll_numbers = []
        self._type_pending = None
        self._settings = load_settings()
        self._icc_path = None
        self._patch_window = None
        self._patch_output = None
        self._cookie = None
        self.connect("close-request", self._close_request)
        header = Adw.HeaderBar()
        self._cancel = Gtk.Button(label="Cancel")
        self._cancel.connect("clicked", lambda _: self.close())
        header.pack_start(self._cancel)
        self._stack = Gtk.Stack()
        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(self._stack)
        self.set_content(view)
        self._build_setup()
        self._build_progress()
        self._build_review()
        self._refresh()

    def _page(self, name):
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        for side in ("top", "bottom", "start", "end"):
            getattr(content, f"set_margin_{side}")(24)
        scroll = Gtk.ScrolledWindow(vexpand=True)
        scroll.set_child(content)
        self._stack.add_named(scroll, name)
        return content

    def _label(self, text):
        return Gtk.Label(label=text, wrap=True, xalign=0)

    def _combo(self, box, title, labels):
        row = Adw.ComboRow(title=title, model=Gtk.StringList.new(labels))
        row.set_use_markup(False)
        box.add(row)
        return row

    def _spin(self, group, title, value, low, high, step=1, digits=0):
        row = Adw.SpinRow(title=title, adjustment=Gtk.Adjustment(value=value, lower=low,
                          upper=high, step_increment=step, page_increment=step * 10), digits=digits)
        group.add(row)
        return row

    def _build_setup(self):
        body = self._page("setup")
        title = self._label("Measure, calibrate, and profile your display")
        title.add_css_class("title-2")
        body.append(title)
        body.append(self._label("Use an X11 SDR session. Let the display warm up for about 30 minutes. "
                                "Disable HDR, dynamic contrast, automatic brightness, and Night Light. "
                                "Keep other color tools paused until calibration finishes."))
        self._status = self._label("Looking for displays and instruments…")
        body.append(self._status)
        self._refresh_button = Gtk.Button(label="Refresh instruments and displays", halign=Gtk.Align.START)
        self._refresh_button.connect("clicked", lambda _: self._refresh())
        body.append(self._refresh_button)
        devices = Adw.PreferencesGroup(title="Measurement equipment")
        body.append(devices)
        self._output = self._combo(devices, "Display", ["Discovering…"])
        self._output.connect("notify::selected", self._output_changed)
        self._ddc = self._combo(devices, "Monitor hardware controls", ["Automatic match / manual setup"])
        self._argyll_index = self._spin(devices, "Argyll display number", 1, 1, 64)
        self._instrument = self._combo(devices, "Instrument", ["Discovering…"])
        self._instrument.connect("notify::selected", lambda *_: self._types_changed())
        self._type = self._combo(devices, "Display technology", ["Instrument default"])
        self._type_values = [""]
        self._correction = Adw.EntryRow(title="Correction file (.ccss or .ccmx)", text=self._settings.colorimeter_correction)
        devices.add(self._correction)
        browse = Gtk.Button(label="Choose correction file…", halign=Gtk.Align.START)
        browse.connect("clicked", self._choose_correction)
        body.append(browse)
        targets = Adw.PreferencesGroup(title="Calibration targets")
        body.append(targets)
        self._white_values = ["D65", "D50", "native", "custom"]
        self._white = self._combo(targets, "White point", ["D65", "D50", "Native", "Custom daylight temperature"])
        self._white.set_selected(self._white_values.index(self._settings.colorimeter_white_point))
        self._temperature = self._spin(targets, "Custom temperature (K)", self._settings.colorimeter_temperature, 4000, 10000, 100)
        self._luminance = self._spin(targets, "White luminance (cd/m²)", self._settings.colorimeter_luminance, 50, 400)
        self._response = self._combo(targets, "Tone response", ["Gamma", "sRGB"])
        self._response.set_selected(0 if self._settings.colorimeter_response == "gamma" else 1)
        self._gamma = self._spin(targets, "Gamma", self._settings.colorimeter_gamma, 1.6, 2.6, 0.1, 1)
        self._mode = self._combo(targets, "Profiling detail", ["Quick · matrix ICC", "Detailed · 729 patches, LUT ICC"])
        self._mode.set_selected(0 if self._settings.colorimeter_mode == "quick" else 1)
        self._automatic = Adw.SwitchRow(title="Adjust DDC controls automatically", active=self._settings.colorimeter_auto_ddc)
        targets.add(self._automatic)
        self._ready = Gtk.CheckButton(label="I verified the display numbers and positioned the sensor over the white patch")
        self._ready.connect("toggled", lambda _: self._update_start())
        body.append(self._label("Verify the Argyll display number against the discovery list above. "
                                "Only pair hardware controls with a display you recognize. If uncertain, "
                                "leave hardware controls unpaired and use the monitor’s own menu."))
        body.append(self._ready)
        self._start = Gtk.Button(label="Start calibration", sensitive=False, halign=Gtk.Align.END)
        self._start.add_css_class("suggested-action")
        self._start.connect("clicked", self._start_run)
        body.append(self._start)

    def _build_progress(self):
        body = self._page("progress")
        self._stage = self._label("Preparing calibration")
        self._stage.add_css_class("title-2")
        body.append(self._stage)
        self._progress = Gtk.ProgressBar(show_text=True)
        body.append(self._progress)
        self._reading = self._label("")
        body.append(self._reading)
        self._details = self._label("")
        self._details.set_selectable(True)
        body.append(self._details)
        self._white_patch = Gtk.DrawingArea(content_width=300, content_height=300, hexpand=True, vexpand=True)
        self._white_patch.set_draw_func(lambda area, cr, width, height: (cr.set_source_rgb(1, 1, 1), cr.paint()))
        body.append(self._white_patch)
        body.append(self._label("Keep the sensor centered on the white patch during setup. "
                                "ArgyllCMS will display its own measurement patch window next. "
                                "Follow placement prompts before continuing."))

    def _build_review(self):
        body = self._page("review")
        title = self._label("Review measured results")
        title.add_css_class("title-2")
        body.append(title)
        self._report = self._label("")
        self._report.set_selectable(True)
        body.append(self._report)
        self._name = Adw.EntryRow(title="Calibration name", text="Colorimeter calibration")
        group = Adw.PreferencesGroup()
        group.add(self._name)
        body.append(group)
        self._keep = Gtk.CheckButton(label="Keep this calibration despite the reported target deviations")
        self._keep.connect("toggled", lambda _: self._review_sensitivity())
        body.append(self._keep)
        self._save_status = self._label("")
        body.append(self._save_status)
        actions = Gtk.Box(spacing=8)
        self._discard = Gtk.Button(label="Discard")
        self._discard.connect("clicked", lambda _: self.close())
        self._save_only = Gtk.Button(label="Save only")
        self._save_only.connect("clicked", lambda _: self._decide("save"))
        self._activate = Gtk.Button(label="Save and activate")
        if self._settings.colorimeter_activate_icc:
            self._activate.add_css_class("suggested-action")
        else:
            self._save_only.add_css_class("suggested-action")
        self._activate.connect("clicked", lambda _: self._decide("activate"))
        self._export = Gtk.Button(label="Export ICC…", sensitive=False)
        self._export.connect("clicked", self._export_icc)
        for button in (self._discard, self._save_only, self._activate, self._export):
            actions.append(button)
        body.append(actions)
        body.append(self._label("Save only stores the calibration and restores the previous display state. "
                                "You can apply the saved profile later. Manual monitor-menu changes "
                                "must be restored on the monitor. Verification describes this run, "
                                "not a certification of display accuracy."))

    def _idle(self, function, *args):
        def call():
            if not self._closed:
                function(*args)
            return GLib.SOURCE_REMOVE
        GLib.idle_add(call)

    def _refresh(self):
        if self._running:
            return
        self._refresh_button.set_sensitive(False)
        self._start.set_sensitive(False)
        self._status.set_label("Checking ArgyllCMS and connected equipment…")
        self._choices, self._instruments = [], []
        def discover():
            try:
                if not availability().available:
                    raise RuntimeError(availability().description)
                import os
                if os.environ.get("XDG_SESSION_TYPE") == "wayland":
                    raise RuntimeError("Colorimeter calibration requires an X11 session; XWayland cannot calibrate the compositor")
                choices, unpaired = discover_displays()
                choices = [choice for choice in choices if choice.output and choice.gamma_backend]
                backend = ArgyllBackend(ProcessRunner(self._token))
                instruments, displays = backend.discover([choice.output for choice in choices])
                self._idle(self._discovered, choices, unpaired, instruments, displays, backend.display_listing, backend.display_numbers)
            except Exception as error:
                self._idle(self._discovery_failed, str(error))
        threading.Thread(target=discover, daemon=True).start()

    def _discovery_failed(self, message):
        self._status.set_label(message + "\nInstall the distribution’s ArgyllCMS package and instrument udev rules, then refresh.")
        self._refresh_button.set_sensitive(True)

    def _discovered(self, choices, unpaired, instruments, displays, listing="", numbers=None):
        self._choices, self._unpaired, self._instruments = choices, unpaired, instruments
        self._argyll_displays = displays
        self._argyll_numbers = numbers or list(displays.values())
        self._output.set_model(Gtk.StringList.new([f"{c.monitor.model or c.output} · {c.output}" for c in choices] or ["No X11 gamma outputs"]))
        self._ddc.set_model(Gtk.StringList.new(["Automatic match / manual setup"] + [f"{m.model or m.identifier} · {m.identifier}" for m, _, _ in unpaired]))
        self._instrument.set_model(Gtk.StringList.new([i.name for i in instruments] or ["No accessible instrument"]))
        preferred = [i for i, instrument in enumerate(instruments) if instrument.identifier == self._settings.colorimeter_instrument]
        if len(preferred) == 1:
            self._instrument.set_selected(preferred[0])
        mapping = listing or "\n".join(f"Argyll display {number}: {output}" for output, number in displays.items())
        self._status.set_label((mapping or "Display names could not be matched automatically. Check ArgyllCMS’s display list before selecting a number.") +
                               ("\nNo instrument found. Check its USB permissions and reconnect it." if not instruments else ""))
        self._refresh_button.set_sensitive(True)
        self._output_changed()
        self._types_changed()
        self._update_start()

    def _output_changed(self, *_):
        index = self._output.get_selected()
        if index < len(self._choices):
            mapped = self._argyll_displays.get(self._choices[index].output)
            if mapped:
                self._argyll_index.set_value(mapped)
            self._ready.set_active(False)

    def _types_changed(self):
        index = self._instrument.get_selected()
        if index >= len(self._instruments) or self._running:
            return
        instrument = self._instruments[index]
        if self._type_pending == instrument:
            return
        self._type_pending = instrument
        self._start.set_sensitive(False)
        def discover():
            try:
                types = ArgyllBackend(ProcessRunner(self._token)).display_types(instrument)
                self._idle(self._set_types, instrument, types)
            except Exception as error:
                self._idle(self._types_failed, instrument, str(error))
        threading.Thread(target=discover, daemon=True).start()

    def _types_failed(self, instrument, message):
        if self._type_pending == instrument:
            self._type_pending = None
            self._status.set_label(message)
            self._update_start()

    def _set_types(self, instrument, types):
        index = self._instrument.get_selected()
        if index >= len(self._instruments) or self._instruments[index] != instrument:
            return
        self._type_pending = None
        self._type_values = [""] + [key for key, _ in types]
        self._type.set_model(Gtk.StringList.new(["Instrument default"] + [label for _, label in types]))
        if self._settings.colorimeter_display_type in self._type_values:
            self._type.set_selected(self._type_values.index(self._settings.colorimeter_display_type))
        self._update_start()

    def _update_start(self):
        if hasattr(self, "_start"):
            self._start.set_sensitive(bool(self._choices and self._instruments and self._ready.get_active()) and not self._running and self._type_pending is None)

    def _choose_correction(self, _button):
        dialog = Gtk.FileDialog(title="Choose instrument correction")
        def selected(dialog, result):
            try:
                file = dialog.open_finish(result)
                if file.get_path():
                    self._correction.set_text(file.get_path())
            except GLib.Error:
                pass
        dialog.open(self, None, selected)

    def _start_run(self, _button):
        try:
            target = CalibrationTarget(self._white_values[self._white.get_selected()], self._temperature.get_value(),
                                       self._luminance.get_value(), "gamma" if self._response.get_selected() == 0 else "srgb",
                                       self._gamma.get_value(), "quick" if self._mode.get_selected() == 0 else "detailed")
            choice = replace(self._choices[self._output.get_selected()])
            mapping = self._ddc.get_selected()
            if mapping:
                choice.monitor, choice.capabilities, choice.backend = self._unpaired[mapping - 1]
            output_descriptor = next(c.monitor for c in self._choices if c.output == choice.output)
            number = int(self._argyll_index.get_value())
            if number not in self._argyll_numbers:
                raise ValueError("Select an Argyll display number from the discovered list")
            display = DisplayTarget(choice.output, output_descriptor.edid_hash, number)
            instrument = self._instruments[self._instrument.get_selected()]
            display_type = self._type_values[self._type.get_selected()]
            correction = self._correction.get_text().strip() or None
            ArgyllBackend.instrument_args(instrument, display_type, correction)
        except Exception as error:
            self._status.set_label(str(error))
            return
        self._run_target = target
        self._patch_output = choice.output
        self._running = True
        self._stack.set_visible_child_name("progress")
        try:
            self._cookie = self.get_application().inhibit(self, Gtk.ApplicationInhibitFlags.IDLE | Gtk.ApplicationInhibitFlags.SUSPEND,
                                                         "Measuring display calibration")
        except Exception:
            self._cookie = None
        automatic = self._automatic.get_active()
        self._activate.set_label("Save and activate" if self._settings.colorimeter_activate_icc else "Activate ICC")
        def worker():
            try:
                colord, device = None, None
                try:
                    colord = ColordBackend()
                    device = colord.find_device(choice.output, display.edid_hash)
                except Exception:
                    pass
                backend = ArgyllBackend(ProcessRunner(self._token, self._event, self._prompt))
                session = ColorimeterSession(choice.applier(), backend, instrument, display, target,
                    display_type=display_type, correction=correction, automatic=automatic,
                    colord=colord, colord_device=device, token=self._token,
                    on_event=self._event, on_prompt=self._prompt, on_manual=self._manual,
                    prepare_patch=self._prepare_patch, finish_patch=self._hide_patch)
                with session:
                    result = session.run()
                    self._idle(self._show_result, result)
                    while True:
                        while not self._decision.wait(0.1):
                            self._token.check()
                        self._decision.clear()
                        self._token.check()
                        command, name = self._command
                        try:
                            path = session.save(name)
                            self._icc_path = session.profile.icc_path
                            if command == "activate":
                                session.activate()
                            break
                        except Exception as error:
                            self._idle(self._save_failed, str(error), session.saved_path)
                self._token.check()
                self._idle(self._saved, path, command)
            except Cancelled:
                self._idle(self._finished, None)
            except Exception as error:
                self._idle(self._finished, str(error))
            finally:
                self._idle(self._destroy_patch)
                self._idle(self._release_inhibitor)
        threading.Thread(target=worker, daemon=True).start()

    def _prepare_patch(self, token):
        ready = threading.Event()
        failure = []
        def shown():
            monitors = Gdk.Display.get_default().get_monitors()
            candidates = [monitors.get_item(i) for i in range(monitors.get_n_items())
                          if monitors.get_item(i).get_connector() == self._patch_output]
            if len(candidates) != 1:
                failure.append("Cannot identify the selected GTK display for the measurement patch")
                ready.set()
                return
            window = Gtk.Window(transient_for=self, decorated=False, title="Lumen measurement patch")
            area = Gtk.DrawingArea()
            area.set_draw_func(lambda area, cr, width, height: (cr.set_source_rgb(1, 1, 1), cr.paint()))
            window.set_child(area)
            keys = Gtk.EventControllerKey()
            keys.connect("key-pressed", lambda _, key, *_args: (self.close() or True) if key == Gdk.KEY_Escape else False)
            window.add_controller(keys)
            window.connect("close-request", lambda _: (self.close() or True))
            self._patch_window = window
            window.fullscreen_on_monitor(candidates[0])
            window.present()
            GLib.timeout_add(500, lambda: (ready.set(), GLib.SOURCE_REMOVE)[1])
        self._idle(shown)
        while not ready.wait(0.1):
            token.check()
        if failure:
            raise RuntimeError(failure[0])
        if not self._prompt("Place the instrument in the center of the white screen on the selected display. "
                            "Continue when ready. Press Escape during measurement to cancel.", token):
            raise Cancelled("Instrument placement cancelled")
        token.wait(0.5)

    def _destroy_patch(self):
        if self._patch_window:
            window, self._patch_window = self._patch_window, None
            window.destroy()
        self.present()

    def _hide_patch(self, token):
        ready = threading.Event()
        def hide():
            self._destroy_patch()
            ready.set()
        self._idle(hide)
        while not ready.wait(0.1):
            token.check()


    def _event(self, event: Progress):
        self._idle(self._show_event, event)

    def _show_event(self, event):
        if event.stage == "reading":
            self._reading.set_label(event.message)
        else:
            self._stage.set_label(event.stage.capitalize())
            self._details.set_label(event.message)
            if event.fraction is None:
                self._progress.pulse()
            else:
                self._progress.set_fraction(event.fraction)

    def _ask(self, message, token, manual=False):
        answered = threading.Event()
        answer = [False]
        def show():
            dialog = Adw.AlertDialog(heading="Adjust the monitor" if manual else "Instrument needs attention", body=message[-5000:])
            dialog.add_response("cancel", "Measure again" if manual else "Cancel")
            dialog.add_response("continue", "Continue")
            dialog.set_default_response("cancel")
            dialog.set_close_response("cancel")
            def response(_dialog, value):
                answer[0] = value == "continue"
                answered.set()
            dialog.connect("response", response)
            dialog.present(self._patch_window or self)
        self._idle(show)
        deadline = time.monotonic() + 600
        while not answered.wait(0.1):
            token.check()
            if time.monotonic() > deadline:
                raise RuntimeError("Instrument prompt timed out")
        token.wait(0.4)
        return answer[0]

    def _prompt(self, message, token):
        return self._ask(message, token)

    def _manual(self, reading, token):
        target = self._run_target
        xy = target.xy
        desired = f"Target: {target.luminance:.0f} cd/m²" + (f", x {xy[0]:.4f}, y {xy[1]:.4f}" if xy else ", native white")
        return self._ask(desired + "\n" + f"Measured white: {reading.y:.1f} cd/m², x {reading.xy[0]:.4f}, y {reading.xy[1]:.4f}.\n"
                         "Adjust brightness and RGB gains in the monitor’s menu. Choose Measure again "
                         "to update the reading, or Continue for automatic gamma calibration. "
                         "Lumen cannot restore settings changed through the monitor menu.", token, manual=True)

    def _show_result(self, result):
        self._warnings = result.warnings
        self._keep.set_visible(bool(result.warnings))
        r = result.report
        self._report.set_label(f"White: {r['white_luminance']:.1f} cd/m² · x {r['white_xy'][0]:.4f}, y {r['white_xy'][1]:.4f}\n"
            f"Black: {r['black_luminance']:.4f} cd/m²\nWhite point Δu′v′: {r['white_uv_distance']:.4f}\n"
            f"Grayscale luminance error: {r.get('grayscale_max_error_percent', 0):.2f}% of white (maximum)\n"
            f"ICC prediction error · average ΔE00 {r['average_delta_e00']:.2f} · maximum {r['maximum_delta_e00']:.2f}\n\n" +
            ("\n".join(result.warnings) if result.warnings else "Measured white and ICC errors meet the review thresholds."))
        self._stack.set_visible_child_name("review")
        self._review_sensitivity()
        (self._keep if result.warnings else self._activate).grab_focus()
        self._report.select_region(0, 0)

    def _review_sensitivity(self):
        enabled = not getattr(self, "_warnings", []) or self._keep.get_active()
        self._save_only.set_sensitive(enabled)
        self._activate.set_sensitive(enabled)

    def _decide(self, command):
        self._save_only.set_sensitive(False)
        self._activate.set_sensitive(False)
        self._command = (command, self._name.get_text())
        self._decision.set()

    def _save_failed(self, error, path):
        self._save_status.set_label(error + (f"\nSaved: {path}" if path else ""))
        self._activate.set_label("Retry activation")
        self._export.set_sensitive(self._icc_path is not None)
        self._review_sensitivity()

    def _saved(self, path, command):
        self._running = False
        self._release_inhibitor()
        if self._on_finished:
            self._on_finished(load_profile(path))
        self._save_status.set_label(f"Saved: {path}\n" + ("ICC profile activated." if command == "activate" else "Previous display settings restored."))
        self._save_only.set_sensitive(False)
        self._activate.set_sensitive(False)
        self._export.set_sensitive(True)
        self._discard.set_label("Close")
        self._cancel.set_label("Close")

    def _finished(self, error):
        self._running = False
        self._destroy_patch()
        self._release_inhibitor()
        if error:
            self._cancel.set_sensitive(True)
            self._cancel.set_label("Cancel")
            self._stack.set_visible_child_name("setup")
            self._status.set_label(error)
            self._token = Cancellation()
            self._update_start()
        else:
            self.close()

    def _release_inhibitor(self):
        if self._cookie:
            self.get_application().uninhibit(self._cookie)
            self._cookie = None

    def _export_icc(self, _button):
        if not self._icc_path:
            return
        dialog = Gtk.FileDialog(title="Export ICC profile", initial_name="lumen-display.icc")
        def selected(dialog, result):
            try:
                target = dialog.save_finish(result)
                Gio.File.new_for_path(self._icc_path).copy_async(target, Gio.FileCopyFlags.OVERWRITE,
                    GLib.PRIORITY_DEFAULT, None, None, None, finished)
            except GLib.Error as error:
                self._save_status.set_label(str(error))
        def finished(source, result):
            try:
                source.copy_finish(result)
            except GLib.Error as error:
                self._save_status.set_label(str(error))
        dialog.save(self, None, selected)

    def _close_request(self, _window):
        if self._running:
            self._cancel.set_sensitive(False)
            self._cancel.set_label("Restoring…")
            self._token.cancel()
            return True
        self._closed = True
        self._token.cancel()
        self._release_inhibitor()
        return False
