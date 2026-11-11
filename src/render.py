#!/usr/bin/env python3
"""Draw out/daily.gif: the last year of activity as an oscillating field of ASCII.

Each glyph column samples one stretch of the year, left to right, so the
horizontal axis is time and the colour is that day's temperature: blue for a day
with no commits, white hot for your busiest. Temperature drives the wave too, so
a hot stretch rides high with tight fast ripples while a quiet one goes flat and
calm. All the art is plain text in art/.
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

from PIL import Image

CELL_W, CELL_H = 6, 8  # glyph pitch; the sprite itself is smaller, leaving a gap
COLS, ROWS = 123, 24
WIDTH = COLS * CELL_W
HEIGHT = ROWS * CELL_H

FRAMES, FRAME_MS = 60, 80
PALETTE_COLOURS = 128
SIZE_BUDGET = 3 * 1024 * 1024  # README-friendly; GitHub raw chokes far above this

YEARS_WORTH = 365
HEAT_PERCENTILE = 0.9  # the temperature that counts as "hot"; a heavy tail cannot wash it out
HEAT_CURVE = 0.5  # square root: commit counts are skewed, so lift the quiet days

# The wave, in cell units. Cold stretches are a thin calm line; hot ones get tall,
# tightly packed, fast crests.
AMP_COLD, AMP_HOT = 5.2, 6.4
THICK_COLD, THICK_HOT = 2.2, 4.2
RIPPLE_COLD, RIPPLE_HOT = 48.0, 13.0  # columns per ripple
LOOPS_COLD, LOOPS_HOT = 1, 3  # whole loops per GIF; integers keep it seamless
EDGE_DIM = 0.25  # brightness at the outer edge of the ribbon, 1.0 at the crest
DAILY_DRIFT = 0.35  # radians of date-seeded phase jitter (see check())


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
    temperature = [hex_rgb(c) for c in raw["temperature"]]
    if len(temperature) < 2:
        raise SystemExit(f"{art}/palette.json: temperature needs at least two stops")
    return {
        "background": hex_rgb(raw["background"]),
        "temperature": temperature,
        "ink": hex_rgb(raw["ink"]),
    }


def load_sprites(path: Path, ink: tuple[int, int, int]) -> list[Image.Image]:
    """Text grids: frames split on --- lines, 'o' is ink, '.' is empty."""
    blocks, current = [], []
    for line in path.read_text().splitlines():
        if line.strip().startswith("---"):
            blocks.append(current)
            current = []
        elif line.strip() and not line.lstrip().startswith("#"):
            current.append(line)
    blocks.append(current)

    grids = [b for b in blocks if b]
    if not grids:
        raise SystemExit(f"{path}: no sprite frames found")

    sprites = []
    for grid in grids:
        width = max(len(row) for row in grid)
        image = Image.new("RGBA", (width, len(grid)), (0, 0, 0, 0))
        for y, row in enumerate(grid):
            for x, char in enumerate(row):
                if char == ".":
                    continue
                # 'o', deliberately not '#': a '#' here would be read as a comment.
                if char != "o":
                    raise SystemExit(f"{path}: '{char}' is not '.' or 'o'")
                image.putpixel((x, y), (*ink, 255))
        sprites.append(image)
    return sprites


def glyph_offsets(sprite: Image.Image) -> list[int]:
    """Byte offsets, relative to a cell's origin, of every ink pixel.

    Precomputed once so composing a frame is a flat loop of byte writes.
    """
    alpha = sprite.getchannel("A").tobytes()
    width = sprite.width
    return [
        ((index // width) * WIDTH + index % width) * 3
        for index, value in enumerate(alpha)
        if value
    ]


def ramp_colour(ramp: list[tuple[int, int, int]], heat: float) -> tuple[int, int, int]:
    span = (len(ramp) - 1) * max(0.0, min(1.0, heat))
    low = min(math.floor(span), len(ramp) - 2)
    mix = span - low
    below, above = ramp[low], ramp[low + 1]
    return (
        round(below[0] + (above[0] - below[0]) * mix),
        round(below[1] + (above[1] - below[1]) * mix),
        round(below[2] + (above[2] - below[2]) * mix),
    )


def diurnal(days: list[dict], columns: int) -> list[float]:
    """One temperature per glyph column, left = oldest, right = most recent.

    Each column takes the busiest day it covers, so a single good day in a quiet
    stretch still shows up as a hot column.
    """
    window = days[-YEARS_WORTH:]
    counts = [day["count"] for day in window]
    hot = sorted(c for c in counts if c)
    scale = hot[min(len(hot) - 1, math.floor(len(hot) * HEAT_PERCENTILE))] if hot else 0

    step = len(counts) / columns
    heat = []
    for column in range(columns):
        low = math.floor(column * step)
        high = max(low + 1, min(len(counts), math.floor((column + 1) * step)))
        count = max(counts[low:high])
        heat.append(0.0 if scale <= 0 else min(1.0, count / scale) ** HEAT_CURVE)
    return heat


def shades(ramp: list[tuple[int, int, int]], heat: float, levels: int) -> list[tuple]:
    """The colour of every glyph level in one column: dim at the fringe, bright at the crest."""
    base = ramp_colour(ramp, heat)
    out = []
    for level in range(levels):
        weight = EDGE_DIM + (1.0 - EDGE_DIM) * level / max(1, levels - 1)
        out.append(tuple(round(channel * weight) for channel in base))
    return out


def compose(
    index: int,
    heat: list[float],
    glyphs: list[list[int]],
    palette: dict,
    phases: list[float],
    gains: list[float],
) -> bytes:
    background = bytes(palette["background"])
    buffer = bytearray(background * (WIDTH * HEIGHT))
    top = (ROWS - 1) / 2
    clock = index / FRAMES

    for column, temperature in enumerate(heat):
        amplitude = AMP_COLD + (AMP_HOT - AMP_COLD) * temperature
        thickness = THICK_COLD + (THICK_HOT - THICK_COLD) * temperature
        ripple = RIPPLE_COLD + (RIPPLE_HOT - RIPPLE_COLD) * temperature
        # Whole loops per GIF: a fractional one would make the last frame jump
        # back to the first.
        loops = round(LOOPS_COLD + (LOOPS_HOT - LOOPS_COLD) * temperature)
        column_shades = shades(palette["temperature"], temperature, len(glyphs))
        angle = 2 * math.pi * (column / ripple + loops * clock) + phases[column]
        crest = top + amplitude * gains[column] * math.sin(angle)
        left = column * CELL_W

        for row in range(ROWS):
            reach = 1.0 - abs(row - crest) / thickness
            if reach <= 0.0:
                continue
            level = math.floor(reach * (len(glyphs) - 1) + 0.5)
            offsets = glyphs[level]
            if not offsets:
                continue
            red, green, blue = column_shades[level]
            origin = (row * CELL_H * WIDTH + left) * 3
            for offset in offsets:
                at = origin + offset
                buffer[at] = red
                buffer[at + 1] = green
                buffer[at + 2] = blue
    return bytes(buffer)


def seed(palette: dict, generated: str) -> tuple[list[float], list[float]]:
    """Per-column phase and gain, stable within a run but different every day.

    Beyond looking more like water than a pure sine, this is load-bearing: it
    guarantees the GIF changes nightly even through a long quiet stretch, which
    is what stops GitHub pausing the schedule after 60 idle days.
    """
    rng = random.Random(f"drift:{generated}")
    phases = [rng.uniform(-DAILY_DRIFT, DAILY_DRIFT) for _ in range(COLS)]
    gains = [rng.uniform(0.92, 1.08) for _ in range(COLS)]
    return phases, gains


def render(data: dict, palette: dict, glyphs: list[list[int]]) -> list[Image.Image]:
    heat = diurnal(data["days"], COLS)
    phases, gains = seed(palette, data.get("generated", "unset"))
    frames = []
    for index in range(FRAMES):
        raw = compose(index, heat, glyphs, palette, phases, gains)
        frames.append(Image.frombytes("RGB", (WIDTH, HEIGHT), raw))
    return frames


def write_gif(frames: list[Image.Image], out: Path) -> int:
    base = frames[0].quantize(colors=PALETTE_COLOURS, method=Image.Quantize.MEDIANCUT)
    quantised = [f.quantize(palette=base, dither=Image.Dither.NONE) for f in frames]
    out.parent.mkdir(parents=True, exist_ok=True)
    quantised[0].save(
        out,
        save_all=True,
        append_images=quantised[1:],
        duration=FRAME_MS,
        loop=0,
        optimize=True,
        disposal=2,
    )
    return out.stat().st_size


def synthetic(day: str) -> dict:
    """A full year of plausible data, for --check and for previewing art offline."""
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
    """Fail loudly rather than publish art that cannot form a readable wave."""
    palette = load_palette(art)
    sprites = load_sprites(art / "glyphs.txt", palette["ink"])
    if len(sprites) < 3:
        raise SystemExit(f"{art}/glyphs.txt: a ramp needs at least three glyphs")

    sizes = {(s.width, s.height) for s in sprites}
    if len(sizes) != 1:
        raise SystemExit(f"{art}/glyphs.txt: glyphs differ in size: {sorted(sizes)}")

    glyphs = [glyph_offsets(s) for s in sprites]
    ink = [len(g) for g in glyphs]
    if any(later <= earlier for earlier, later in zip(ink, ink[1:], strict=False)):
        raise SystemExit(
            f"{art}/glyphs.txt: the ramp must get strictly denser, got ink {ink}"
        )

    # The whole point of the picture: a quiet year and a busy one must not look alike.
    heat = diurnal(synthetic("2026-03-02")["days"], COLS)
    if max(heat) - min(heat) < 0.5:
        raise SystemExit("the temperature range collapsed; every column looks the same")

    # The 60-day defence: on two nights with IDENTICAL activity the GIF must still
    # differ, or a quiet stretch stops producing commits and GitHub pauses the
    # schedule. Same days, different date - nothing else varies.
    quiet = synthetic("2026-03-02")
    filled = []
    with tempfile.TemporaryDirectory() as tmp:
        for generated in ("2026-03-02", "2026-03-03"):
            night = dict(quiet, generated=generated)
            path = Path(tmp) / f"check-{generated}.gif"
            size = write_gif(render(night, palette, glyphs), path)
            filled.append((size, path.stat().st_size))

    first, second = filled[0][0], filled[1][0]
    if first > SIZE_BUDGET:
        raise SystemExit(f"{first / 1e6:.1f}MB is too big for a README")
    if first == second:
        raise SystemExit(
            "two nights with identical activity rendered byte-identically; the "
            "nightly commit needs a difference or GitHub pauses the schedule"
        )

    # Every column cold would mean the temperature mapping has stopped working.
    if max(heat) < 1.0:
        raise SystemExit("no column reached the top of the temperature ramp")
    print(
        f"ok: {len(glyphs)} glyphs (ink {ink}), {WIDTH}x{HEIGHT}, "
        f"{FRAMES} frames, {first / 1024:.0f}KB, redraws nightly",
        file=sys.stderr,
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", help="snapshot from collect.py")
    parser.add_argument("--art", default="art", help="folder with palette.json and glyphs.txt")
    parser.add_argument("--out", default="out/daily.gif", help="GIF to write")
    parser.add_argument("--check", action="store_true", help="self-check, writes no GIF")
    parser.add_argument("--preview", help="also write a still PNG here")
    args = parser.parse_args()

    art = Path(args.art)
    palette = load_palette(art)
    sprites = load_sprites(art / "glyphs.txt", palette["ink"])
    glyphs = [glyph_offsets(s) for s in sprites]

    if args.check:
        return check(art)

    if not args.data:
        parser.error("--data is required unless --check is given")
    data = read_json(Path(args.data))
    if not data.get("days"):
        raise SystemExit(f"{args.data}: no days; refusing to draw an empty year")

    frames = render(data, palette, glyphs)
    size = write_gif(frames, Path(args.out))
    if size > SIZE_BUDGET:
        raise SystemExit(f"{args.out} is {size / 1e6:.1f}MB, too big for a README")

    if args.preview:
        Path(args.preview).parent.mkdir(parents=True, exist_ok=True)
        frames[0].save(args.preview)

    heat = diurnal(data["days"], COLS)
    print(
        f"{args.out}: {WIDTH}x{HEIGHT}, {FRAMES} frames, {size / 1024:.0f}KB, "
        f"temperature {min(heat):.2f}-{max(heat):.2f}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
