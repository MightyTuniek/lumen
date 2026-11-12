import pytest

pytest.importorskip("gi")
from lumen.ui.app import LumenApplication

pytestmark = pytest.mark.usefixtures("gtk_display")


def test_startup_error_has_a_window_and_can_be_closed():
    app = LumenApplication()
    app.register(None)
    try:
        app._show_error("No display could be reached", "No backend is available")
        window = app.get_active_window()
        assert window is not None
        assert window.get_visible()
        dialog = window.get_visible_dialog()
        assert dialog.get_heading() == "No display could be reached"
        dialog.emit("response", "close")
        assert not app.get_windows()
    finally:
        for window in app.get_windows():
            window.destroy()
