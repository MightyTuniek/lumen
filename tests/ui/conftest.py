"""Only construct GTK widgets when a display is available."""

import pytest


@pytest.fixture
def gtk_display():
    gi = pytest.importorskip("gi")
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gdk, Gtk

    Gtk.init()
    if Gdk.Display.get_default() is None:
        pytest.skip("GTK needs a display")
    Adw.init()
