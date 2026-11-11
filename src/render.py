#!/usr/bin/env python3
"""Draw out/daily.gif: a drop of water in the ocean, rippling at your pace.

One drop lands at the centre. How busy your last week was sets three things at
once, all the same physical quantity: how fast the rings travel, how tightly
they are spaced, and how far and how brightly they carry. A quiet week gives a
few wide dim rings creeping outward; a busy one gives tight bright rings racing
away and reaching further.

Three implementation notes, because they are load-bearing:

* The rings are a radial ripple *train*, periodic in space, so the GIF loops
  seamlessly. A single expanding front could not: it would have to jump back.
* The swell bends the rings and lights them from one side, and the rings spread
  apart as they travel. Without those three the water reads as a radar sweep.
* Radius, warp, falloff and swell are baked once per run into 8-bit maps. Every
  frame is then about six C-level Pillow operations, so 60 frames at 738x192
  take milliseconds rather than minutes.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
import tempfile
from datetime import date
from pathlib import Path

from PIL import Image, ImageChops

WIDTH, HEIGHT = 738, 192
FRAMES, FRAME_MS = 40, 100
PHASE_STEPS = 256  # phase quantisation, and the length of the per-frame wave table
GRADIENT_STEPS = 64  # distinct colours. GIF's LZW is poor on smooth gradients, so
# this is the main size lever: measured 8.7MB at 256, 2.3MB at 64, 1.2MB at 16.
SIZE_BUDGET = 3 * 1024 * 1024

DROP = (0.5, 0.5)  # fractional canvas position of the single drop

# Everything below rides the one activity reading, quiet -> busy.
RIPPLE_QUIET, RIPPLE_BUSY = 38.0, 20.0  # pixels between rings
CARRY_QUIET, CARRY_BUSY = 80.0, 135.0  # how far out the rings reach before fading
ENERGY_QUIET, ENERGY_BUSY = 0.50, 1.00  # ring brightness
LOOPS_QUIET, LOOPS_BUSY = 1, 3  # whole loops per GIF; integers keep it seamless

DISPERSION = 500.0  # px; real ripples spread further apart as they travel out
REFRACTION = 3.0  # px of swell-driven warp, or the rings read as drawn circles
LIGHT_ANGLE = -0.8  # radians; rings are brighter on one side, like real water
HIGHLIGHT = 1.6  # >1 sharpens the crest into a bright band over dark water
AMBIENT = 0.20  # the ocean's own baseline swell
SWELL = ((1.5, 1.0), (0.7, -1.7))  # swell wavenumbers, in cycles across the frame

YEARS_WORTH = 365
ACTIVITY_WINDOW = 7  # days averaged into "activity"; the one knob that defines it
ACTIVITY_PERCENTILE = 0.9  # the window that counts as your busiest
ACTIVITY_CURVE = 0.5  # square root: a heavy tail cannot wash out a quiet month


def hex_rgb(value: str) -> tuple[int, int, int]:
    try:
        digits = value.lstrip("#")
        number = int(digits, 16)
    except ValueError as error:
        raise SystemExit(f"palette colour {value!r} is not hex") from error
    if len(digits) != 6:
        raise SystemExit(f"palette colour {value!r} is not six hex digits")
    return ((number >> 16) & 0xFF, (number >> 8) & 0xFF, number & 0xFF)


def read_json(path: Path) -> dict:
    """Art and snapshots are hand-edited, so report those mistakes as one line."""
    try:
        return json.loads(path.read_text())
    except OSError as error:
        raise SystemExit(f"{path}: {error.strerror}") from error
    except json.JSONDecodeError as error:
        raise SystemExit(f"{path}: not valid JSON ({error})") from error


def load_palette(art: Path) -> dict:
    raw = read_json(art / "palette.json")
    return {name: hex_rgb(raw[name]) for name in ("deep", "shallow", "crest", "crest_hot")}


def blend(low: tuple, high: tuple, mix: float) -> tuple:
    mix = max(0.0, min(1.0, mix))
    return tuple(round(low[i] + (high[i] - low[i]) * mix) for i in range(3))


def water_palette(palette: dict, activity: float) -> tuple[bytes, list[int]]:
    """The colour table every pixel is indexed through, trough to crest.

    Activity tints the crest, so a busy week's water is hotter at the top of its
    range as well as faster. Returns the table plus the LUT that folds the
    arbitrary 0-255 level down onto it.
    """
    stops = [
        palette["deep"],
        palette["shallow"],
        palette["crest"],
        blend(palette["crest"], palette["crest_hot"], activity),
    ]
    colours = bytearray()
    for step in range(GRADIENT_STEPS):
        span = (step / (GRADIENT_STEPS - 1)) * (len(stops) - 1)
        low = min(math.floor(span), len(stops) - 2)
        mix = span - low
        colours += bytes(
            round(stops[low][channel] + (stops[low + 1][channel] - stops[low][channel]) * mix)
            for channel in range(3)
        )
    fold = [round(step / 255 * (GRADIENT_STEPS - 1)) for step in range(256)]
    return bytes(colours), fold


def activity(data: dict) -> float:
    """How busy the last ACTIVITY_WINDOW complete days were, 0 to 1.

    Graded against your own year rather than a fixed count, so the reading stays
    meaningful whether you commit twice a week or two hundred times.
    """
    counts = [day["count"] for day in data["days"]]
    if len(counts) <= ACTIVITY_WINDOW:
        return 0.0
    windows = [
        sum(counts[start : start + ACTIVITY_WINDOW])
        for start in range(len(counts) - ACTIVITY_WINDOW)
    ]
    busy = sorted(window for window in windows if window)
    if not busy:
        return 0.0
    scale = busy[min(len(busy) - 1, math.floor(len(busy) * ACTIVITY_PERCENTILE))]
    return min(1.0, windows[-1] / scale) ** ACTIVITY_CURVE


def build_maps(seed: str, ripple: float, carry: float, energy: float) -> tuple:
    """Radius, ring falloff and swell, baked once into three 8-bit maps.

    The wobble seeded from the date also has a job: it guarantees the GIF differs
    nightly even through a dead-quiet stretch, which is what stops GitHub pausing
    a scheduled workflow after 60 days without repository activity.
    """
    centre_x = (WIDTH - 1) * DROP[0]
    centre_y = (HEIGHT - 1) * DROP[1]
    rng = random.Random(f"water:{seed}")
    drift = rng.random()  # whole-cycle offset, so tonight's rings start elsewhere
    swell = [rng.uniform(0, 2 * math.pi) for _ in SWELL]
    warp = [rng.uniform(0, 2 * math.pi) for _ in range(3)]

    phase = bytearray(WIDTH * HEIGHT)
    falloff = bytearray(WIDTH * HEIGHT)
    ambient = bytearray(WIDTH * HEIGHT)
    ceiling = AMBIENT * 255

    for y in range(HEIGHT):
        offset_y = y - centre_y
        row = y * WIDTH
        for x in range(WIDTH):
            radius = math.hypot(x - centre_x, offset_y)
            at = row + x

            # The swell bends the ring spacing; without this the rings are drawn circles.
            bend = REFRACTION * (
                math.sin(2 * math.pi * (1.5 * x / WIDTH + 1.0 * y / HEIGHT) + warp[0])
                + 0.6 * math.sin(2 * math.pi * (0.7 * x / WIDTH - 1.7 * y / HEIGHT) + warp[1])
                + 0.3 * math.sin(2 * math.pi * (3.1 * x / WIDTH + 2.3 * y / HEIGHT) + warp[2])
            )
            # Dispersion: ripples spread further apart the further they have travelled.
            travelled = radius + bend + radius * radius / (2 * DISPERSION)
            phase[at] = math.floor((travelled / ripple + drift) * PHASE_STEPS) & (PHASE_STEPS - 1)

            # Lit from one side, so the rings are not uniformly bright all the way round.
            lit = 0.55 + 0.45 * math.sin(math.atan2(offset_y, x - centre_x) + LIGHT_ANGLE)
            falloff[at] = min(255, math.floor(math.exp(-radius / carry) * energy * lit * 255))

            sway = sum(
                math.sin(2 * math.pi * (k_x * x / WIDTH + k_y * y / HEIGHT) + angle)
                for (k_x, k_y), angle in zip(SWELL, swell, strict=True)
            ) / len(SWELL)
            ambient[at] = math.floor((sway + 1) * 0.5 * ceiling)

    return (
        Image.frombytes("L", (WIDTH, HEIGHT), bytes(phase)),
        Image.frombytes("L", (WIDTH, HEIGHT), bytes(falloff)),
        Image.frombytes("L", (WIDTH, HEIGHT), bytes(ambient)),
    )


def scene(data: dict, palette: dict) -> dict:
    """Everything constant across one GIF: the maps, the colour table, the tempo."""
    heat = activity(data)
    colours, fold = water_palette(palette, heat)
    phase, falloff, ambient = build_maps(
        data.get("generated", "unset"),
        RIPPLE_QUIET + (RIPPLE_BUSY - RIPPLE_QUIET) * heat,
        CARRY_QUIET + (CARRY_BUSY - CARRY_QUIET) * heat,
        ENERGY_QUIET + (ENERGY_BUSY - ENERGY_QUIET) * heat,
    )
    return {
        "heat": heat,
        "loops": round(LOOPS_QUIET + (LOOPS_BUSY - LOOPS_QUIET) * heat),
        "phase": phase,
        "falloff": falloff,
        "ambient": ambient,
        "colours": colours,
        "fold": fold,
    }


def frame(index: int, scene: dict) -> Image.Image:
    """One frame: rotate the ripple phase, lift the crest, shade, tint, done."""
    shift = round(scene["loops"] * index / FRAMES * PHASE_STEPS)
    wave = [
        round(max(0.0, math.sin(2 * math.pi * ((step + shift) % PHASE_STEPS) / PHASE_STEPS))
              ** HIGHLIGHT * 255)
        for step in range(PHASE_STEPS)
    ]
    level = ImageChops.add(
        ImageChops.multiply(scene["phase"].point(wave), scene["falloff"]),
        scene["ambient"],
    ).point(scene["fold"])
    # Build P straight from the bytes so our own colour table survives untouched.
    image = Image.frombytes("P", (WIDTH, HEIGHT), level.tobytes())
    image.putpalette(scene["colours"])
    return image


def render(data: dict, palette: dict) -> list[Image.Image]:
    current = scene(data, palette)
    return [frame(index, current) for index in range(FRAMES)]


def write_gif(frames: list[Image.Image], out: Path) -> int:
    out.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        out,
        save_all=True,
        append_images=frames[1:],
        duration=FRAME_MS,
        loop=0,
        optimize=True,
        disposal=1,
    )
    return out.stat().st_size


def synthetic(day: str) -> dict:
    """A full year of plausible data, for --check and for previewing offline."""
    days = []
    started = date.fromisoformat(day).toordinal() - (YEARS_WORTH - 1)
    for offset in range(YEARS_WORTH):
        current = date.fromordinal(started + offset)
        rng = random.Random(f"day:{current.isoformat()}")
        count = rng.choice([0, 0, 0, 1, 2, 3, 5, 8, 13, 21, 34])
        days.append(
            {
                "date": current.isoformat(),
                "count": count,
                "level": 0 if count == 0 else min(4, 1 + count // 6),
            }
        )
    return {
        "login": "check",
        "generated": day,
        "total": sum(d["count"] for d in days),
        "yesterday": days[-2],
        "days": days,
    }


def check(art: Path) -> int:
    """Fail loudly rather than publish water that is flat, frozen, or not looping."""
    palette = load_palette(art)
    quiet = synthetic("2026-03-02")
    current = scene(quiet, palette)

    opening = frame(0, current).tobytes()

    # A flat rectangle would mean the amplitude or the colour table has collapsed.
    if len(set(opening)) < 8:
        raise SystemExit(f"a frame uses only {len(set(opening))} colours; the water is flat")

    # Compare neighbours, not frames a whole sub-cycle apart: with three cycles per
    # loop those are legitimately identical, so that comparison proves nothing.
    if frame(1, current).tobytes() == opening:
        raise SystemExit("the water never moves within a single GIF")

    # It has to loop, or the last frame jumps back to the first.
    if frame(FRAMES, current).tobytes() != opening:
        raise SystemExit("the loop does not close: the frame after the last differs from the first")

    with tempfile.TemporaryDirectory() as tmp:
        # Quiet and busy must genuinely differ, or activity is not reaching the water.
        rested = scene({**quiet, "days": [{**d, "count": 0} for d in quiet["days"]]}, palette)
        if frame(0, rested).tobytes() == opening:
            raise SystemExit("a dead-quiet week renders identically to a busy one")

        # The 60-day defence: IDENTICAL activity on two different nights must still
        # redraw, or a quiet stretch stops committing and the schedule gets paused.
        tonight = write_gif(render(quiet, palette), Path(tmp) / "tonight.gif")
        tomorrow = write_gif(
            render({**quiet, "generated": "2026-03-03"}, palette), Path(tmp) / "tomorrow.gif"
        )
        size = Path(tmp, "tonight.gif").stat().st_size

    if tonight == tomorrow:
        raise SystemExit(
            "two nights with identical activity rendered identically; the nightly "
            "commit needs a difference or GitHub pauses the schedule"
        )
    if size > SIZE_BUDGET:
        raise SystemExit(f"{size / 1e6:.1f}MB is too big for a README")

    print(
        f"ok: {WIDTH}x{HEIGHT}, {FRAMES} frames, {size / 1024:.0f}KB, "
        f"{GRADIENT_STEPS} colours, ripple {RIPPLE_QUIET:.0f}px quiet -> "
        f"{RIPPLE_BUSY:.0f}px busy",
        file=sys.stderr,
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", help="snapshot from collect.py")
    parser.add_argument("--art", default="art", help="folder holding palette.json")
    parser.add_argument("--out", default="out/daily.gif", help="GIF to write")
    parser.add_argument("--check", action="store_true", help="self-check, writes no GIF")
    parser.add_argument("--preview", help="also write one frame as a PNG here")
    args = parser.parse_args()

    palette = load_palette(Path(args.art))

    if args.check:
        return check(Path(args.art))

    if not args.data:
        parser.error("--data is required unless --check is given")
    data = read_json(Path(args.data))
    if not data.get("days"):
        raise SystemExit(f"{args.data}: no days; refusing to draw an empty year")

    current = scene(data, palette)
    frames = [frame(index, current) for index in range(FRAMES)]
    size = write_gif(frames, Path(args.out))
    if size > SIZE_BUDGET:
        raise SystemExit(f"{args.out} is {size / 1e6:.1f}MB, too big for a README")

    if args.preview:
        Path(args.preview).parent.mkdir(parents=True, exist_ok=True)
        frames[0].save(args.preview)

    recent = sum(day["count"] for day in data["days"][-(ACTIVITY_WINDOW + 1) : -1])
    print(
        f"{args.out}: {WIDTH}x{HEIGHT}, {FRAMES} frames, {size / 1024:.0f}KB, "
        f"activity {current['heat']:.2f} ({recent} commits in {ACTIVITY_WINDOW} days), "
        f"{current['loops']} loops",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
