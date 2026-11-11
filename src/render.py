#!/usr/bin/env python3
"""Draw out/daily.gif from a collect.py snapshot.

The last year of contributions becomes the terrain the creature walks across:
one square per day, coloured by that day's activity level, laid out in GitHub's
own Sunday-first week columns. Yesterday's activity picks the sky.

Art lives in --art and is plain text, so the scene can be redrawn without
touching this file.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

from PIL import Image, ImageDraw

CELL, GAP = 14, 2
BLOCK = CELL - GAP
MARGIN = 15
MAX_WEEKS = 53
SKY_H = 106
GROUND_H = 62
ROWS = 7
WIDTH = MARGIN * 2 + MAX_WEEKS * CELL - GAP
GRID_Y = SKY_H
GRID_H = ROWS * CELL - GAP
GROUND_Y = GRID_Y + GRID_H
HEIGHT = GROUND_Y + GROUND_H

FRAMES, FRAME_MS = 60, 80
PALETTE_COLOURS = 128
STROLL = 90  # how far either side of centre the creature paces
CREATURE_SCALE = 3  # nearest-neighbour, so a small sprite stays crisp and readable
SIZE_BUDGET = 3 * 1024 * 1024  # README-friendly; GitHub raw chokes far above this


def hex_rgb(value: str) -> tuple[int, int, int]:
    try:
        number = int(value.lstrip("#"), 16)
    except ValueError as error:
        raise SystemExit(f"palette colour {value!r} is not hex") from error
    if len(value.lstrip("#")) != 6:
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
    return {
        "levels": [hex_rgb(c) for c in raw["levels"]],
        "sky": {k: [hex_rgb(c) for c in v] for k, v in raw["sky"].items()},
        "ground": [hex_rgb(c) for c in raw["ground"]],
        "moon": hex_rgb(raw["moon"]),
        "speck": hex_rgb(raw["speck"]),
        "panel": hex_rgb(raw["panel"]),
        "panel_edge": hex_rgb(raw["panel_edge"]),
        "chars": {k: hex_rgb(v) for k, v in raw["chars"].items()},
    }


def load_frames(path: Path, chars: dict) -> list[Image.Image]:
    """Text grids, frames split on --- lines, '.' transparent."""
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
    if len({len(g) for g in grids}) != 1:
        raise SystemExit(f"{path}: frames have different heights")

    frames = []
    for grid in grids:
        width = max(len(row) for row in grid)
        image = Image.new("RGBA", (width, len(grid)), (0, 0, 0, 0))
        for y, row in enumerate(grid):
            for x, char in enumerate(row):
                if char in chars:
                    image.putpixel((x, y), (*chars[char], 255))
                elif char != ".":
                    raise SystemExit(f"{path}: '{char}' is not in palette chars")
        frames.append(image)
    return frames


def band(draw: ImageDraw.ImageDraw, box, top, bottom, bands: int) -> None:
    """Vertical gradient in visible steps, so it reads as pixel art, not a fade."""
    x0, y0, x1, y1 = box
    step = max(1, (y1 - y0) // bands)
    for y in range(y0, y1, step):
        mix = (y - y0) / max(1, y1 - y0 - 1)
        colour = tuple(round(top[i] + (bottom[i] - top[i]) * mix) for i in range(3))
        draw.rectangle([x0, y, x1, min(y + step, y1)], fill=colour)


def mood_of(data: dict) -> str:
    yesterday = data.get("yesterday") or {"count": 0}
    count = yesterday["count"]
    if count == 0:
        return "quiet"
    if count < 3:
        return "low"
    if count < 10:
        return "steady"
    return "blazing"


def grid_offset(weeks: int) -> int:
    return MARGIN + max(0, (MAX_WEEKS - weeks) * CELL) // 2


def draw_sky(image: Image.Image, palette: dict, data: dict, seed: str) -> None:
    draw = ImageDraw.Draw(image)
    top, bottom = palette["sky"][mood_of(data)]
    band(draw, (0, 0, WIDTH, SKY_H), top, bottom, bands=14)

    # Speck field: seeded by the date, so a quiet year still redraws every night.
    rng = random.Random(f"sky:{seed}")
    for _ in range(42):
        x, y = rng.randrange(WIDTH), rng.randrange(SKY_H - 20)
        draw.point((x, y), fill=palette["speck"])
        if rng.random() < 0.25:
            draw.point((x + 1, y), fill=palette["speck"])

    # A moon whose size tracks the day of the month.
    rng = random.Random(f"moon:{seed}")
    radius = rng.randrange(7, 12)
    cx, cy = WIDTH - 74, 34
    draw.ellipse(
        [cx - radius, cy - radius, cx + radius, cy + radius], fill=palette["moon"]
    )
    draw.ellipse(
        [cx - radius - 3, cy - radius - 3, cx + radius + 3, cy + radius + 3],
        outline=palette["speck"],
    )


def layout(data: dict) -> tuple[dict[str, tuple[int, int]], int]:
    """Place each day on GitHub's Sunday-first week grid: column = week, row = weekday.

    Derived from the dates rather than the order of the list, so a short first
    week cannot shift every square after it.
    """
    days = data["days"]
    if not days:
        return {}, 1
    first = date.fromisoformat(days[0]["date"])
    origin = first - timedelta(days=(first.weekday() + 1) % 7)  # that week's Sunday

    placement = {}
    for day in days:
        current = date.fromisoformat(day["date"])
        sunday = current - timedelta(days=(current.weekday() + 1) % 7)
        placement[day["date"]] = (
            (sunday - origin).days // 7,
            (current.weekday() + 1) % 7,
        )
    columns = max(column for column, _ in placement.values()) + 1
    return placement, max(1, min(MAX_WEEKS, columns))


def draw_terrain(image: Image.Image, palette: dict, data: dict) -> int:
    """One square per day. Returns the week count, for centring."""
    placement, weeks = layout(data)
    origin = grid_offset(weeks)
    generated = data.get("generated")
    draw = ImageDraw.Draw(image)

    pad = 7
    panel = [
        origin - pad,
        GRID_Y - pad,
        origin + weeks * CELL - GAP + pad - 1,
        GRID_Y + GRID_H + pad - 1,
    ]
    draw.rectangle(panel, fill=palette["panel"], outline=palette["panel_edge"])

    for day in data["days"]:
        column, row = placement[day["date"]]
        if column >= weeks:
            continue
        if generated and day["date"] > generated:
            continue  # the rest of this week has not happened yet
        x = origin + column * CELL
        y = GRID_Y + row * CELL
        draw.rectangle(
            [x, y, x + BLOCK - 1, y + BLOCK - 1],
            fill=palette["levels"][max(0, min(4, day["level"]))],
        )
    return weeks


def draw_ground(image: Image.Image, palette: dict) -> None:
    draw = ImageDraw.Draw(image)
    band(draw, (0, GROUND_Y, WIDTH, HEIGHT), *palette["ground"], bands=9)


def frame_at(
    index: int,
    palette: dict,
    data: dict,
    sprites: list[Image.Image],
    backdrop: Image.Image,
    weeks: int,
) -> Image.Image:
    image = backdrop.copy()
    progress = index / FRAMES
    origin = grid_offset(weeks)
    centre = origin + weeks * CELL / 2
    x = centre + STROLL * math.sin(2 * math.pi * 2 * progress)
    facing_left = math.cos(2 * math.pi * 2 * progress) < 0

    walk = sprites[(index * len(sprites)) // FRAMES % len(sprites)]
    if facing_left:
        walk = walk.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    if CREATURE_SCALE != 1:
        walk = walk.resize(
            (walk.width * CREATURE_SCALE, walk.height * CREATURE_SCALE),
            Image.Resampling.NEAREST,
        )

    # Clear of the card, so the creature never covers a day.
    feet = GROUND_Y + 48
    image.alpha_composite(walk, (round(x) - walk.width // 2, feet - walk.height))

    # Two specks drifting up keep the still parts of the scene alive.
    draw = ImageDraw.Draw(image)
    travel = SKY_H + 24
    for i, speed in ((0, 2), (1, 3)):
        y = SKY_H + 24 - (index * speed) % travel
        if 0 <= y <= SKY_H:
            draw.point((round(centre) + 120 * (i + 1), y), fill=palette["speck"])
    return image


def write_gif(frames: list[Image.Image], out: Path) -> int:
    opaque = [f.convert("RGB") for f in frames]
    base = opaque[0].quantize(colors=PALETTE_COLOURS, method=Image.Quantize.MEDIANCUT)
    quantised = [f.quantize(palette=base, dither=Image.Dither.NONE) for f in opaque]
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
    started = date.fromisoformat(day).toordinal() - 364
    for offset in range(365):
        current = date.fromordinal(started + offset)
        rng = random.Random(f"day:{current.isoformat()}")
        count = rng.choice([0, 0, 1, 2, 3, 5, 8, 13, 21])
        days.append(
            {
                "date": current.isoformat(),
                "count": count,
                "level": 0 if count == 0 else min(4, 1 + count // 5),
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
    """Fail loudly rather than publish art or a palette that cannot render."""
    palette = load_palette(art)
    sprites = load_frames(art / "creature.txt", palette["chars"])
    if len(sprites) < 2:
        raise SystemExit(f"{art}/creature.txt: a walk cycle needs at least two frames")

    # The year is only legible if every square is the right weekday and week.
    days = synthetic("2026-03-02")["days"]
    placement, columns = layout({"days": days})
    if len(set(placement.values())) != len(days):
        raise SystemExit("art/layout: two days landed on the same square")
    if columns != MAX_WEEKS:
        raise SystemExit(f"layout: a year should be {MAX_WEEKS} columns, got {columns}")
    for day in days:
        current = date.fromisoformat(day["date"])
        if placement[day["date"]][1] != (current.weekday() + 1) % 7:
            raise SystemExit(f"layout: {day['date']} is on the wrong weekday row")

    sizes = []
    with tempfile.TemporaryDirectory() as tmp:
        for day in ("2026-03-01", "2026-03-02"):
            data = synthetic(day)
            backdrop = backdrop_for(data, palette)
            frames = [
                frame_at(i, palette, data, sprites, backdrop, weeks_of(data))
                for i in range(FRAMES)
            ]
            path = Path(tmp) / f"check-{day}.gif"
            sizes.append((write_gif(frames, path), path.stat().st_size))

    first, second = sizes[0][0], sizes[1][0]
    if first > SIZE_BUDGET:
        raise SystemExit(f"{first / 1e6:.1f}MB is too big for a README")
    if first == second:
        raise SystemExit(
            "two different days rendered byte-identically; the nightly commit needs "
            "a difference to keep GitHub from pausing the schedule after 60 quiet days"
        )
    print(
        f"ok: {len(sprites)} frames, {WIDTH}x{HEIGHT}, {first / 1024:.0f}KB, "
        f"redraws nightly",
        file=sys.stderr,
    )
    return 0


def backdrop_for(data: dict, palette: dict) -> Image.Image:
    image = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 255))
    draw_sky(image, palette, data, data.get("generated", "unset"))
    draw_ground(image, palette)
    draw_terrain(image, palette, data)  # last, so the card sits on the horizon
    return image


def weeks_of(data: dict) -> int:
    return layout(data)[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", help="snapshot from collect.py")
    parser.add_argument("--art", default="art", help="folder with palette.json and creature.txt")
    parser.add_argument("--out", default="out/daily.gif", help="GIF to write")
    parser.add_argument("--check", action="store_true", help="self-check, writes no GIF")
    parser.add_argument("--preview", help="also write a still PNG here")
    args = parser.parse_args()

    art = Path(args.art)
    palette = load_palette(art)
    sprites = load_frames(art / "creature.txt", palette["chars"])

    if args.check:
        return check(art)

    if not args.data:
        parser.error("--data is required unless --check is given")
    data = read_json(Path(args.data))
    if not data.get("days"):
        raise SystemExit(f"{args.data}: no days; refusing to draw an empty year")

    backdrop = backdrop_for(data, palette)
    weeks = weeks_of(data)
    frames = [
        frame_at(i, palette, data, sprites, backdrop, weeks) for i in range(FRAMES)
    ]
    size = write_gif(frames, Path(args.out))
    if size > SIZE_BUDGET:
        raise SystemExit(f"{args.out} is {size / 1e6:.1f}MB, too big for a README")

    if args.preview:
        Path(args.preview).parent.mkdir(parents=True, exist_ok=True)
        frames[0].save(args.preview)

    print(
        f"{args.out}: {WIDTH}x{HEIGHT}, {FRAMES} frames, "
        f"{size / 1024:.0f}KB, mood={mood_of(data)}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
