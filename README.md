<img src="out/daily.gif" width="100%" alt="A drop of water landing in a dark ocean, its rings racing outward at whatever pace my last week of commits set" />

**A drop in the ocean, rippling at my pace.** How busy my last week was decides
how fast the rings travel, how tightly they are spaced, and how far they carry.

<details>
<summary>How it works, and how to retune it</summary>

## What the picture is

`out/daily.gif` is drawn from scratch at 00:17 UTC every night by
[`.github/workflows/daily.yml`](.github/workflows/daily.yml) and committed only
if it changed.

One drop lands at the centre. A single activity reading — your commits over the
last 7 complete days, graded against your own year — drives four things at once,
all of them the same physical quantity:

| In the picture | Quiet week | Busy week |
| --- | --- | --- |
| Ring spacing | 38 px apart | 20 px apart |
| Ring speed | 1 loop per GIF | 3 loops per GIF |
| How far they carry | 80 px | 135 px |
| Ring brightness | 0.50 | 1.00 |

So a quiet week is a few wide dim rings creeping outward and dying young; a busy
one is tight bright rings racing away and reaching further. The crest is tinted
hotter as activity rises, too, so the colour moves with the speed.

Three details exist purely to stop it looking like a radar sweep, and all three
are baked into per-pixel maps, so they cost nothing at frame time:

- **Refraction** — the swell bends the ring spacing, so they are not drawn circles.
- **Dispersion** — ripples spread further apart the further they have travelled,
  the way real ones do.
- **Directional light** — the rings are brighter on one side.

## Run it locally

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

.venv/bin/python src/collect.py --login suzukiven0m --out out/activity.json
.venv/bin/python src/render.py --data out/activity.json --preview out/still.png
```

`collect.py` picks up a token from `GH_TOKEN`/`GITHUB_TOKEN`, or from
`gh auth token` if you are logged in to the GitHub CLI. `--preview` also leaves
one frame as a PNG. A full render takes well under a second, so tune freely.

To see a different night without waiting, fake the date:

```sh
.venv/bin/python src/collect.py --login suzukiven0m --out out/activity.json --today 2026-06-01
```

## Tune it

`art/palette.json` holds the four water colours: `deep` at the trough,
`shallow` mid-water, `crest` for the ring highlight, and `crest_hot` which the
crest is tinted toward as activity rises.

Everything about the wave is a constant at the top of `src/render.py`:

| Constant | Means |
| --- | --- |
| `RIPPLE_QUIET` / `RIPPLE_BUSY` | pixels between rings, quiet to busy |
| `CARRY_QUIET` / `CARRY_BUSY` | how far out the rings reach before fading |
| `LOOPS_QUIET` / `LOOPS_BUSY` | whole loops per GIF. Must stay integers, or the loop will not close |
| `REFRACTION`, `DISPERSION`, `LIGHT_ANGLE` | the three that make it read as water |
| `ACTIVITY_WINDOW` | days averaged into "activity". 7 is a week; 1 is yesterday |
| `GRADIENT_STEPS` | distinct colours. The main file-size lever |
| `FRAMES` | frames per GIF. The other size lever |

`GRADIENT_STEPS` and `FRAMES` are the size knobs because GIF's compression is
poor on smooth gradients: measured at 738×192, 256 colours gave 8.7 MB, 64 gave
2.3 MB, 16 gave 1.2 MB. A dark-water look compresses far better than a filled
one, which is most of why this lands near 1 MB.

After any change:

```sh
.venv/bin/python src/render.py --check
```

## Traps this already handles

- **Bot commits do not count as contributions.** The nightly commit is made by
  `github-actions[bot]`, so it never inflates the graph it draws.
- **GitHub disables scheduled workflows after 60 days without repository
  activity.** The swell's warp is seeded by the date, so the GIF differs every
  night even through a dead-quiet stretch. `--check` renders two nights with
  *identical activity and different dates* and fails if they come out identical,
  so this cannot silently regress.
- **Cron is best-effort.** GitHub can start a scheduled run 5–60 minutes late;
  only "after 00:00 UTC" matters.
- **Private contributions.** A plain `GITHUB_TOKEN` sees public activity only.
  Add a personal access token as the `ACTIVITY_TOKEN` secret to include private
  contributions.
- **GIF size.** Over 3 MB and `render.py` refuses to write, rather than publish
  something a README browser will not load.
- **Concurrent runs** are serialized by the workflow `concurrency` group, and the
  commit step rebases before pushing.
- **Pinned actions.** Checkout and setup-python are pinned to commits, since this
  job runs with `contents: write`.

`--check` also asserts that the water actually moves, that the loop closes
exactly, and that a dead-quiet week does not render the same as a busy one.

If the schedule ever does get disabled, re-enable it from the Actions tab or with
`gh workflow enable daily.yml`.

</details>
