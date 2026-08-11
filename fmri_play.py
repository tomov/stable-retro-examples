"""
fmri_play.py — a generic fMRI wrapper for ANY stable-retro game.

Proof of concept. Turns stable-retro games into an fMRI-ready experiment:

  * fixed, centered display (every game letterboxed into ONE window size, so
    Genesis / NES / SNES / Atari all look uniform to the subject);
  * a fixation cross "+" that holds until the scanner trigger key ("=");
  * a declarative CURRICULUM: run -> block (a game) -> instance (a level/state)
    -> play (one attempt, repeated until the instance's time budget elapses);
  * inter-instance / inter-block intervals (IBIs) shown as fixation or a text
    screen; optional instruction / survey screens between blocks;
  * per-play logging for later fMRI analysis: frame-exact .bk2 movie (inputs),
    a .json/.npz of timestamps + game `info` + key on/off boxcars, and the
    starting savestate. Everything is timestamped relative to `scan_start_ts`,
    the moment the "=" trigger arrives.

This mirrors the structure of two existing task frameworks:
  ~/Documents/projects/DBP/vgdl        (VGDL games, fMRI)
  ~/Documents/projects/DBP/mario_task  (Mario, MEG/EEG)
...but stripped to the essentials and made game-agnostic. It uses pyglet only
(already required by the interactive player) — no pygame/psychopy — because
fMRI timing tolerance (TR ~= 1-2 s) is loose relative to the per-frame pacing.

USAGE
    conda activate retro-play
    python fmri_play.py --subject 01 --run 0            # real session
    python fmri_play.py --subject 01 --run 0 --self-test # auto-trigger + random agent

    ESC quits at any time (data saved so far is kept).

EDIT POINTS
    * CURRICULUM ......... the whole experiment structure (games/levels/timing)
    * KEYMAP / TRIGGER_KEY / ADVANCE_KEY ... input mapping
    * Screen constants (SCREEN_W/H, BG) ... display
    * agent_action() ..... only used in --self-test; ignored in real sessions

Run this from THIS directory (not the stable-retro source repo, which shadows
the compiled wheel). Airstriker's installed name is "Airstriker-Genesis-v0".
"""

import argparse
import ctypes
import json
import os
import sys
import time

import numpy as np
import pyglet
from pyglet import gl
from pyglet.window import key as keycodes

import stable_retro as retro


# ===========================================================================
# CONFIG
# ===========================================================================

# --- Display: one fixed window; every game is letterboxed/centered into it ---
SCREEN_W, SCREEN_H = 1024, 768
BG = (0, 0, 0)               # background / letterbox color (r,g,b 0-255)
FPS = 60                     # emulator frames per second

# --- Input keys ---
TRIGGER_KEY = "EQUAL"        # scanner sends this at the first TR ("=")
ADVANCE_KEY = "SPACE"        # experimenter advances instruction/survey screens
QUIT_KEY = "ESCAPE"

# Keyboard -> console-button mapping (same scheme as interactive_play.py).
# In-scanner you'd remap these to the button box; this is the default rig.
def keys_to_act(held, buttons):
    pressed = {
        "BUTTON": "Z" in held, "A": "Z" in held, "B": "X" in held, "C": "C" in held,
        "X": "A" in held, "Y": "S" in held, "Z": "D" in held,
        "L": "Q" in held, "R": "W" in held,
        "UP": "UP" in held, "DOWN": "DOWN" in held,
        "LEFT": "LEFT" in held, "RIGHT": "RIGHT" in held,
        "MODE": "TAB" in held, "SELECT": "TAB" in held,
        "START": "ENTER" in held, "RESET": "ENTER" in held,
    }
    return [pressed.get(b, False) for b in buttons]


# --- CURRICULUM ------------------------------------------------------------
# A run is a list of blocks. A block is one game. Each block has a list of
# instances; each instance names a savestate/level and a duration (seconds).
# During an instance the level is replayed from the start on every game-over
# until `duration` seconds elapse (the "repeat-for-X-seconds" pattern).
#
# Optional screens ("fixation" / "text") can be inserted between things.
# `state=None` uses the game's default start state.
CURRICULUM = {
    # run index -> list of items
    0: [
        {"type": "text", "text": "Welcome. Press SPACE to begin.\n\nUse the arrow keys to move and Z to fire."},
        {"type": "block",
         "game": "Airstriker-Genesis-v0",
         "scenario": None,
         "instructions": "Airstriker: shoot the enemies. Ready?",
         "instances": [
             {"state": None,     "duration": 30.0},   # play default level, on repeat, for 30 s
             {"state": "Level1", "duration": 30.0},
         ]},
        {"type": "text", "text": "Short break. Press SPACE when ready to continue."},
        {"type": "block",
         "game": "Airstriker-Genesis-v0",
         "scenario": None,
         "instructions": "One more block. Ready?",
         "instances": [
             {"state": None, "duration": 20.0},
         ]},
        {"type": "survey",
         "prompt": "How engaging was that? (1 = boring ... 7 = very engaging)",
         "scale": 7},
    ],
}

# Timing intervals (seconds) — the fixation/rest periods around the structure.
PRERUN_FIXATION = 8.0        # "+" after trigger, for scanner to settle
POSTRUN_FIXATION = 8.0       # "+" at end, for HRF to settle
INTER_INSTANCE_FIXATION = 2.0  # "+" between plays/instances
INTER_BLOCK_FIXATION = 4.0     # "+" between blocks


# ===========================================================================
# LOGGING
# ===========================================================================

class Logger:
    """Writes per-run and per-play records under out_dir/sub-XX/run-YY/.

    All timestamps are wall-clock time.time(); analysis subtracts scan_start_ts
    (the "=" trigger moment) to get scanner-relative onsets.
    """

    def __init__(self, out_dir, subject, run):
        self.dir = os.path.join(out_dir, f"sub-{subject}", f"run-{run:02d}")
        os.makedirs(self.dir, exist_ok=True)
        self.subject = subject
        self.run = run
        self.run_meta = {"subject": subject, "run": run, "events": []}
        self.scan_start_ts = None

    def bk2_path(self, block_i, instance_i, play_i):
        return os.path.join(
            self.dir, f"block-{block_i:02d}_inst-{instance_i:02d}_play-{play_i:02d}.bk2")

    def log_event(self, kind, onset, **fields):
        """A coarse timeline event (fixation, instruction, block start, ...)."""
        rel = None if self.scan_start_ts is None else onset - self.scan_start_ts
        self.run_meta["events"].append(
            {"kind": kind, "ts": onset, "onset": rel, **fields})

    def save_play(self, block_i, instance_i, play_i, record):
        """Persist one play's per-frame log as .json (+ .npz for arrays)."""
        base = os.path.join(
            self.dir, f"block-{block_i:02d}_inst-{instance_i:02d}_play-{play_i:02d}")
        s0 = self.scan_start_ts or 0.0
        meta = {
            "game": record["game"], "state": record["state"],
            "scenario": record["scenario"],
            "start_ts": record["frame_ts"][0] if record["frame_ts"] else None,
            "start_onset": (record["frame_ts"][0] - s0) if record["frame_ts"] else None,
            "n_frames": len(record["frame_ts"]),
            "total_reward": float(sum(record["reward"])),
            "final_info": record["info"][-1] if record["info"] else {},
            "key_boxcars": record["key_boxcars"],   # {button: [[onset,offset],...]}
        }
        with open(base + ".json", "w") as f:
            json.dump(meta, f, indent=2, default=float)
        # arrays: per-frame timestamps, rewards, actions, and (optional) RAM
        arrays = {
            "frame_ts": np.asarray(record["frame_ts"], dtype=np.float64),
            "reward": np.asarray(record["reward"], dtype=np.float32),
            "actions": np.asarray(record["actions"], dtype=np.int8),
        }
        if record["ram"]:
            arrays["ram"] = np.asarray(record["ram"], dtype=np.uint8)
        np.savez_compressed(base + ".npz", **arrays)

    def save_run(self):
        self.run_meta["scan_start_ts"] = self.scan_start_ts
        with open(os.path.join(self.dir, "run.json"), "w") as f:
            json.dump(self.run_meta, f, indent=2, default=float)


# ===========================================================================
# EXPERIMENT ENGINE
# ===========================================================================

class Experiment:
    def __init__(self, subject, run, out_dir, self_test=False, save_ram=False):
        self.run_idx = run
        self.self_test = self_test
        self.save_ram = save_ram
        self.log = Logger(out_dir, subject, run)

        self.win = pyglet.window.Window(width=SCREEN_W, height=SCREEN_H,
                                        caption=f"fMRI sub-{subject} run-{run}")
        self.keys = pyglet.window.key.KeyStateHandler()
        self.win.push_handlers(self.keys)
        self.win.on_close = self._quit

        # one reusable GL texture for whatever we blit (game frame or blank)
        gl.glEnable(gl.GL_TEXTURE_2D)
        self._tex = gl.GLuint(0)
        gl.glGenTextures(1, ctypes.byref(self._tex))

        self._label = pyglet.text.Label(
            "", font_size=28, x=SCREEN_W // 2, y=SCREEN_H // 2,
            anchor_x="center", anchor_y="center", color=(255, 255, 255, 255),
            multiline=True, width=int(SCREEN_W * 0.8), align="center")

    # ---- low-level helpers -------------------------------------------------

    def _quit(self):
        try:
            self.log.save_run()
        finally:
            sys.exit(0)

    def _held_keys(self):
        held = set()
        for code, down in self.keys.items():
            if down:
                for name in dir(keycodes):
                    if getattr(keycodes, name) == code:
                        held.add(name)
        if QUIT_KEY in held:
            self._quit()
        return held

    def _pump(self):
        """Process window events; returns the set of currently-held key names."""
        self.win.switch_to()
        self.win.dispatch_events()
        return self._held_keys()

    def _blit_frame(self, frame):
        """Draw an RGB frame letterboxed & centered into the fixed window."""
        h, w = frame.shape[:2]
        scale = min(SCREEN_W / w, SCREEN_H / h)
        dw, dh = int(w * scale), int(h * scale)
        x, y = (SCREEN_W - dw) // 2, (SCREEN_H - dh) // 2

        gl.glBindTexture(gl.GL_TEXTURE_2D, self._tex)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_NEAREST)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_NEAREST)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_S, gl.GL_CLAMP_TO_EDGE)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_T, gl.GL_CLAMP_TO_EDGE)
        buf = frame.tobytes()
        gl.glTexImage2D(gl.GL_TEXTURE_2D, 0, gl.GL_RGB, w, h, 0,
                        gl.GL_RGB, gl.GL_UNSIGNED_BYTE,
                        ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
        pyglet.graphics.draw(
            4, pyglet.gl.GL_QUADS,
            ("v2f", [x, y, x + dw, y, x + dw, y + dh, x, y + dh]),
            ("t2f", [0, 1, 1, 1, 1, 0, 0, 0]))

    def _clear(self):
        gl.glClearColor(BG[0] / 255, BG[1] / 255, BG[2] / 255, 1.0)
        self.win.clear()

    # ---- screens -----------------------------------------------------------

    def show_fixation(self, duration, kind="fixation"):
        """Show '+' for a fixed duration (busy-wait, pumping events)."""
        self.log.log_event(kind, time.time(), duration=duration)
        self._label.text = "+"
        end = time.time() + duration
        while time.time() < end:
            self._pump()
            self._clear()
            self._label.draw()
            self.win.flip()

    def show_text_until_key(self, text, advance=ADVANCE_KEY):
        """Show a message; block until `advance` is pressed (edge-triggered)."""
        self.log.log_event("text", time.time(), text=text)
        self._label.text = text
        was_down = True   # require a fresh press (ignore key already held)
        while True:
            held = self._pump()
            self._clear()
            self._label.draw()
            self.win.flip()
            down = advance in held
            if self.self_test or (down and not was_down):
                return
            was_down = down
            if self.self_test:
                return

    def wait_for_trigger(self):
        """Hold '+' until the scanner trigger ('=') arrives; anchor the clock."""
        self.log.log_event("await_trigger", time.time())
        self._label.text = "+"
        was_down = True
        while True:
            held = self._pump()
            self._clear()
            self._label.draw()
            self.win.flip()
            down = TRIGGER_KEY in held
            if self.self_test or (down and not was_down):
                break
            was_down = down
        self.log.scan_start_ts = time.time()
        self.log.log_event("trigger", self.log.scan_start_ts)

    def run_survey(self, prompt, scale):
        """Minimal Likert survey: number keys 1..scale, ENTER/SPACE to submit."""
        selected = [None]
        num_names = {getattr(keycodes, f"_{i}"): i for i in range(1, scale + 1)
                     if hasattr(keycodes, f"_{i}")}
        was = set()
        while True:
            held = self._pump()
            for code, down in self.keys.items():
                if down and code in num_names and code not in was:
                    selected[0] = num_names[code]
            was = {c for c, d in self.keys.items() if d}
            sel = selected[0]
            self._label.text = (f"{prompt}\n\n"
                                f"Selected: {sel if sel else '-'}"
                                f"\n\n(1-{scale}, then SPACE to submit)")
            self._clear()
            self._label.draw()
            self.win.flip()
            if self.self_test:
                selected[0] = selected[0] or 1
            if (ADVANCE_KEY in held or self.self_test) and selected[0]:
                break
        self.log.log_event("survey", time.time(), prompt=prompt,
                           response=selected[0], scale=scale)

    # ---- gameplay ----------------------------------------------------------

    def play_instance(self, env, buttons, game, state, scenario,
                      duration, block_i, instance_i):
        """Replay `state` from the start on every game-over until `duration`
        seconds elapse. Each attempt is one 'play' with its own log + bk2."""
        instance_end = time.time() + duration
        play_i = 0
        while time.time() < instance_end - 0.05:
            play_i += 1
            bk2 = self.log.bk2_path(block_i, instance_i, play_i)
            env.reset()
            env.unwrapped.record_movie(bk2)

            rec = {"game": game, "state": state, "scenario": scenario,
                   "frame_ts": [], "reward": [], "actions": [], "info": [],
                   "ram": [] if self.save_ram else None,
                   "key_boxcars": {b: [] for b in buttons}}
            key_open = {}   # button -> onset ts, for boxcar bookkeeping

            sim_time = 0.0
            wall_start = time.time()
            while True:
                now = time.time()
                if now >= instance_end:
                    break
                held = self._pump()

                # pace emulator to FPS relative to this play's start
                target = (now - wall_start) * FPS
                stepped = False
                while sim_time < target:
                    sim_time += 1
                    action = (agent_action(buttons) if self.self_test
                              else keys_to_act(held, buttons))
                    obs, reward, terminated, truncated, info = env.step(action)
                    ts = time.time()
                    rec["frame_ts"].append(ts)
                    rec["reward"].append(reward)
                    rec["actions"].append([int(a) for a in action])
                    rec["info"].append(dict(info))
                    if self.save_ram:
                        rec["ram"].append(env.unwrapped.get_ram().copy())
                    # key on/off boxcars
                    for b, a in zip(buttons, action):
                        if a and b not in key_open:
                            key_open[b] = ts
                        elif not a and b in key_open:
                            rel = self.log.scan_start_ts or 0.0
                            rec["key_boxcars"][b].append(
                                [key_open.pop(b) - rel, ts - rel])
                    stepped = True
                    if terminated or truncated:
                        break

                self._clear()
                self._blit_frame(env.render())
                self.win.flip()

                if stepped and (terminated or truncated):
                    break

            # close out any keys still held at play end
            rel = self.log.scan_start_ts or 0.0
            end_ts = time.time()
            for b, onset in key_open.items():
                rec["key_boxcars"][b].append([onset - rel, end_ts - rel])

            env.unwrapped.stop_record()
            self.log.save_play(block_i, instance_i, play_i, rec)
            self.log.log_event("play", wall_start, block=block_i, instance=instance_i,
                               play=play_i, state=state, n_frames=len(rec["frame_ts"]),
                               reward=float(sum(rec["reward"])))

            if time.time() < instance_end - 0.05:
                self.show_fixation(INTER_INSTANCE_FIXATION, kind="ibi")

    def run_block(self, item, block_i):
        game = item["game"]
        scenario = item.get("scenario")
        if item.get("instructions"):
            self.show_text_until_key(item["instructions"])
        env = retro.make(game=game, scenario=scenario, render_mode="rgb_array")
        buttons = env.buttons
        self.log.log_event("block_start", time.time(), block=block_i,
                           game=game, buttons=list(buttons))
        try:
            for instance_i, inst in enumerate(item["instances"]):
                state = inst.get("state")
                if state:
                    env.load_state(state)
                self.play_instance(env, buttons, game, state, scenario,
                                   inst["duration"], block_i, instance_i)
        finally:
            env.close()

    # ---- top-level ---------------------------------------------------------

    def run(self):
        items = CURRICULUM.get(self.run_idx)
        if items is None:
            print(f"No curriculum for run {self.run_idx}. Known runs: "
                  f"{sorted(CURRICULUM)}")
            return

        self.wait_for_trigger()
        self.show_fixation(PRERUN_FIXATION, kind="prerun")

        block_i = 0
        for item in items:
            t = item["type"]
            if t == "text":
                self.show_text_until_key(item["text"])
            elif t == "survey":
                self.run_survey(item["prompt"], item.get("scale", 7))
            elif t == "block":
                if block_i > 0:
                    self.show_fixation(INTER_BLOCK_FIXATION, kind="inter_block")
                self.run_block(item, block_i)
                block_i += 1

        self.show_fixation(POSTRUN_FIXATION, kind="postrun")
        self.log.save_run()
        self._label.text = "Run complete. Thank you!"
        for _ in range(int(FPS * 2)):
            self._pump(); self._clear(); self._label.draw(); self.win.flip()


def agent_action(buttons):
    """Only used in --self-test: a trivial random agent, mostly-no-op."""
    act = [0] * len(buttons)
    # occasionally press a few buttons so bk2/logs have variety
    for i in range(len(buttons)):
        if (i * 7 + int(time.time() * 3)) % 11 == 0:
            act[i] = 1
    return act


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--subject", default="test")
    p.add_argument("--run", type=int, default=0)
    p.add_argument("--out-dir", default=os.path.join(os.getcwd(), "data"))
    p.add_argument("--self-test", action="store_true",
                   help="auto-fire the trigger and drive a random agent (no human)")
    p.add_argument("--save-ram", action="store_true",
                   help="also log full emulator RAM per frame (large files)")
    args = p.parse_args()

    exp = Experiment(subject=args.subject, run=args.run, out_dir=args.out_dir,
                     self_test=args.self_test, save_ram=args.save_ram)
    exp.run()


if __name__ == "__main__":
    main()
