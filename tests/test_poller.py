"""The desktop poller runs off the GUI thread.

Reading the desktop means EnumWindows plus a DWM call per window. Doing that on
the GUI thread a few times a second hitched the animation on every poll - the
"stutter" this fixes.
"""

from __future__ import annotations

import threading
import time

import pytest

from pet.desktop import FakeDesktop, Monitor, Rect, Snapshot
from pet.qtapp import DesktopPoller


class SlowDesktop:
    """Stands in for a machine with a lot of windows open."""

    def __init__(self, delay: float = 0.05):
        self.delay = delay
        self.calls = 0
        self.threads: set[int] = set()
        self._lock = threading.Lock()

    def snapshot(self) -> Snapshot:
        time.sleep(self.delay)
        with self._lock:
            self.calls += 1
            self.threads.add(threading.get_ident())
        return Snapshot([Monitor(Rect(0, 0, 800, 600), Rect(0, 0, 800, 560))])


def test_first_snapshot_is_available_immediately():
    poller = DesktopPoller(FakeDesktop([Monitor(Rect(0, 0, 800, 600), Rect(0, 0, 800, 560))]), 0.01)
    assert poller.latest() is not None
    poller.stop()


def test_latest_never_blocks_once_running():
    slow = SlowDesktop(delay=0.08)
    poller = DesktopPoller(slow, 0.01)   # constructor pays for the first one
    poller.start()
    try:
        time.sleep(0.05)
        for _ in range(50):              # 50 reads must not cost 50 snapshots
            start = time.perf_counter()
            assert poller.latest() is not None
            assert time.perf_counter() - start < 0.02
    finally:
        poller.stop()


def test_polling_happens_on_a_background_thread():
    slow = SlowDesktop(delay=0.01)
    poller = DesktopPoller(slow, 0.01)
    main = threading.get_ident()
    poller.start()
    try:
        deadline = time.time() + 3.0
        while slow.calls < 3 and time.time() < deadline:
            time.sleep(0.01)
        assert slow.calls >= 3, "poller never ran"
        assert slow.threads - {main}, "snapshots were taken on the calling thread"
    finally:
        poller.stop()


def test_snapshots_actually_refresh():
    fake = FakeDesktop([Monitor(Rect(0, 0, 800, 600), Rect(0, 0, 800, 560))])
    poller = DesktopPoller(fake, 0.01)
    poller.start()
    try:
        assert poller.latest().windows == []
        fake.windows.append(Rect(10, 100, 400, 500))
        deadline = time.time() + 3.0
        while not poller.latest().windows and time.time() < deadline:
            time.sleep(0.01)
        assert poller.latest().windows, "poller never picked up the new window"
    finally:
        poller.stop()


def test_a_failing_snapshot_keeps_the_last_good_one():
    """Windows vanish mid-enumeration; that must not blank the terrain."""

    class Flaky:
        def __init__(self):
            self.n = 0

        def snapshot(self):
            self.n += 1
            if self.n > 1:
                raise OSError("window went away")
            return Snapshot([Monitor(Rect(0, 0, 800, 600), Rect(0, 0, 800, 560))])

    poller = DesktopPoller(Flaky(), 0.01)
    poller.start()
    try:
        time.sleep(0.1)
        assert poller.latest() is not None
        assert poller.latest().monitors
    finally:
        poller.stop()


def test_stop_ends_the_thread():
    poller = DesktopPoller(SlowDesktop(delay=0.01), 0.01)
    poller.start()
    time.sleep(0.05)
    poller.stop()
    poller._thread.join(timeout=3.0)
    assert not poller._thread.is_alive()


def test_start_is_idempotent():
    poller = DesktopPoller(SlowDesktop(delay=0.01), 0.05)
    poller.start()
    first = poller._thread
    poller.start()
    try:
        assert poller._thread is first
    finally:
        poller.stop()
