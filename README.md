# Window Pet

A desktop companion for Windows, rigged from a single photo. He walks around the
desktop, breaks into dances, and treats your actual windows as scenery — landing on
title bars, walking along them, sitting on the edge to dance, leaning on the screen
edge, turning back at obstacles and falling when the window under him closes.

He is one always-on-top transparent overlay with no window frame and no taskbar
button. Clicks on empty space around him pass straight through to whatever is
underneath.

```
python -m pet
```

## Getting the .exe

Every push builds on `windows-latest` and publishes two artifacts:

| Artifact | What it is |
| --- | --- |
| **WindowPet-exe** | One file, `WindowPet.exe`. Start here. |
| **WindowPet-folder** | A folder containing `WindowPet.exe` plus its DLLs. The fallback. |

1. Open the **Actions** tab → the newest **build** run.
2. Download an artifact from the bottom of the page.
3. Unzip and run `WindowPet.exe`. No Python needed.

Use the one-folder build if the one-file build will not start. A one-file exe
unpacks itself into `%TEMP%` on every launch, and antivirus or a locked-down temp
directory can interfere with the extracted DLLs; the one-folder build has nothing
to unpack.

Both are smoke-tested in CI — actually launched and left running for 20 seconds —
and the build refuses to publish an exe that is missing its assets. Nothing is
cross-compiled: PyInstaller cannot do that, so these come from a real Windows
runner.

### If it will not start

The exe is built without a console, so Windows shows only a truncated dialog. The
full traceback is written to:

```
%APPDATA%\WindowPet\crash.log
```

Send that file — it names the actual failure.

## Controls

| Action | What happens |
| --- | --- |
| Drag him | Picks him up; let go to throw. He falls and lands. |
| Double-click | Dance on demand. |
| Right-click (or tray icon) | Menu: dance, pause, size, click-through, re-centre, quit. |
| Tray icon, single click | Dance on demand. |

Settings persist to `%APPDATA%\WindowPet\settings.json`.

## How he reads your desktop

`pet/desktop.py` turns the desktop into platformer terrain:

* **Floors and walls** come from each monitor's *work area*, so he stands on top of
  the taskbar rather than behind it, and walks across a multi-monitor setup as one
  continuous room when the screens' floors line up.
* **Ledges** are the top edges of visible top-level windows, found with
  `EnumWindows`. Minimised, cloaked (suspended UWP), child, tool and untitled
  windows are skipped, and geometry comes from `DwmGetWindowAttribute`
  (`EXTENDED_FRAME_BOUNDS`) so a ledge lines up with the window you actually see
  rather than its invisible resize border.
* A ledge only exists **where nothing covers it**. `EnumWindows` returns windows
  top-of-z-order first, so each title bar has the windows above it subtracted from
  it; a title bar hidden behind another window is not standable, and a partly
  covered one leaves only its exposed pieces.
* The desktop is re-read a few times a second, so when a window moves, closes,
  minimises or gets covered, the ledge under him disappears and he falls.

`pet/behavior.py` decides what to do with that: walk, dance, sit-dance on a window
edge, lean on a screen edge, wave, idle, hop up onto a low window lip, turn back at
a tall one, and recover if he ever ends up below everything.

## How the character is rigged

The photo is a T-pose, which is close to ideal: nothing is occluded, both hands are
visible, and the arms are clear of the body, so no part of him has to be invented.

```
assets/source.png ──tools/cutout.py──▶ assets/cutout.png ──tools/build_assets.py──▶ assets/parts/*.png + rig.json
```

1. **`tools/cutout.py`** removes the background with rembg (U²-Net). Thresholding
   cannot do this job here: the shirt is light grey (~192–227) and the studio
   backdrop is white (~243–251), so any global brightness cut either eats the
   sleeves or leaks into them. The result is committed, so the rig can be rebuilt
   without rembg or its 176 MB model.
2. **`tools/build_assets.py`** cuts the matte into 11 parts with hand-authored
   polygons and writes the skeleton — pivots, parents and draw order.

Two details are what make the joints hold up:

* Each arm piece reaches well past its shoulder pivot, into the torso, and the arms
  draw *behind* the torso. The pivot sits under solid shirt pixels, so the joint
  cannot tear open.
* The shoulder pivots sit at the **top** of each sleeve, not on its centreline.
  Swinging an arm down out of the T maps "outward" to "downward", so any arm
  material above the pivot swings *outboard* and pokes out past the shoulder as a
  wing, while material below it swings *inboard* and stays hidden. The arm pieces
  are clamped to start at the pivot line for exactly this reason.

Animation lives in `pet/poses.py` as procedural clips — each is a function of phase
returning joint angles, so motion is smooth at any frame rate. Angles are deltas on
top of the rig's rest pose, which is what brings the arms down out of the T.

The maths in `pet/rigmath.py` is pure Python on purpose. The rig only needs 2D
affine transforms — a few 3×3 multiplies per frame — and the app used to reach for
numpy to do it. That cost ~40 MB in the exe and, worse, dragged in C extensions
that failed to load on a real machine (`Importing the numpy C-extensions failed`),
killing the app at startup. The asset tools still use numpy; the runtime does not,
and `tests/test_packaging.py` imports the whole runtime with numpy hard-blocked to
keep it that way.

### Using a different photo

Best results come from a front-facing, evenly lit, full-body T-pose on a plain
background. Then:

```bash
cp your-photo.png assets/source.png
python tools/cutout.py            # needs: pip install "rembg[cpu]"
# re-measure the joints and polygons in tools/build_assets.py for the new body
python tools/build_assets.py
python tools/make_icon.py
```

The coordinates in `tools/build_assets.py` are specific to this photo — see
[`tools/README.md`](tools/README.md) for how they were measured.

## Development

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest tests/ -q                        # 158 tests, no display needed
python -m pet                                     # run it

python tools/render_preview.py --sheet clips.png  # every clip, as a contact sheet
python tools/render_preview.py --clip hiphop --gif hiphop.gif
python tools/render_demo.py --out demo.gif        # the real state machine on a mock desktop
```

`tools/render_demo.py` drives the actual `Behavior` against a scripted desktop and
renders the result, which is the quickest way to see whether a change to the physics
or the clips looks right.

## Known limitations

* **Windows only for the desktop awareness.** The rig, animation and physics are
  portable and tested on Linux, but `EnumWindows`/`dwmapi` are not. On other
  platforms `make_desktop()` falls back to a scripted stand-in, so he walks on an
  imaginary desktop instead of your real windows.
* **He is a 2D cutout, not a 3D model.** He faces the viewer and mirrors to change
  direction; he cannot turn around or show his back. `spin_step` fakes a pivot by
  narrowing him, deliberately mildly — pinch it further and it reads as a glitch
  rather than a spin.
* **Joints are planar.** Angles are kept moderate (roughly ±40° at a joint, more at
  the shoulder) because a cutout starts to look rubbery past that.
* **"Sitting" is a perch.** His hips are anchored to the ledge and his legs dangle
  and kick below it. From a front-on cutout that reads well, but it is not a real
  seated pose.
* **DPI.** The world model is in physical pixels because that is what Win32 reports,
  so Qt's own scaling is switched off in `pet/__main__.py`. If he lands slightly off
  a ledge on an unusual multi-DPI setup, that is the thing to look at.
