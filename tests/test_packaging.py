"""What the frozen exe is allowed to depend on.

A user's WindowPet.exe died on startup with "Importing the numpy C-extensions
failed". numpy was only ever used for 3x3 affine maths, so it was removed from the
runtime; these tests keep it out. They import the runtime modules in a subprocess
with numpy hard-blocked, which is the closest thing to a machine where numpy will
not load.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

_BLOCK_NUMPY = """
import sys
from importlib.abc import MetaPathFinder

class _Blocked(MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name == "numpy" or name.startswith("numpy."):
            raise ImportError("numpy is unavailable on this machine (simulated)")
        return None

sys.meta_path.insert(0, _Blocked())
"""


def _run(body: str) -> subprocess.CompletedProcess:
    # dedent the body on its own: the prelude is already flush left, so dedenting
    # the concatenation would find a common prefix of "" and leave the body indented
    return subprocess.run(
        [sys.executable, "-c", _BLOCK_NUMPY + textwrap.dedent(body)],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
        env={"PATH": "/usr/bin:/bin", "QT_QPA_PLATFORM": "offscreen",
             "PYTHONPATH": str(ROOT), "HOME": "/tmp"},
    )


def test_the_blocker_itself_works():
    """Guards the other tests: if numpy imported anyway they would prove nothing."""
    r = _run("""
        try:
            import numpy
        except ImportError:
            print("blocked")
        else:
            print("NOT BLOCKED")
    """)
    assert "blocked" in r.stdout, r.stderr
    assert "NOT BLOCKED" not in r.stdout


def test_core_runtime_imports_without_numpy():
    r = _run("""
        import pet.rigmath, pet.kinematics, pet.poses, pet.behavior, pet.desktop, pet.config
        print("ok")
    """)
    assert r.returncode == 0, r.stderr
    assert "ok" in r.stdout


def test_the_rig_can_be_posed_without_numpy():
    r = _run("""
        from pet.rigmath import Rig
        from pet.kinematics import Skeleton
        from pet.poses import CLIPS
        rig = Rig.load("assets/rig.json")
        skel = Skeleton(rig)
        pose = skel.resolve(CLIPS["hiphop"].at(0.3))
        tf = rig.compose(pose, (100.0, 400.0), 0.25, flip=True)
        print(len(tf), round(rig.bounds(tf)[3], 2))
    """)
    assert r.returncode == 0, r.stderr
    assert r.stdout.split()[0] == "15"


def test_a_whole_simulated_session_runs_without_numpy():
    r = _run("""
        import random
        from pet.behavior import Behavior, Pet
        from pet.desktop import default_fake
        b = Behavior(Pet(x=400.0, y=80.0, airborne=True), rng=random.Random(3))
        d = default_fake()
        for _ in range(60 * 30):
            b.update(d.snapshot(), 1/60)
        print("ok", round(b.pet.x), round(b.pet.y))
    """)
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith("ok")


@pytest.mark.skipif(
    __import__("importlib").util.find_spec("PySide6") is None,
    reason="PySide6 not installed",
)
def test_the_qt_window_renders_without_numpy():
    """qtapp is where numpy was imported, so this is the one that mattered."""
    r = _run("""
        from PySide6.QtWidgets import QApplication
        from pet.qtapp import PetWindow
        from pet.rigmath import Rig
        from pet import config as C
        app = QApplication([])
        w = PetWindow(Rig.load("assets/rig.json"), C.Settings(pet_height=200))
        w.behavior.pet.x, w.behavior.pet.y = 500.0, 700.0
        w.behavior.pet.airborne = False
        w._render()
        assert w._frame is not None and w._frame.width() > 0
        print("rendered", w._frame.width(), w._frame.height())
    """)
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith("rendered")


def test_no_runtime_module_imports_numpy():
    """Static backstop, in case a future edit reaches for it again.

    Matches import statements only - rigmath's docstring mentions numpy to explain
    why it does not use it.
    """
    import re

    pattern = re.compile(r"^\s*(?:import\s+numpy|from\s+numpy\b)", re.MULTILINE)
    offenders = [
        str(p.relative_to(ROOT))
        for p in (ROOT / "pet").rglob("*.py")
        if pattern.search(p.read_text())
    ]
    assert not offenders, f"numpy is back in the runtime: {offenders}"
