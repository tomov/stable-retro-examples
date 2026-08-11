"""
Hackable interactive stable-retro player.

Play a retro game with the keyboard, but with an explicit, editable control
loop so you can set breakpoints and inspect observations / rewards / RAM.

USAGE
    conda activate retro-play
    python interactive_play.py                 # uses GAME below
    python interactive_play.py --game Airstriker-Genesis-v0
    python interactive_play.py --list          # print matching game names

WHAT TO EDIT
    * GAME / STATE / SCENARIO ............ which game + starting savestate
    * on_step() .......................... runs every frame -> put breakpoint() here
    * keys_to_act() ...................... keyboard -> console buttons mapping

NOTES
    * Run this from OUTSIDE the stable-retro *source* repo. The repo contains a
      `stable_retro/` folder with no compiled C extension; if Python's cwd is the
      repo root it shadows the installed wheel and import fails. This directory is
      fine.
    * Installed Airstriker is named "Airstriker-Genesis-v0" (note the -v0), not
      "Airstriker-Genesis" as some bundled examples assume.
    * When a breakpoint pauses execution, the pyglet window simply freezes; the
      game resumes when you continue (`c` in pdb).
"""

import argparse
import ctypes
import sys
import time

import numpy as np
import pyglet
from pyglet import gl
from pyglet.window import key as keycodes

import stable_retro as retro

# ---------------------------------------------------------------------------
# CONFIG — change these freely
# ---------------------------------------------------------------------------
GAME = "Airstriker-Genesis-v0"      # try retro.data.list_games() for options
STATE = retro.State.DEFAULT          # e.g. "Level1" for some games, or DEFAULT
SCENARIO = None                      # None = default reward/done rules
FPS = 60                             # emulator frames per second


# ---------------------------------------------------------------------------
# CONTROL LOOP HOOK — this runs once per emulated frame.
# Drop `breakpoint()` anywhere in here to pause and inspect state in pdb:
#     obs         -> np.uint8 array, shape (H, W, 3), the screen pixels
#     reward      -> float from the scenario's reward function
#     terminated  -> bool, natural episode end (e.g. game over)
#     truncated   -> bool, external cutoff (e.g. time limit)
#     info        -> dict of RAM-extracted game variables (score, lives, ...)
#     env         -> the underlying gym env (env.get_ram(), env.buttons, ...)
#     step        -> global frame counter
# Return nothing; this is purely for observation/logging/experiments.
# ---------------------------------------------------------------------------
def on_step(obs, reward, terminated, truncated, info, env, step):
    # Example: print every second, and break when the score first changes.
    if step % FPS == 0:
        print(f"[t={step}] reward={reward:+.1f} info={info} obs.shape={obs.shape}")

    # --- Try uncommenting one of these to experiment -----------------------
    # if reward != 0:
    #     print(f"got reward {reward} at t={step}")
    #
    # if info.get("score", 0) > 0:
    #     breakpoint()   # inspect: obs.mean(), env.get_ram()[:32], info, etc.
    #
    # save_frame(obs, f"/tmp/frame_{step:05d}.png")   # needs Pillow
    pass


def keys_to_act(keys, buttons):
    """Map currently-held keyboard keys to a console button action vector.

    `keys` is a set of pyglet key-name strings (e.g. "UP", "Z").
    Returns a list[bool] aligned with `buttons` (env.buttons).
    Edit this to remap controls.
    """
    pressed = {
        "BUTTON": "Z" in keys,
        "A": "Z" in keys,
        "B": "X" in keys,
        "C": "C" in keys,
        "X": "A" in keys,
        "Y": "S" in keys,
        "Z": "D" in keys,
        "L": "Q" in keys,
        "R": "W" in keys,
        "UP": "UP" in keys,
        "DOWN": "DOWN" in keys,
        "LEFT": "LEFT" in keys,
        "RIGHT": "RIGHT" in keys,
        "MODE": "TAB" in keys,
        "SELECT": "TAB" in keys,
        "RESET": "ENTER" in keys,
        "START": "ENTER" in keys,
    }
    return [pressed.get(b, False) for b in buttons]


def save_frame(obs, path):
    """Optional helper: dump an observation to a PNG (requires Pillow)."""
    from PIL import Image

    Image.fromarray(obs).save(path)
    print(f"saved {path}")


# ---------------------------------------------------------------------------
# Player: window + emulator plumbing. You usually don't need to edit below.
# ---------------------------------------------------------------------------
class Player:
    def __init__(self, game, state, scenario):
        self.env = retro.make(
            game=game,
            state=state,
            scenario=scenario,
            render_mode="rgb_array",
        )
        self.buttons = self.env.buttons
        print(f"game={game}  buttons={self.buttons}")

        self.env.reset()
        image = self.env.render()
        assert image.ndim == 3 and image.shape[2] == 3, "expected RGB frame"
        self._image = image
        h, w = image.shape[:2]

        # pick a reasonable window size (integer upscale of the native frame)
        display = pyglet.canvas.get_display()
        screen = display.get_default_screen()
        scale = 1
        while w * (scale + 1) < screen.width * 0.9 and h * (scale + 1) < screen.height * 0.9:
            scale += 1
        self.win = pyglet.window.Window(width=w * scale, height=h * scale)
        self._key_handler = pyglet.window.key.KeyStateHandler()
        self.win.push_handlers(self._key_handler)
        self.win.on_close = self._close

        gl.glEnable(gl.GL_TEXTURE_2D)
        self._tex = gl.GLuint(0)
        gl.glGenTextures(1, ctypes.byref(self._tex))
        gl.glBindTexture(gl.GL_TEXTURE_2D, self._tex)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_S, gl.GL_CLAMP)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_T, gl.GL_CLAMP)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_NEAREST)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_NEAREST)
        gl.glTexImage2D(gl.GL_TEXTURE_2D, 0, gl.GL_RGBA8, w, h, 0, gl.GL_RGB,
                        gl.GL_UNSIGNED_BYTE, None)

        self._step = 0
        self._sim_time = 0.0
        self._wall_time = 0.0

    def _close(self):
        self.env.close()
        sys.exit(0)

    def _draw(self):
        gl.glBindTexture(gl.GL_TEXTURE_2D, self._tex)
        buf = ctypes.cast(self._image.tobytes(), ctypes.POINTER(ctypes.c_short))
        gl.glTexSubImage2D(gl.GL_TEXTURE_2D, 0, 0, 0, self._image.shape[1],
                           self._image.shape[0], gl.GL_RGB, gl.GL_UNSIGNED_BYTE, buf)
        w, h = self.win.width, self.win.height
        pyglet.graphics.draw(
            4, pyglet.gl.GL_QUADS,
            ("v2f", [0, 0, w, 0, w, h, 0, h]),
            ("t2f", [0, 1, 1, 1, 1, 0, 0, 0]),
        )

    def _advance(self, dt):
        # step the emulator forward to catch up to wall-clock, capped
        dt = min(dt, 4 / FPS)
        self._wall_time += dt
        while self._sim_time < self._wall_time:
            self._sim_time += 1 / FPS

            held = set()
            for code, is_down in self._key_handler.items():
                if is_down:
                    for name in dir(keycodes):
                        if getattr(keycodes, name) == code:
                            held.add(name)
            if "ESCAPE" in held:
                self._close()

            action = keys_to_act(held, self.buttons)
            obs, reward, terminated, truncated, info = self.env.step(action)
            self._image = self.env.render()
            self._step += 1

            # >>> your hook <<<
            on_step(obs, reward, terminated, truncated, info, self.env, self._step)

            if terminated or truncated:
                self.env.reset()

    def run(self):
        prev = time.time()
        while True:
            self.win.switch_to()
            self.win.dispatch_events()
            now = time.time()
            self._advance(now - prev)
            prev = now
            self._draw()
            self.win.flip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--game", default=GAME)
    parser.add_argument("--state", default=STATE)
    parser.add_argument("--scenario", default=SCENARIO)
    parser.add_argument("--list", action="store_true",
                        help="list game names containing --game, then exit")
    args = parser.parse_args()

    if args.list:
        needle = "" if args.game == GAME else args.game
        for g in retro.data.list_games():
            if needle.lower() in g.lower():
                print(g)
        return

    Player(game=args.game, state=args.state, scenario=args.scenario).run()


if __name__ == "__main__":
    main()
