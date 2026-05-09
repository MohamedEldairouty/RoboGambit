"""
Generate a DUMMY arm_config.json so the IK node can run without crashing
when the real arm isn't calibrated yet.

All angles default to [90, 90, 90, 90] for every square. This is meaningless
for real hardware, but it lets you run the full software pipeline end-to-end
to verify ROS topics flow correctly from GUI -> IK node -> /nano_serial.

Once you have the actual arm, run calibrate_arm.py to overwrite this with
real calibrated values.

Usage:
    cd ros2_ws/src/robogambit_ik/robogambit_ik
    python3 generate_dummy_config.py
"""
import json
import os

OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "arm_config.json")


def main():
    files = "abcdefgh"
    config = {
        "_comment": "DUMMY config for software-only testing. Run calibrate_arm.py for real values.",
        "rest": [90, 90, 90, 90, 90],
        "gripper_open": 90,
        "gripper_closed": 30,
        "squares": {},
    }

    # 64 chess squares
    for rank in range(1, 9):
        for f in files:
            sq = f"{f}{rank}"
            config["squares"][sq] = {
                "hover": [90, 90, 90, 90],
                "pick":  [90, 90, 90, 90],
            }

    # 16 graveyard slots
    for color in ("white", "black"):
        for i in range(8):
            slot = f"graveyard_{color}_{i}"
            config["squares"][slot] = {
                "hover": [90, 90, 90, 90],
                "pick":  [90, 90, 90, 90],
            }

    # 4 spare promotion pieces
    for piece in ("q", "r", "b", "n"):
        slot = f"spare_{piece}"
        config["squares"][slot] = {
            "hover": [90, 90, 90, 90],
            "pick":  [90, 90, 90, 90],
        }

    with open(OUT_PATH, "w") as f:
        json.dump(config, f, indent=2)

    print(f"[OK] Wrote dummy config to {OUT_PATH}")
    print(f"     Total positions: {len(config['squares'])}")
    print(f"     ALL ANGLES ARE PLACEHOLDERS — replace with calibrate_arm.py before using real hardware.")


if __name__ == "__main__":
    main()
