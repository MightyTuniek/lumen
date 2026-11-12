"""Portable cancellation shared by measurement workers and adapters."""

from __future__ import annotations

import threading


class Cancelled(RuntimeError):
    """The user cancelled a measurement transaction."""


class Cancellation:
    def __init__(self):
        self.event = threading.Event()

    def cancel(self):
        self.event.set()

    def check(self):
        if self.event.is_set():
            raise Cancelled("Calibration cancelled")

    def wait(self, seconds: float):
        if self.event.wait(seconds):
            self.check()
