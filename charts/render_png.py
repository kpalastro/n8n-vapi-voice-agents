#!/usr/bin/env python3
"""
Rasterise each figure to PNG so it can be embedded in the README.

GitHub renders an SVG from a repo path, but a PNG is what survives being pasted
into a chat, an email or a phone browser — which is where these figures get read.
The SVG stays the source of truth; this only converts it.

The window is sized from each figure's own width and height, so the screenshot is an exact crop
of the figure with no margin and no clipped edge, and the 2x default keeps the text sharp on a
retina display. The scale is applied to the figure's own width/height rather than to the
screenshot, so the browser rasterises vector art instead of upscaling pixels — which is also far
faster on the tallest figure.

Needs a Chromium-based browser (Chrome, Chromium, Brave or Edge), and needs a viewBox on the
figure, since that is what makes scaling it a no-op for the layout. Set CHROME_PATH to point at a
browser if the usual locations are wrong.

Headless Chrome on macOS writes the screenshot about a second in and then sits there without
exiting, so waiting for the process means waiting for the timeout. This waits for the PNG to be
finished instead, and stops the browser as soon as it is.

Usage:
    python3 charts/render_png.py                  # every .svg beside this script
    python3 charts/render_png.py --scale 3
    python3 charts/render_png.py charts/inbound-call-quality.svg
"""

import argparse
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
PNG_END = b"IEND\xaeB`\x82"

CANDIDATES = [
    os.environ.get("CHROME_PATH"),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/snap/bin/chromium",
]


def find_chrome():
    for path in CANDIDATES:
        if path and Path(path).exists():
            return path
    for name in ("google-chrome", "chromium", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def figure_size(svg):
    """Read the root width/height off the SVG — the exact crop to screenshot."""
    head = svg.read_text(encoding="utf-8")[:400]
    root = re.search(r"<svg([^>]*)>", head)
    if not root:
        raise ValueError(f"{svg.name}: no root <svg> element")
    attrs = root.group(1)
    viewbox = re.search(r'\sviewBox="([^"]+)"', attrs)
    if not viewbox:
        raise ValueError(f"{svg.name}: no viewBox, so it cannot be scaled without redrawing it")
    w = re.search(r'\swidth="([\d.]+)"', attrs)
    h = re.search(r'\sheight="([\d.]+)"', attrs)
    if not w or not h:
        raise ValueError(f"{svg.name}: no width/height on the root <svg>")
    return int(float(w.group(1))), int(float(h.group(1))), viewbox.group(1)


def scaled_copy(svg, scale, dest):
    """Write a copy of the figure whose width/height are `scale` times larger.

    The viewBox is untouched, so the whole figure scales as vector art and
    rasterises at 1 device pixel per CSS pixel. Asking the browser to upscale
    instead (--force-device-scale-factor) is exact too, but on a tall viewport it
    is many times slower, so the scale is applied to the artwork rather than to
    the screenshot.
    """
    w, h = figure_size(svg)[:2]
    raw = svg.read_text(encoding="utf-8")
    root = re.search(r"<svg([^>]*)>", raw)
    attrs = root.group(1)
    scaled = re.sub(r'\swidth="[\d.]+"', f' width="{int(round(w * scale))}"', attrs, count=1)
    scaled = re.sub(r'\sheight="[\d.]+"', f' height="{int(round(h * scale))}"', scaled, count=1)
    dest.write_text(raw[:root.start()] + "<svg" + scaled + ">" + raw[root.end():],
                    encoding="utf-8")
    return int(round(w * scale)), int(round(h * scale))


def png_size(path):
    """Width/height straight out of the PNG IHDR, so the crop is verified."""
    with open(path, "rb") as fh:
        head = fh.read(24)
    if head[:8] != PNG_MAGIC:
        raise ValueError(f"{path.name}: not a PNG")
    return struct.unpack(">II", head[16:24])


def png_complete(path):
    """True once the browser has written the whole file, not just its start."""
    try:
        with open(path, "rb") as fh:
            if fh.read(8) != PNG_MAGIC:
                return False
            fh.seek(-len(PNG_END), os.SEEK_END)
            return fh.read(len(PNG_END)) == PNG_END
    except OSError:
        return False


def render(chrome, svg, scale, profile_dir, timeout):
    out = svg.with_suffix(".png")
    scaled = profile_dir.parent / f"{svg.stem}-scaled.svg"
    w, h = scaled_copy(svg, scale, scaled)
    # a stale PNG from an earlier run must not be mistaken for this one
    out.unlink(missing_ok=True)
    # a fresh profile per figure: a reused one silently screenshots nothing
    if profile_dir.exists():
        shutil.rmtree(profile_dir)
    cmd = [
        chrome, "--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
        "--no-first-run", "--no-default-browser-check", "--disable-extensions",
        f"--user-data-dir={profile_dir}",
        f"--window-size={w},{h}",
        "--force-device-scale-factor=1",
        "--virtual-time-budget=2000",
        f"--screenshot={out}",
        scaled.resolve().as_uri(),
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    deadline = time.monotonic() + timeout
    ready = False
    try:
        while time.monotonic() < deadline:
            # the browser does not exit after a headless screenshot, so the file
            # finishing — not the process finishing — is what says the render is done
            if png_complete(out):
                ready = True
                break
            if proc.poll() is not None:
                break  # it gave up on its own; its stderr says why
            time.sleep(0.2)
    finally:
        # stop the browser now rather than leave it parked for the rest of the timeout
        proc.terminate()
        try:
            _, err = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            _, err = proc.communicate()
        scaled.unlink(missing_ok=True)
    if not ready:
        tail = (err or b"").decode(errors="replace").strip()[-400:]
        sys.exit(f"{svg.name}: no PNG after {timeout}s" + (f"\n{tail}" if tail else ""))
    got = png_size(out)
    want = (int(round(figure_size(svg)[0] * scale)), int(round(figure_size(svg)[1] * scale)))
    if got != want:
        sys.exit(f"{svg.name}: rendered {got[0]}x{got[1]}, expected {want[0]}x{want[1]}")
    return out, got


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("svg", nargs="*", type=Path, help="figures to render (default: all)")
    ap.add_argument("--scale", type=float, default=2.0, help="pixels per CSS px (default 2)")
    ap.add_argument("--timeout", type=float, default=180,
                    help="give up on a figure after this many seconds (default 180)")
    ap.add_argument("--profile-dir", type=Path, default=None,
                    help="scratch dir for the browser profile (default: a temp dir)")
    args = ap.parse_args()

    chrome = find_chrome()
    if not chrome:
        sys.exit("no Chromium-based browser found — set CHROME_PATH")

    svgs = args.svg or sorted(HERE.glob("*.svg"))
    if not svgs:
        sys.exit("no .svg files to render")

    tmp = None
    if args.profile_dir:
        args.profile_dir.mkdir(parents=True, exist_ok=True)
        profile = args.profile_dir
    else:
        tmp = tempfile.TemporaryDirectory(prefix="render-png-")
        profile = Path(tmp.name)

    print(f"chrome  -> {chrome}")
    for svg in svgs:
        out, (w, h) = render(chrome, svg, args.scale, profile / svg.stem, args.timeout)
        kb = out.stat().st_size / 1024
        print(f"  {svg.name:26} -> {out.name:26} {w}x{h}px  {kb:,.0f} KB")

    if tmp:
        tmp.cleanup()


if __name__ == "__main__":
    sys.exit(main())
