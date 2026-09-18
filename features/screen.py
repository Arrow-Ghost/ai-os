"""
Eyes: screenshots, and asking the vision model what is on screen.

WAYLAND NOTE -- read this before "fixing" anything here.
Your session is Wayland, where a client cannot grab another window's pixels;
that is a security property of the protocol, not a bug. The X11 stack (mss,
pyautogui, import) silently fails or returns black. So capture goes through a
desktop screenshot helper instead: spectacle on KDE, grim on wlroots,
gnome-screenshot on GNOME. mss is used only if you are actually on X11.

PRIVACY -- screen.describe uploads your screen to Groq and an image cannot be
redacted. That is why it is tiered DANGER and asks every time. If you are
demoing and the prompt is in the way, lower it deliberately in
config/policy.yaml:

    governance:
      tier_overrides:
        screen.describe: write
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

from servant.sdk import Tier, ToolError, tool

REGIONS = {"full", "active", "monitor"}


def _capture(destination: Path, region: str) -> str:
    """Take the shot with whatever backend this desktop provides."""
    wayland = os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"

    if shutil.which("spectacle"):                      # KDE, Wayland and X11
        flag = {"full": "-f", "active": "-a", "monitor": "-m"}[region]
        cmd, backend = ["spectacle", "-b", "-n", flag, "-o", str(destination)], "spectacle"
    elif shutil.which("grim"):                          # wlroots compositors
        cmd, backend = ["grim", str(destination)], "grim"
    elif shutil.which("gnome-screenshot"):
        cmd = ["gnome-screenshot", "-f", str(destination)]
        if region == "active":
            cmd.insert(1, "-w")
        backend = "gnome-screenshot"
    elif not wayland:
        return _capture_mss(destination)                # X11 only
    else:
        raise ToolError(
            "no Wayland screenshot backend found. Install one: "
            "`sudo apt install kde-spectacle` (KDE) or `sudo apt install grim` (wlroots)."
        )

    try:
        result = subprocess.run(cmd, capture_output=True, timeout=30)
    except subprocess.TimeoutExpired as exc:
        raise ToolError(f"{backend} timed out after 30s") from exc

    # Some backends exit 0 but write nothing if the compositor refuses.
    if not destination.exists() or destination.stat().st_size == 0:
        detail = result.stderr.decode(errors="replace").strip()[:200]
        raise ToolError(f"{backend} produced no image. {detail}")
    return backend


def _capture_mss(destination: Path) -> str:
    try:
        import mss  # type: ignore
    except ImportError as exc:
        raise ToolError("on X11 and mss is not installed (pip install mss)") from exc
    with mss.mss() as sct:
        sct.shot(mon=-1, output=str(destination))
    return "mss"


def _downscale(path: Path, max_width: int) -> str:
    """Shrink a wide screenshot so it does not eat the token budget."""
    try:
        from PIL import Image  # type: ignore
    except ImportError:
        return "not resized (Pillow not installed)"

    with Image.open(path) as img:
        if img.width <= max_width:
            return f"{img.width}x{img.height}"
        height = round(img.height * max_width / img.width)
        img.resize((max_width, height), Image.LANCZOS).save(path)
        return f"{max_width}x{height} (downscaled)"


@tool(
    name="screen.capture",
    tier=Tier.READ,
    params={
        "region": "full (whole desktop) | active (focused window) | monitor (current screen)",
        "max_width": "Downscale wider images to this width, to save tokens",
    },
)
def screen_capture(ctx, region: str = "full", max_width: int = 1600) -> str:
    """Take a screenshot and save it. Returns the file path -- use screen.describe to read it."""
    if region not in REGIONS:
        raise ToolError(f"region must be one of {sorted(REGIONS)}, got {region!r}")

    destination = ctx.state_dir("screen") / f"shot-{time.strftime('%Y%m%d-%H%M%S')}.png"
    backend = _capture(destination, region)
    size = _downscale(destination, max_width)

    ctx.memory.remember("screen.last_capture", str(destination))
    ctx.log(f"captured {region} via {backend} -> {destination.name}")
    return f"{destination} ({size}, {destination.stat().st_size // 1024}KB, via {backend})"


@tool(
    name="screen.describe",
    tier=Tier.DANGER,
    params={
        "question": "What you want to know about the screen",
        "path": "An existing image to look at. Leave empty to capture a fresh one.",
        "region": "Used only when capturing fresh: full | active | monitor",
    },
    undo="nothing to undo locally, but the image has already been sent to Groq",
)
def screen_describe(ctx, question: str, path: str = "", region: str = "full") -> str:
    """Look at the screen and answer a question about it.

    UPLOADS AN IMAGE OF YOUR SCREEN to Groq's vision model. The image cannot be
    redacted, so anything visible -- tokens, DMs, customer data -- goes with it.
    """
    if path:
        image = ctx.check_path(path)
        if not image.is_file():
            raise ToolError(f"no such image: {image}")
    else:
        if region not in REGIONS:
            raise ToolError(f"region must be one of {sorted(REGIONS)}, got {region!r}")
        image = ctx.state_dir("screen") / f"shot-{time.strftime('%Y%m%d-%H%M%S')}.png"
        _capture(image, region)
        _downscale(image, 1600)

    ctx.log(f"sending {image.name} to the vision model")
    return ctx.see(image, question)


@tool(
    name="screen.read_text",
    tier=Tier.DANGER,
    params={"path": "An existing image. Leave empty to capture a fresh one."},
    undo="nothing to undo locally, but the image has already been sent to Groq",
)
def screen_read_text(ctx, path: str = "") -> str:
    """Transcribe the text visible on screen. Same upload warning as screen.describe."""
    return screen_describe(
        ctx,
        question=(
            "Transcribe all text visible in this image, preserving the reading order and "
            "layout as plain text. Do not describe the image or add commentary."
        ),
        path=path,
    )
