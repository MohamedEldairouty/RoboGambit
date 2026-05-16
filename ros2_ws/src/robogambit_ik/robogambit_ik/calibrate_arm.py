"""
Arm calibration tool.

Walk through every chess square + graveyard + spare-piece slot, manually
adjust each servo angle, and save them all to arm_config.json. The IK node
uses this file at runtime to look up servo positions for each square.

WORKFLOW:
  1. Start the serial bridge in another terminal:
         ros2 run robogambit_ik serial_node
  2. Run this script:
         ros2 run robogambit_ik calibrate_arm
  3. Use the keyboard to adjust servos in real time and record positions.
     The config is saved to disk after every recording, so you can quit
     at any time without losing progress.

KEY CALIBRATION ORDER (recommended):
  1. Find a safe REST pose first (arm folded, away from board) -> press R
  2. Find GRIPPER OPEN / CLOSED angles                          -> press G / C
  3. For each of the 64 squares: hover + pick                   -> h / p
  4. For graveyard slots (8 white + 8 black off-board)
  5. For spare promotion pieces (q, r, b, n)

Total time: ~30-60 minutes. Only needs to be done once.
"""
import json
import os
import sys
from typing import Dict, List, Optional

import rclpy
from rclpy.node import Node
from std_msgs.msg import String


# === Where to save the calibration ===
# Prefer the package source folder so dev edits are visible immediately.
def _find_arm_config() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, "arm_config.json"),
        os.path.join(
            os.path.expanduser("~"),
            "Downloads", "robogambit", "ros2_ws", "src",
            "robogambit_ik", "robogambit_ik", "arm_config.json",
        ),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return candidates[0]


ARM_CONFIG_PATH = _find_arm_config()


# === Target ordering ===
# Snake pattern through the 64 squares so adjacent moves are short.
SQUARES_IN_ORDER: List[str] = []
_files = "abcdefgh"
for _rank in range(1, 9):
    _row = [f"{f}{_rank}" for f in (_files if _rank % 2 else reversed(_files))]
    SQUARES_IN_ORDER.extend(_row)

# Off-board graveyard slots (calibrate after the board)
GRAVEYARD_SLOTS = [f"graveyard_white_{i}" for i in range(8)] + \
                  [f"graveyard_black_{i}" for i in range(8)]

# Spare promotion pieces (place them physically off the board)
SPARE_SLOTS = ["spare_q", "spare_r", "spare_b", "spare_n"]


# === Keyboard input (Linux, no curses dependency) ===
import termios
import tty
import select


def get_key_nonblocking(timeout: float = 0.05) -> str:
    """Read one keystroke from stdin, non-blocking. Returns '' if no key."""
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        rlist, _, _ = select.select([sys.stdin], [], [], timeout)
        if rlist:
            return sys.stdin.read(1)
        return ""
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


# Step sizes the user can cycle through with '+' / '-'.
DELTA_STEPS = [1, 2, 5, 10]


class CalibrationNode(Node):
    def __init__(self):
        super().__init__("robogambit_calibrate")
        self.publisher = self.create_publisher(String, "/nano_serial", 10)
        self.config = self._load_existing_or_blank()
        # Start from saved rest if we have one, otherwise neutral 90s.
        self.angles: List[int] = list(self.config.get("rest", [90, 90, 90, 90, 90]))
        self.get_logger().info(f"Calibration node ready. Config: {ARM_CONFIG_PATH}")

    def _load_existing_or_blank(self) -> Dict:
        if os.path.exists(ARM_CONFIG_PATH):
            try:
                with open(ARM_CONFIG_PATH, "r") as f:
                    data = json.load(f)
                # Strip the "DUMMY" comment if present so a real save replaces it.
                data.pop("_comment", None)
                data.setdefault("rest", [90, 90, 90, 90, 90])
                data.setdefault("gripper_open", 90)
                data.setdefault("gripper_closed", 30)
                data.setdefault("squares", {})
                return data
            except Exception:
                pass
        return {
            "rest": [90, 90, 90, 90, 90],
            "gripper_open": 90,
            "gripper_closed": 30,
            "squares": {},
        }

    def publish_pose(self) -> None:
        """Publish current angles to /nano_serial so the arm tracks live edits."""
        msg = String()
        msg.data = ",".join(str(int(a)) for a in self.angles)
        self.publisher.publish(msg)

    def publish_angles(self, angles: List[int]) -> None:
        """Move arm to a specific pose (e.g. previewing a saved square)."""
        self.angles = [int(a) for a in angles]
        self.publish_pose()

    def save_to_disk(self) -> None:
        tmp = ARM_CONFIG_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.config, f, indent=2)
        os.replace(tmp, ARM_CONFIG_PATH)


def _target_label(target: str, idx: int, total: int, calibrated: bool) -> str:
    mark = "[done]" if calibrated else "[    ]"
    return f"{mark}  {target}   ({idx + 1}/{total})"


def _is_calibrated(config: Dict, target: str) -> bool:
    sq = config["squares"].get(target)
    return bool(sq and "hover" in sq and "pick" in sq)


def _progress(config: Dict, group: List[str]) -> str:
    done = sum(1 for t in group if _is_calibrated(config, t))
    return f"{done}/{len(group)}"


def _read_line_blocking(prompt: str) -> str:
    """Temporarily restore cooked mode and read a full line (for square-jump input)."""
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)  # ensure cooked
        sys.stdout.write(prompt)
        sys.stdout.flush()
        return sys.stdin.readline().strip()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def print_status(node: CalibrationNode, target: str, idx: int, total: int,
                 delta: int, status_msg: str) -> None:
    """Redraw the TUI. Called only when something actually changed."""
    cfg = node.config
    os.system("clear")
    print("=" * 72)
    print("  RoboGambit Arm Calibration")
    print("=" * 72)
    print(f"  Target:  {_target_label(target, idx, total, _is_calibrated(cfg, target))}")
    print(f"  Progress:  squares {_progress(cfg, SQUARES_IN_ORDER)}   "
          f"graveyard {_progress(cfg, GRAVEYARD_SLOTS)}   "
          f"spares {_progress(cfg, SPARE_SLOTS)}")
    print()
    print(f"  Live angles:  S0={node.angles[0]:3d}  S1={node.angles[1]:3d}  "
          f"S2={node.angles[2]:3d}  S3={node.angles[3]:3d}  GRIP={node.angles[4]:3d}")
    print(f"  Saved:  rest={cfg['rest']}   open={cfg['gripper_open']}   closed={cfg['gripper_closed']}")
    sq_data = cfg["squares"].get(target, {})
    hov = sq_data.get("hover", "-")
    pck = sq_data.get("pick", "-")
    print(f"  This target:  hover={hov}   pick={pck}")
    print()
    print(f"  Step size: {delta} deg   (change with + / -)")
    print()
    print("  --- Adjust servos ---")
    print("    Servo 0:  1 (+) / q (-)        Servo 1:  2 (+) / w (-)")
    print("    Servo 2:  3 (+) / e (-)        Servo 3:  4 (+) / r (-)")
    print("    Gripper:  5 (+) / t (-)        also: o=open, c=close")
    print()
    print("  --- Navigate ---")
    print("    n  next target        b  previous target        j  jump to square")
    print("    v  preview saved hover for this target")
    print("    V  preview saved pick  for this target")
    print("    z  send arm to REST pose (safety)")
    print()
    print("  --- Record (saves to disk immediately) ---")
    print("    h  record HOVER for current target")
    print("    p  record PICK for current target (auto-lifts to hover & advances)")
    print("    R  save current pose as REST")
    print("    G  save gripper angle as OPEN     C  save gripper angle as CLOSED")
    print()
    print("    F  finish              Q  quit")
    print("=" * 72)
    if status_msg:
        print(f"  >> {status_msg}")
    else:
        print()


def main(args=None):
    rclpy.init(args=args)
    node = CalibrationNode()
    node.publish_pose()  # snap to starting pose

    targets = SQUARES_IN_ORDER + GRAVEYARD_SLOTS + SPARE_SLOTS
    idx = 0
    delta_i = 1  # index into DELTA_STEPS -> default 2 deg
    status_msg = f"Loaded {len(node.config['squares'])} previously-saved squares."
    need_redraw = True

    try:
        while True:
            if idx >= len(targets):
                idx = len(targets) - 1  # clamp

            target = targets[idx]
            delta = DELTA_STEPS[delta_i]

            if need_redraw:
                print_status(node, target, idx, len(targets), delta, status_msg)
                need_redraw = False

            key = get_key_nonblocking(timeout=0.1)
            if not key:
                node.publish_pose()  # hold pose so servos don't drift
                continue

            # --- Servo adjustments ---
            adjustments = {
                "1": (0, +delta), "q": (0, -delta),
                "2": (1, +delta), "w": (1, -delta),
                "3": (2, +delta), "e": (2, -delta),
                "4": (3, +delta), "r": (3, -delta),
                "5": (4, +delta), "t": (4, -delta),
            }
            if key in adjustments:
                i, d = adjustments[key]
                node.angles[i] = max(0, min(180, node.angles[i] + d))
                node.publish_pose()
                status_msg = f"S{i} = {node.angles[i]}"
                need_redraw = True
                continue

            # --- Step size ---
            if key == "+":
                delta_i = (delta_i + 1) % len(DELTA_STEPS)
                status_msg = f"Step size = {DELTA_STEPS[delta_i]} deg"
                need_redraw = True
                continue
            if key == "-":
                delta_i = (delta_i - 1) % len(DELTA_STEPS)
                status_msg = f"Step size = {DELTA_STEPS[delta_i]} deg"
                need_redraw = True
                continue

            # --- Gripper shortcuts ---
            if key == "o":
                node.angles[4] = node.config["gripper_open"]
                node.publish_pose()
                status_msg = "Gripper -> OPEN"
                need_redraw = True
                continue
            if key == "c":
                node.angles[4] = node.config["gripper_closed"]
                node.publish_pose()
                status_msg = "Gripper -> CLOSED"
                need_redraw = True
                continue

            # --- Record actions (autosave after every change) ---
            if key == "h":
                node.config["squares"].setdefault(target, {})
                node.config["squares"][target]["hover"] = list(node.angles[:4])
                node.save_to_disk()
                status_msg = f"HOVER recorded for {target}  (saved)"
                need_redraw = True
                continue

            if key == "p":
                node.config["squares"].setdefault(target, {})
                node.config["squares"][target]["pick"] = list(node.angles[:4])
                node.save_to_disk()
                # Safety: lift back to the saved hover before advancing so the
                # gripper doesn't drag across pieces on the way to the next square.
                hover = node.config["squares"][target].get("hover")
                if hover:
                    node.publish_angles(list(hover) + [node.angles[4]])
                if idx + 1 < len(targets):
                    idx += 1
                status_msg = f"PICK recorded for {target}  (saved, advanced)"
                need_redraw = True
                continue

            if key == "R":
                node.config["rest"] = list(node.angles)
                node.save_to_disk()
                status_msg = f"REST pose saved: {node.angles}"
                need_redraw = True
                continue

            if key == "G":
                node.config["gripper_open"] = node.angles[4]
                node.save_to_disk()
                status_msg = f"Gripper OPEN = {node.angles[4]}  (saved)"
                need_redraw = True
                continue

            if key == "C":
                node.config["gripper_closed"] = node.angles[4]
                node.save_to_disk()
                status_msg = f"Gripper CLOSED = {node.angles[4]}  (saved)"
                need_redraw = True
                continue

            # --- Navigation ---
            if key == "n":
                if idx + 1 < len(targets):
                    idx += 1
                status_msg = f"-> {targets[idx]}"
                need_redraw = True
                continue

            if key == "b":
                idx = max(0, idx - 1)
                status_msg = f"<- {targets[idx]}"
                need_redraw = True
                continue

            if key == "j":
                name = _read_line_blocking("\n  Jump to (e.g. e4, graveyard_white_3, spare_q): ")
                if name in targets:
                    idx = targets.index(name)
                    status_msg = f"Jumped to {name}"
                else:
                    status_msg = f"Unknown target: {name!r}"
                need_redraw = True
                continue

            # --- Preview / safety ---
            if key == "v":
                hov = node.config["squares"].get(target, {}).get("hover")
                if hov:
                    node.publish_angles(list(hov) + [node.angles[4]])
                    status_msg = f"Previewing HOVER {hov}"
                else:
                    status_msg = "No hover recorded for this target yet."
                need_redraw = True
                continue

            if key == "V":
                pck = node.config["squares"].get(target, {}).get("pick")
                if pck:
                    node.publish_angles(list(pck) + [node.angles[4]])
                    status_msg = f"Previewing PICK {pck}"
                else:
                    status_msg = "No pick recorded for this target yet."
                need_redraw = True
                continue

            if key == "z":
                node.publish_angles(list(node.config["rest"]))
                status_msg = "Sent arm to REST pose."
                need_redraw = True
                continue

            # --- Finish / quit ---
            if key == "F":
                node.save_to_disk()
                print(f"\n[OK] Finished. Saved to {ARM_CONFIG_PATH}")
                print(f"     {len(node.config['squares'])} positions recorded")
                break

            if key == "Q":
                print(f"\nQuit. Last state saved at {ARM_CONFIG_PATH}")
                break

            # Unknown key: silently ignore (no redraw needed).

    except KeyboardInterrupt:
        print(f"\nInterrupted. Last state saved at {ARM_CONFIG_PATH}")
    finally:
        try:
            node.save_to_disk()
        except Exception:
            pass
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
