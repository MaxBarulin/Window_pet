"""Reading the desktop as terrain.

The pet treats the desktop as a platformer level:

* every monitor's work area is a room, its bottom edge (just above the taskbar)
  is the floor and its left/right edges are walls;
* every visible window's **top edge** is a ledge he can land on, walk along, sit
  on and dance on.

A window's top edge only counts where nothing above it in the z-order covers it,
so he cannot stand on a ledge that is not actually visible.

`Win32Desktop` is the real implementation. `FakeDesktop` takes explicit rectangles
so the behaviour layer can be tested anywhere, including on this Linux box.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field

# A window has to be at least this wide/tall to count as terrain; anything
# smaller is a tooltip or a stray popup and makes for jittery platforms.
MIN_WINDOW_W = 120
MIN_WINDOW_H = 60

# Ledges thinner than this are not worth trying to stand on.
MIN_LEDGE_W = 48


@dataclass(frozen=True)
class Rect:
    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def w(self) -> int:
        return self.x1 - self.x0

    @property
    def h(self) -> int:
        return self.y1 - self.y0

    def contains_x(self, x: float) -> bool:
        return self.x0 <= x <= self.x1

    def contains(self, x: float, y: float) -> bool:
        return self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1


@dataclass(frozen=True)
class Monitor:
    bounds: Rect
    work: Rect  # excludes the taskbar


@dataclass(frozen=True)
class Ledge:
    """A walkable horizontal surface."""

    y: int
    x0: int
    x1: int
    kind: str  # "floor" | "window"

    @property
    def w(self) -> int:
        return self.x1 - self.x0

    def contains_x(self, x: float) -> bool:
        return self.x0 <= x <= self.x1


def subtract_intervals(
    span: tuple[int, int], cuts: list[tuple[int, int]]
) -> list[tuple[int, int]]:
    """span minus every cut, as a list of surviving pieces."""
    pieces = [span]
    for cx0, cx1 in cuts:
        nxt: list[tuple[int, int]] = []
        for px0, px1 in pieces:
            if cx1 <= px0 or cx0 >= px1:  # no overlap
                nxt.append((px0, px1))
                continue
            if cx0 > px0:
                nxt.append((px0, min(cx0, px1)))
            if cx1 < px1:
                nxt.append((max(cx1, px0), px1))
        pieces = nxt
    return [p for p in pieces if p[1] > p[0]]


@dataclass
class Snapshot:
    """One reading of the desktop."""

    monitors: list[Monitor]
    windows: list[Rect] = field(default_factory=list)  # topmost first
    _ledges: list[Ledge] | None = field(default=None, repr=False, compare=False)

    @property
    def ledges(self) -> list[Ledge]:
        if self._ledges is None:
            self._ledges = self._build_ledges()
        return self._ledges

    def _build_ledges(self) -> list[Ledge]:
        out: list[Ledge] = []
        for m in self.monitors:
            out.append(Ledge(m.work.y1, m.work.x0, m.work.x1, "floor"))

        for i, win in enumerate(self.windows):
            if win.w < MIN_WINDOW_W or win.h < MIN_WINDOW_H:
                continue
            # anything higher in the z-order that overlaps this top edge hides it
            cuts = [
                (o.x0, o.x1)
                for o in self.windows[:i]
                if o.y0 <= win.y0 <= o.y1 and o.x1 > win.x0 and o.x0 < win.x1
            ]
            for x0, x1 in subtract_intervals((win.x0, win.x1), cuts):
                if x1 - x0 >= MIN_LEDGE_W:
                    out.append(Ledge(win.y0, x0, x1, "window"))
        return out

    # -- queries used by the behaviour layer -------------------------------

    def monitor_at(self, x: float, y: float) -> Monitor | None:
        for m in self.monitors:
            if m.work.contains(x, y):
                return m
        for m in self.monitors:  # fall back to nearest by x
            if m.work.contains_x(x):
                return m
        return self.monitors[0] if self.monitors else None

    def ledge_below(self, x: float, y: float) -> Ledge | None:
        """The surface he would land on falling from (x, y): highest one below."""
        best: Ledge | None = None
        for lg in self.ledges:
            if lg.contains_x(x) and lg.y >= y - 1e-6:
                if best is None or lg.y < best.y:
                    best = lg
        return best

    def ledge_under_feet(self, x: float, y: float, tol: float = 3.0) -> Ledge | None:
        """The surface he is currently standing on, if any."""
        best: Ledge | None = None
        for lg in self.ledges:
            if lg.contains_x(x) and abs(lg.y - y) <= tol:
                if best is None or lg.y < best.y:
                    best = lg
        return best

    def bounds_for(self, x: float, y: float) -> Rect:
        m = self.monitor_at(x, y)
        if m is None:
            return Rect(0, 0, 1920, 1080)
        return m.work

    def walk_span(self, x: float, y: float) -> tuple[int, int]:
        """How far he can walk left/right from here before hitting a real wall.

        Adjoining screens whose floors line up are one continuous room, so the
        seam between two monitors is not a wall - he should walk straight across.
        """
        m = self.monitor_at(x, y)
        if m is None:
            b = self.desktop_bounds()
            return b.x0, b.x1
        lo, hi = m.work.x0, m.work.x1
        changed = True
        while changed:
            changed = False
            for o in self.monitors:
                if o.work.y1 != m.work.y1:
                    continue  # different floor height: not the same room
                if o.work.x0 <= hi <= o.work.x1 and o.work.x1 > hi:
                    hi, changed = o.work.x1, True
                if o.work.x0 <= lo <= o.work.x1 and o.work.x0 < lo:
                    lo, changed = o.work.x0, True
        return lo, hi

    def desktop_bounds(self) -> Rect:
        if not self.monitors:
            return Rect(0, 0, 1920, 1080)
        return Rect(
            min(m.work.x0 for m in self.monitors),
            min(m.work.y0 for m in self.monitors),
            max(m.work.x1 for m in self.monitors),
            max(m.work.y1 for m in self.monitors),
        )


class FakeDesktop:
    """Scriptable desktop for tests and for running on non-Windows hosts."""

    def __init__(self, monitors: list[Monitor], windows: list[Rect] | None = None):
        self.monitors = monitors
        self.windows = windows or []

    def snapshot(self) -> Snapshot:
        return Snapshot(list(self.monitors), list(self.windows))


def default_fake() -> FakeDesktop:
    work = Rect(0, 0, 1920, 1040)
    return FakeDesktop(
        [Monitor(Rect(0, 0, 1920, 1080), work)],
        [Rect(300, 420, 900, 900), Rect(1100, 300, 1700, 780)],
    )


# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------

class Win32Desktop:
    """Enumerates real monitors and windows through user32/dwmapi."""

    # GetWindowLong / DWM constants
    GWL_EXSTYLE = -20
    GWL_STYLE = -16
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_NOREDIRECTIONBITMAP = 0x00200000
    WS_CHILD = 0x40000000
    DWMWA_CLOAKED = 14
    DWMWA_EXTENDED_FRAME_BOUNDS = 9

    def __init__(self, exclude_hwnds: set[int] | None = None):
        if sys.platform != "win32":
            raise RuntimeError("Win32Desktop requires Windows")
        import ctypes
        from ctypes import wintypes

        self.ctypes = ctypes
        self.wintypes = wintypes
        self.user32 = ctypes.windll.user32
        self.dwmapi = ctypes.windll.dwmapi
        try:
            # so window rects come back in real pixels on scaled displays
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                self.user32.SetProcessDPIAware()
            except Exception:
                pass
        self.exclude: set[int] = set(exclude_hwnds or ())

    def exclude_hwnd(self, hwnd: int) -> None:
        self.exclude.add(int(hwnd))

    def _monitors(self) -> list[Monitor]:
        ctypes, wintypes = self.ctypes, self.wintypes

        class MONITORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD),
            ]

        found: list[Monitor] = []
        proto = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
            ctypes.POINTER(wintypes.RECT), wintypes.LPARAM,
        )

        def cb(hmon, hdc, lprc, lparam):
            mi = MONITORINFO()
            mi.cbSize = ctypes.sizeof(MONITORINFO)
            if self.user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
                b, w = mi.rcMonitor, mi.rcWork
                found.append(
                    Monitor(
                        Rect(b.left, b.top, b.right, b.bottom),
                        Rect(w.left, w.top, w.right, w.bottom),
                    )
                )
            return True

        self.user32.EnumDisplayMonitors(None, None, proto(cb), 0)
        if not found:
            w = self.user32.GetSystemMetrics(0)
            h = self.user32.GetSystemMetrics(1)
            found.append(Monitor(Rect(0, 0, w, h), Rect(0, 0, w, h)))
        return found

    def _window_rect(self, hwnd) -> Rect | None:
        ctypes, wintypes = self.ctypes, self.wintypes
        r = wintypes.RECT()
        # extended frame bounds excludes the invisible resize border/shadow, so
        # the ledge lines up with what the user actually sees
        hr = self.dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd), ctypes.c_uint(self.DWMWA_EXTENDED_FRAME_BOUNDS),
            ctypes.byref(r), ctypes.sizeof(r),
        )
        if hr != 0 and not self.user32.GetWindowRect(hwnd, ctypes.byref(r)):
            return None
        return Rect(r.left, r.top, r.right, r.bottom)

    def _is_cloaked(self, hwnd) -> bool:
        ctypes, wintypes = self.ctypes, self.wintypes
        val = ctypes.c_int(0)
        hr = self.dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd), ctypes.c_uint(self.DWMWA_CLOAKED),
            ctypes.byref(val), ctypes.sizeof(val),
        )
        return hr == 0 and val.value != 0

    def _windows(self) -> list[Rect]:
        ctypes, wintypes = self.ctypes, self.wintypes
        out: list[Rect] = []
        proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def cb(hwnd, lparam):
            h = int(hwnd)
            if h in self.exclude:
                return True
            if not self.user32.IsWindowVisible(hwnd) or self.user32.IsIconic(hwnd):
                return True
            style = self.user32.GetWindowLongW(hwnd, self.GWL_STYLE)
            if style & self.WS_CHILD:
                return True
            ex = self.user32.GetWindowLongW(hwnd, self.GWL_EXSTYLE)
            if ex & self.WS_EX_TOOLWINDOW:
                return True
            if self.user32.GetWindowTextLengthW(hwnd) == 0:
                return True
            if self._is_cloaked(hwnd):
                return True
            rect = self._window_rect(h)
            if rect is None or rect.w < MIN_WINDOW_W or rect.h < MIN_WINDOW_H:
                return True
            out.append(rect)
            return True

        # EnumWindows walks top-of-z-order first, which is the order ledges need
        self.user32.EnumWindows(proto(cb), 0)
        return out

    def snapshot(self) -> Snapshot:
        return Snapshot(self._monitors(), self._windows())


def make_desktop(exclude_hwnds: set[int] | None = None):
    """Real desktop on Windows, a scripted stand-in everywhere else."""
    if sys.platform == "win32":
        return Win32Desktop(exclude_hwnds)
    return default_fake()
