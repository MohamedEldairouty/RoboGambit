"""
Fake robot backend: prints commands to console.

Use this during development when the real arm/ROS aren't available.
"""
from robot.robot_interface import RobotInterface


class FakeRobot(RobotInterface):
    def __init__(self):
        print("[FAKE ROBOT] Initialized (console-only backend)")

    def send_move(self, move_data: dict) -> None:
        print("\n[FAKE ROBOT] === Move Command ===")
        print(f"  Pick from: {move_data['from']}")
        print(f"  Place to:  {move_data['to']}")
        print(f"  Full UCI:  {move_data['uci']}")
        captured = move_data.get("captured_piece")
        if captured:
            print(f"  Captures:  {captured.symbol()} on {move_data['to']}")
        print("[FAKE ROBOT] Move simulated successfully\n")

    def is_ready(self) -> bool:
        return True
