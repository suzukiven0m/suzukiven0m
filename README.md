<img src="out/daily.gif" width="100%" alt="A year of contributions as an oscillating field of ASCII characters, blue where quiet and white hot where busy" />

**A year of commits, as temperature.** One glyph column per stretch of the year,
left is oldest, right is today. Quiet days run cold and calm; busy ones burn
white hot and ripple fast.

<details>
<summary>How it works, and how to redraw it</summary>

## What the picture is

`out/daily.gif` is drawn from scratch at 00:17 UTC every night by
[`.github/workflows/daily.yml`](.github/workflows/daily.yml) and committed only
if it changed.

Each of the 123 glyph columns samples one ~3-day stretch of your contribution
calendar, in order. That column's temperature then drives everything about it:

| In the picture | Comes from |
| --- | --- |
| Horizontal position | time — left is a year ago, right is today |
| Colour | that column's temperature, blue (no commits) → white hot (busiest) |
| Crest height and ribbon thickness | temperature — busier columns ride higher and thicker |
| Ripples per column | temperature — cold columns ripple slowly and widely, hot ones tightly |
| Oscillation speed | temperature — 1 full loop per GIF when cold, 3 when hot |
| Glyph chosen | distance from that column's crest, along the ramp in `art/glyphs.txt` |
| Per-column phase and gain | seeded by the date |

"Temperature" is relative to you: the ramp tops out at the 90th percentile of
your active days, and commit counts are square-rooted first, because they are
heavily skewed — otherwise one 44-commit day would flatten a whole year to cold.

Columns with no activity at all are still real: they are what makes the hot
stretches read as hot.

## Run it locally

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

.venv/bin/python src/collect.py --login suzukiven0m --out out/activity.json
.venv/bin/python src/render.py --data out/activity.json --preview out/still.png
```

`collect.py` picks up a token from `GH_TOKEN`/`GITHUB_TOKEN`, or from
`gh auth token` if you are logged in to the GitHub CLI. `render.py` writes
`out/daily.gif`; `--preview` also leaves a single frame as a PNG.

To see a different day without waiting, fake the date:

```sh
.venv/bin/python src/collect.py --login suzukiven0m --out out/activity.json --today 2026-06-01
```

It takes about half a second, so tune freely.

## Change the art

Both files in `art/` are plain text and need no code changes.

`art/glyphs.txt` is the character ramp, sparse to dense. **Order is the ramp** —
the last frame is the densest. One character per pixel: `o` is ink, `.` is empty,
and `#` at the start of a line is a comment. Every frame must be the same size
and carry strictly more ink than the one before it, or `--check` refuses to
render.

`art/palette.json` holds the background, the ink mask colour, and the
six-stop cold-to-hot gradient.

The whole wave is tuned by the constants at the top of `src/render.py`
(`AMP_COLD`/`AMP_HOT`, `THICK_COLD`/`THICK_HOT`, `RIPPLE_COLD`/`RIPPLE_HOT`,
`LOOPS_COLD`/`LOOPS_HOT`).

After editing anything, check it renders and stays README-sized:

```sh
.venv/bin/python src/render.py --check
```

## Traps this already handles

- **Bot commits do not count as contributions.** The nightly commit is made by
  `github-actions[bot]`, so it never inflates the graph it draws.
- **GitHub disables scheduled workflows after 60 days without repository
  activity.** The per-column phase and gain are seeded by the date, so the GIF
  differs every night even through a totally quiet stretch. `--check` renders two
  nights with *identical activity and different dates* and fails if they come out
  byte-identical, so this cannot silently regress.
- **Cron is best-effort.** GitHub can start a scheduled run 5–60 minutes late;
  only "after 00:00 UTC" matters.
- **Private contributions.** A plain `GITHUB_TOKEN` sees public activity only.
  Add a personal access token as the `ACTIVITY_TOKEN` secret to include private
  contributions.
- **GIF size.** Over 3 MB and `render.py` refuses to write, rather than publish
  something a README browser will not load. A typical year comes in around
  350 KB.
- **Concurrent runs** are serialized by the workflow `concurrency` group, and the
  commit step rebases before pushing.
- **Pinned actions.** Checkout and setup-python are pinned to commits, since this
  job runs with `contents: write`.

If the schedule ever does get disabled, re-enable it from the Actions tab or with
`gh workflow enable daily.yml`.

</details>
