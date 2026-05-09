"""
Arm calibration tool.

Walk through every chess square + special positions, manually adjust each
servo angle, and save them all to arm_config.json. The IK node uses this
file at runtime to look up servo positions for each square.

WORKFLOW:
  1. Run this script. It launches a publisher to /nano_serial.
  2. Use the keyboard to adjust servo angles in real time:
       1/q : servo 0 +/-
       2/w : servo 1 +/-
       3/e : servo 2 +/-
       4/r : servo 3 +/-
     (servo 4 is always the gripper, set during gripper calibration step)
  3. Press 'h' to record HOVER position, 'p' to record PICK position
     for the current square. Auto-advances to the next square.
  4. Press 's' to skip to a specific square (manual entry).
  5. Press 'g' to enter gripper-calibration mode (find open/closed angles).
  6. Press 'F' to FINISH and write arm_config.json.

USAGE:
  cd ~/ros2_ws
  source install/setup.bash
  ros2 run robogambit_ik calibrate_arm

  Then, in another terminal, the existing serial bridge node should be
  running, so each angle update reaches the actual robot in real time.

KEY CALIBRATION ORDER (recommended):
  1. Find a safe REST position (arm folded, away from board)
  2. Find GRIPPER OPEN and CLOSED angles
  3. For each of the 64 squares: hover + pick
  4. For graveyard slots (8 white + 8 black off-board positions)
  5. For spare queens (and other spare promotion pieces)

This is the most tedious step in the whole project but only needs to be
done once. ~30-60 minutes total.
"""
import json
import os
import sys
from typing import Dict, List

import rclpy
from rclpy.node import Node
from std_msgs.msg import String


# Where to save the calibration. Same folder as ik_node.py.
def _find_arm_config():
    candidates = [
        os.path.join(
            os.path.expanduser("~"),
            "Downloads", "robogambit", "ros2_ws", "src",
            "robogambit_ik", "robogambit_ik", "arm_config.json",
        ),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "arm_config.json"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return candidates[0]

ARM_CONFIG_PATH = _find_arm_config()


# Recommended ordering: start at rest, then go through squares in a snake
# pattern so you don't have to swing the arm too far between them.
SQUARES_IN_ORDER = []
files = "abcdefgh"
for rank in range(1, 9):
    row = [f"{f}{rank}" for f in (files if rank % 2 else reversed(files))]
    SQUARES_IN_ORDER.extend(row)

# Off-board graveyard slots (you'll calibrate these last)
GRAVEYARD_SLOTS = [f"graveyard_white_{i}" for i in range(8)] + \
                  [f"graveyard_black_{i}" for i in range(8)]

# Spare promotion pieces. Place them physically near the board.
SPARE_SLOTS = ["spare_q", "spare_r", "spare_b", "spare_n"]


# Keyboard input on Linux without curses dependency
import termios
import tty
import select


def get_key_nonblocking(timeout=0.05):
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


class CalibrationNode(Node):
    def __init__(self):
        super().__init__("robogambit_calibrate")
        self.publisher = self.create_publisher(String, "/nano_serial", 10)
        self.angles = [90, 90, 90, 90, 90]  # current arm pose
        self.config = self._load_existing_or_blank()
        self.get_logger().info("Calibration node ready. Publishing on /nano_serial")

    def _load_existing_or_blank(self) -> Dict:
        if os.path.exists(ARM_CONFIG_PATH):
            try:
                with open(ARM_CONFIG_PATH, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "rest": [90, 90, 90, 90, 90],
            "gripper_open": 90,
            "gripper_closed": 30,
            "squares": {},  # filled in: {"e2": {"hover": [..], "pick": [..]}, ...}
        }

    def publish_pose(self):
        """Publish current angles to /nano_serial so robot moves there."""
        msg = String()
        msg.data = ",".join(str(int(a)) for a in self.angles)
        self.publisher.publish(msg)

    def save_to_disk(self):
        with open(ARM_CONFIG_PATH, "w") as f:
            json.dump(self.config, f, indent=2)
        print(f"\n[OK] Saved {ARM_CONFIG_PATH}")
        print(f"     {len(self.config['squares'])} positions recorded")


def print_status(node, target_label, mode_msg=""):
    """Clear screen and show current status."""
    os.system("clear")
    print("=" * 70)
    print("  RoboGambit Arm Calibration")
    print("=" * 70)
    print(f"  Currently calibrating: {target_label}")
    if mode_msg:
        print(f"  {mode_msg}")
    print()
    print(f"  Servo angles:  S0={node.angles[0]:3d}  S1={node.angles[1]:3d}  "
          f"S2={node.angles[2]:3d}  S3={node.angles[3]:3d}  GRIP={node.angles[4]:3d}")
    print()
    print("  --- Adjust servos (each keypress = +/-2 degrees) ---")
    print("  Servo 0:  1 (+) / q (-)        Servo 1:  2 (+) / w (-)")
    print("  Servo 2:  3 (+) / e (-)        Servo 3:  4 (+) / r (-)")
    print("  Gripper:  5 (+) / t (-)")
    print()
    print("  --- Actions ---")
    print("  h    Record HOVER position for current square")
    print("  p    Record PICK position for current square (also advances)")
    print("  n    Skip to NEXT square")
    print("  b    Go BACK to previous square")
    print("  R    Save current angles as REST position")
    print("  G    Save current angles' grip as GRIPPER OPEN")
    print("  C    Save current angles' grip as GRIPPER CLOSED")
    print("  F    FINISH and write arm_config.json")
    print("  Q    Quit WITHOUT saving")
    print("=" * 70)


def main(args=None):
    rclpy.init(args=args)
    node = CalibrationNode()

    # Build the full list of targets to calibrate
    targets = SQUARES_IN_ORDER + GRAVEYARD_SLOTS + SPARE_SLOTS
    target_idx = 0

    delta = 2  # degrees per keypress

    try:
        while target_idx < len(targets):
            target = targets[target_idx]
            mode_msg = (
                f"Move arm to position [{target}]. Press 'h' to save HOVER, "
                f"'p' to save PICK and advance."
            )
            print_status(node, f"{target}  ({target_idx+1}/{len(targets)})", mode_msg)

            key = get_key_nonblocking(timeout=0.1)

            if not key:
                # No input — keep publishing current pose so the arm holds
                node.publish_pose()
                continue

            # Servo adjustments
            adjustments = {
                "1": (0, +delta), "q": (0, -delta),
                "2": (1, +delta), "w": (1, -delta),
                "3": (2, +delta), "e": (2, -delta),
                "4": (3, +delta), "r": (3, -delta),
                "5": (4, +delta), "t": (4, -delta),
            }
            if key in adjustments:
                idx, d = adjustments[key]
                node.angles[idx] = max(0, min(180, node.angles[idx] + d))
                node.publish_pose()
                continue

            # Record actions
            if key == "h":
                if target not in node.config["squares"]:
                    node.config["squares"][target] = {}
                node.config["squares"][target]["hover"] = list(node.angles[:4])
                print(f"\n  ✓ HOVER recorded for {target}")
                continue

            if key == "p":
                if target not in node.config["squares"]:
                    node.config["squares"][target] = {}
                node.config["squares"][target]["pick"] = list(node.angles[:4])
                print(f"\n  ✓ PICK recorded for {target}, advancing to next")
                target_idx += 1
                continue

            if key == "n":
                target_idx += 1
                continue

            if key == "b":
                target_idx = max(0, target_idx - 1)
                continue

            if key == "R":
                node.config["rest"] = list(node.angles)
                print("\n  ✓ REST position saved")
                continue

            if key == "G":
                node.config["gripper_open"] = node.angles[4]
                print(f"\n  ✓ Gripper OPEN angle = {node.angles[4]}")
                continue

            if key == "C":
                node.config["gripper_closed"] = node.angles[4]
                print(f"\n  ✓ Gripper CLOSED angle = {node.angles[4]}")
                continue

            if key == "F":
                node.save_to_disk()
                break

            if key == "Q":
                print("\n  Quit without saving.")
                break

        else:
            # Loop completed normally (all targets done)
            node.save_to_disk()

    except KeyboardInterrupt:
        print("\n  Interrupted. Saving partial config...")
        node.save_to_disk()
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
