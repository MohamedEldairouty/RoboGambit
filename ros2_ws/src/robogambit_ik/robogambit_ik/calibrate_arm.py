#!/usr/bin/env python3
"""
RoboGambit Arm Calibration — angle entry tool.

Since the arm calibration is done directly using the Arduino's own
serial interface (typing "S1 155" etc. into the Serial Monitor),
this script just collects the calibrated angles for each square
and writes them to arm_config.json.

USAGE:
    python3 calibrate_arm.py

You'll be prompted for each of the 64 squares + 2 graveyard slots
in a sensible order (snake pattern). For each one, enter:

    S2  S3  S4  S5    (space-separated, e.g. "140 25 95 86")

If you press Enter without typing anything, the entry is skipped
(so you can fill it in later).

After all positions, you'll be asked for:
    - gripper_open angle
    - gripper_closed angle
    - rest position (all 5 servos)
    - Arduino speed (ms per degree)

The file is saved as arm_config.json in the same folder as this script.
"""

import json
import os
import sys
from typing import Dict, List, Optional


HERE = os.path.dirname(os.path.abspath(__file__))
ARM_CONFIG_PATH = os.path.join(HERE, "arm_config.json")


# Snake pattern through the board: rank 1 left-to-right, rank 2 right-to-left, etc.
# This minimizes arm sweep between consecutive squares during calibration.
def build_square_order() -> List[str]:
    files = "abcdefgh"
    order = []
    for rank in range(1, 9):
        row = [f"{f}{rank}" for f in (files if rank % 2 else reversed(files))]
        order.extend(row)
    return order


SQUARES = build_square_order()
GRAVEYARD_SLOTS = ["graveyard_white", "graveyard_black"]
ALL_TARGETS = SQUARES + GRAVEYARD_SLOTS


# ─── Helpers ──────────────────────────────────────────────────────────

def load_existing() -> Dict:
    """Load arm_config.json if it exists, else return an empty skeleton."""
    if os.path.exists(ARM_CONFIG_PATH):
        try:
            with open(ARM_CONFIG_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "speed": 15,
        "gripper_open": 165,
        "gripper_closed": 180,
        "rest": {"S1": 180, "S2": 110, "S3": 30, "S4": 85, "S5": 90},
        "squares": {},
    }


def save(config: Dict):
    with open(ARM_CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)
    print(f"  ✔ Saved → {ARM_CONFIG_PATH}")


def prompt_int(label: str, default: Optional[int] = None) -> Optional[int]:
    """Ask user for one int. Empty input returns default."""
    hint = f" [{default}]" if default is not None else ""
    raw = input(f"  {label}{hint}: ").strip()
    if raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"    ⚠ '{raw}' isn't a number, skipping.")
        return default


def prompt_square_angles(label: str, existing: Optional[Dict]) -> Optional[Dict]:
    """Ask for 4 angles (S2 S3 S4 S5) on one line. Empty → skip."""
    if existing:
        print(f"  Current: S2={existing.get('S2')} S3={existing.get('S3')} "
              f"S4={existing.get('S4')} S5={existing.get('S5')}")

    raw = input(f"  {label} (S2 S3 S4 S5, blank=skip): ").strip()
    if not raw:
        return existing  # keep existing or stay empty

    parts = raw.split()
    if len(parts) != 4:
        print(f"    ⚠ Need 4 numbers, got {len(parts)}. Skipping.")
        return existing

    try:
        nums = [int(p) for p in parts]
    except ValueError:
        print(f"    ⚠ Not all numbers. Skipping.")
        return existing

    return {"S2": nums[0], "S3": nums[1], "S4": nums[2], "S5": nums[3]}


# ─── Main flow ─────────────────────────────────────────────────────────

def run():
    print("=" * 70)
    print("  RoboGambit Arm Calibration — Angle Entry Tool")
    print("=" * 70)
    print(f"  Config file: {ARM_CONFIG_PATH}")
    print(f"  Existing entries will be preserved unless overwritten.")
    print()

    config = load_existing()
    config.setdefault("squares", {})

    print("─── Position entry ───")
    print("  For each target, enter 4 numbers: S2 S3 S4 S5 (e.g. '140 25 95 86')")
    print("  Press Enter alone to keep the current value (or skip).")
    print("  Type 'q' at any prompt to stop entering positions and save.\n")

    for i, target in enumerate(ALL_TARGETS):
        existing = config["squares"].get(target)
        label = f"[{i+1:2d}/{len(ALL_TARGETS)}] {target}"
        raw = input(f"  {label} (S2 S3 S4 S5, blank=skip, q=quit): ").strip()
        if raw.lower() == "q":
            print("  Stopping early.")
            break
        if not raw:
            continue
        parts = raw.split()
        if len(parts) != 4:
            print(f"    ⚠ Need 4 numbers, got {len(parts)}. Skipping.")
            continue
        try:
            nums = [int(p) for p in parts]
        except ValueError:
            print(f"    ⚠ Not all numbers. Skipping.")
            continue
        config["squares"][target] = {
            "S2": nums[0], "S3": nums[1], "S4": nums[2], "S5": nums[3],
        }

    # Save after squares so partial progress isn't lost
    save(config)

    print("\n─── Gripper & rest ───")
    g_open = prompt_int("gripper_open angle",
                        config.get("gripper_open", 165))
    if g_open is not None:
        config["gripper_open"] = g_open

    g_close = prompt_int("gripper_closed angle",
                         config.get("gripper_closed", 180))
    if g_close is not None:
        config["gripper_closed"] = g_close

    speed = prompt_int("Arduino speed (ms/deg, 1-100)",
                       config.get("speed", 15))
    if speed is not None:
        config["speed"] = max(1, min(100, speed))

    print("\n  Rest position (5 servos):")
    rest = dict(config.get("rest", {}))
    for key, default in (("S1", 180), ("S2", 110), ("S3", 30), ("S4", 85), ("S5", 90)):
        val = prompt_int(f"rest.{key}", rest.get(key, default))
        if val is not None:
            rest[key] = val
    config["rest"] = rest

    save(config)

    # Summary
    print("\n─── Summary ───")
    print(f"  Squares calibrated: {len(config['squares'])}")
    missing = [t for t in SQUARES if t not in config["squares"]]
    if missing:
        print(f"  ⚠ Still missing {len(missing)} squares: {', '.join(missing[:10])}"
              + (" ..." if len(missing) > 10 else ""))
    for g in GRAVEYARD_SLOTS:
        ok = "✔" if g in config["squares"] else "✗"
        print(f"  Graveyard {g}: {ok}")
    print(f"  gripper_open={config['gripper_open']}, "
          f"gripper_closed={config['gripper_closed']}, speed={config['speed']}")


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        print("\n  Interrupted. Last save was after the squares loop.")
        sys.exit(0)
