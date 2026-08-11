"""
add_game.py — register a ROM as a new stable-retro game integration.

stable-retro only knows a game if there's an "integration" folder for it
(named "<Game>-<Platform>") containing at least:
    rom.sha       - sha1 of the ROM (how retro matches the file)
    rom.<ext>     - the ROM itself (copied here)
    data.json     - variable definitions (may be empty {} for just playing)
    metadata.json - default state etc. (may be minimal for just playing)

This script does that for any ROM. It creates the folder inside the installed
package's `stable` data dir so the game is then usable everywhere with no
custom-path wiring:

    conda activate retro-play
    python add_game.py ~/Downloads/tobutobugirl-dx.gbc --name TobuTobuGirlDX

Then play it:
    python interactive_play.py --game TobuTobuGirlDX-GbColor
    # or add it to a CURRICULUM block in fmri_play.py

ROM extension decides the platform: .gb -> GameBoy, .gbc -> GbColor,
.nes -> Nes, .md/.gen -> Genesis, .sfc/.smc -> Snes, etc.
"""

import argparse
import hashlib
import json
import os
import shutil

import stable_retro as retro


def sha1(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def platform_for(ext):
    # EMU_EXTENSIONS keys include the leading dot, e.g. ".gbc"
    ext = "." + ext.lower().lstrip(".")
    if ext not in retro.data.EMU_EXTENSIONS:
        raise SystemExit(
            f"Unknown ROM extension '{ext}'. Known: "
            f"{sorted(retro.data.EMU_EXTENSIONS)}")
    return retro.data.EMU_EXTENSIONS[ext]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("rom", help="path to the ROM file")
    p.add_argument("--name", required=True,
                   help="game name WITHOUT the -Platform suffix, e.g. TobuTobuGirlDX")
    args = p.parse_args()

    rom = os.path.expanduser(args.rom)
    if not os.path.isfile(rom):
        raise SystemExit(f"ROM not found: {rom}")

    ext = os.path.splitext(rom)[1]
    platform = platform_for(ext)
    game = f"{args.name}-{platform}"

    # Resolve an ABSOLUTE stable-data dir (retro.data.path may be relative).
    stable_dir = os.path.abspath(
        os.path.join(os.path.dirname(retro.__file__), "data", "stable"))
    dest = os.path.join(stable_dir, game)
    os.makedirs(dest, exist_ok=True)

    # 1. checksum
    digest = sha1(rom)
    with open(os.path.join(dest, "rom.sha"), "w") as f:
        f.write(digest + "\n")

    # 2. copy ROM in with the canonical name rom.<ext>
    shutil.copy(rom, os.path.join(dest, "rom" + ext.lower()))

    # 3. minimal data.json / metadata.json (enough to *play*; add vars later)
    dj = os.path.join(dest, "data.json")
    if not os.path.exists(dj):
        with open(dj, "w") as f:
            json.dump({"info": {}}, f, indent=2)
    mj = os.path.join(dest, "metadata.json")
    if not os.path.exists(mj):
        with open(mj, "w") as f:
            json.dump({"default_state": None}, f, indent=2)

    print(f"Integrated '{game}'  (platform={platform}, sha1={digest})")
    print(f"  folder: {dest}")

    # 4. sanity-check: can we make it?
    env = retro.make(game=game, render_mode="rgb_array")
    env.reset()
    env.step(env.action_space.sample())
    print(f"  OK: buttons={env.buttons}  frame={env.render().shape}")
    env.close()
    print(f"\nPlay it:  python interactive_play.py --game {game}")


if __name__ == "__main__":
    main()
