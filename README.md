# stable-retro-examples

Small, hackable examples for playing with [stable-retro](https://github.com/Farama-Foundation/stable-retro)
environments:

- **`interactive_play.py`** — an interactive keyboard player built for
  inspection (set breakpoints, examine observations / RAM / rewards live).
- **`fmri_play.py`** — a generic fMRI wrapper that turns *any* stable-retro game
  into an experiment: fixed centered display, scanner-trigger gating, a
  declarative curriculum, fixation crosses / IBIs / surveys, and per-play
  logging for later analysis.

## Setup

Uses a dedicated conda env with the pip wheel of stable-retro plus pyglet 1.x
(the interactive renderer relies on the pyglet 1.x drawing API):

```bash
conda create -y -n retro-play python=3.11
conda activate retro-play
pip install --index-url https://pypi.org/simple stable-retro "pyglet<2"
```

## interactive_play.py

Play a retro game with the keyboard, with an explicit, editable control loop.

```bash
conda activate retro-play
python interactive_play.py                      # uses GAME set in the file
python interactive_play.py --game Airstriker-Genesis-v0
python interactive_play.py --list --game sonic  # search installed game names
```

### Controls

| Key | Button | Key | Button |
| --- | --- | --- | --- |
| Arrows | D-pad | `Z` | A / fire |
| `X` | B | `C` | C |
| `A` `S` `D` | X / Y / Z | `Q` `W` | L / R |
| `Enter` | START | `Tab` | MODE / SELECT |
| `ESC` | quit | | |

### What to edit

- **`GAME` / `STATE` / `SCENARIO`** (top of file) — which game, starting
  savestate, and reward/done rules. Or pass `--game NAME`.
- **`on_step(...)`** — runs once per emulated frame. Drop `breakpoint()` here to
  pause and inspect state in pdb:
  - `obs` — `(H, W, 3)` uint8 screen pixels
  - `reward`, `terminated`, `truncated`
  - `info` — RAM-extracted game variables (`score`, `lives`, ...)
  - `env` — the gym env (`env.get_ram()`, `env.buttons`, ...)
  - `step` — frame counter

  When a breakpoint hits, the game window freezes; `c` in pdb resumes. Run under
  `python -u interactive_play.py` for unbuffered prompt output.
- **`keys_to_act(keys, buttons)`** — remap keyboard → console buttons.

## fmri_play.py

A proof-of-concept framework for running stable-retro games as an fMRI
experiment. Uses pyglet only (no pygame/psychopy). Structure mirrors the
existing `vgdl` (fMRI) and `mario_task` (MEG/EEG) task frameworks, stripped to
essentials and made game-agnostic.

```bash
conda activate retro-play
python fmri_play.py --subject 01 --run 0              # real session
python fmri_play.py --subject 01 --run 0 --self-test  # auto-trigger + random agent (no human)
python fmri_play.py --subject 01 --run 0 --save-ram   # also log full RAM per frame
```

### Experiment lifecycle

1. Show `+`, wait for the **scanner trigger** (`=`) → capture `scan_start_ts`
   (the anchor all timestamps are relative to).
2. Pre-run fixation `+` (scanner settle).
3. Walk the curriculum: **text/instruction** screens, **survey** screens, and
   **blocks**. A block = one game; each block has **instances** (a level/state);
   each instance replays the level from the start on every game-over until its
   `duration` elapses (repeat-for-X-seconds). Fixation crosses fill the
   inter-instance / inter-block intervals (IBIs).
4. Post-run fixation `+` (HRF settle), then write `run.json`.

`ESC` quits at any time (data written so far is kept).

### What to edit

- **`CURRICULUM`** (dict at top of file) — the whole experiment: a mapping from
  run index to a list of `text` / `survey` / `block` items. Each block names a
  `game`, optional `scenario`, `instructions`, and a list of `instances`
  (`state` + `duration`). `state=None` uses the game's default start.
- **Timing constants** — `PRERUN_FIXATION`, `POSTRUN_FIXATION`,
  `INTER_INSTANCE_FIXATION`, `INTER_BLOCK_FIXATION`, `FPS`.
- **Display** — `SCREEN_W/H`, `BG` (every game is aspect-preserving letterboxed
  and centered into the one fixed window, so all systems look uniform).
- **Input** — `TRIGGER_KEY`, `ADVANCE_KEY`, `keys_to_act()` (remap to a scanner
  button box here).

### Output (per run, under `data/sub-XX/run-YY/`)

- `run.json` — coarse event timeline (trigger, fixations, blocks, plays,
  surveys), each with a `scan_start_ts`-relative `onset`.
- `block-BB_inst-II_play-PP.bk2` — frame-exact emulator movie (button inputs);
  independently replayable via `retro.Movie` to reproduce the exact play.
- `...json` — per-play metadata: game/state/scenario, frame count, total reward,
  final `info`, and per-button on/off **boxcars** (ready as fMRI regressors).
- `...npz` — per-frame arrays: `frame_ts`, `reward`, `actions` (N×12), and
  optionally `ram` (N×65536) with `--save-ram`.

## Gotchas

- **Run from this directory, not the stable-retro *source* repo.** That repo has
  a `stable_retro/` folder with no compiled C extension; if it's your cwd it
  shadows the installed wheel and import fails.
- **Airstriker's installed name is `Airstriker-Genesis-v0`** (note the `-v0`),
  not `Airstriker-Genesis` as some bundled examples assume. Its ROM ships with
  the package, so no extra download is needed.
- **Other games need their own ROMs**, imported via
  `python -m retro.import <romdir>` (ROMs are matched by checksum).
