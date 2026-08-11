# stable-retro-examples

Small, hackable examples for playing with [stable-retro](https://github.com/Farama-Foundation/stable-retro)
environments — starting with an interactive keyboard player built for
inspection (set breakpoints, examine observations / RAM / rewards live).

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

## Gotchas

- **Run from this directory, not the stable-retro *source* repo.** That repo has
  a `stable_retro/` folder with no compiled C extension; if it's your cwd it
  shadows the installed wheel and import fails.
- **Airstriker's installed name is `Airstriker-Genesis-v0`** (note the `-v0`),
  not `Airstriker-Genesis` as some bundled examples assume. Its ROM ships with
  the package, so no extra download is needed.
- **Other games need their own ROMs**, imported via
  `python -m retro.import <romdir>` (ROMs are matched by checksum).
