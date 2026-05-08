"""
Standalone test for the ROS move publisher.

Use this to verify your ROS pipeline without needing the GUI.

Setup (in two terminals):

  Terminal 1 — watch the topic:
      source /opt/ros/<distro>/setup.bash      # e.g. humble, iron, jazzy
      ros2 topic echo /robogambit/move

  Terminal 2 — run this test:
      cd ~/Downloads/robogambit
      source venv/bin/activate
      source /opt/ros/<distro>/setup.bash
      python -m scripts.test_publisher

Type a UCI move (e.g. e2e4) and press enter. You should see it appear in
Terminal 1.

Type 'q' to quit.
"""
import os
import sys

# Make the project importable regardless of where this is run from
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from robot.ros_robot import ROSRobot, shutdown_ros


def main():
    print("=" * 60)
    print(" RoboGambit ROS Publisher Test")
    print("=" * 60)
    print(" Type a UCI move (e.g. e2e4, g8f6, e1g1 for castling)")
    print(" Type 'q' or Ctrl+C to quit")
    print("=" * 60)

    robot = ROSRobot()
    if not robot.is_ready():
        print("\n[ERROR] ROSRobot not ready. Did you source ROS setup.bash?")
        print("        Try: source /opt/ros/$ROS_DISTRO/setup.bash")
        return 1

    try:
        while True:
            move = input("\nMove (or 'q' to quit) > ").strip().lower()
            if move in ("q", "quit", "exit"):
                break
            if not move:
                continue

            # Mimic the move_data dict the GUI normally passes
            move_data = {
                "uci": move,
                "from": move[:2] if len(move) >= 4 else "",
                "to": move[2:4] if len(move) >= 4 else "",
                "captured_piece": None,
            }
            robot.send_move(move_data)
    except KeyboardInterrupt:
        print()  # newline after ^C
    finally:
        robot.close()
        shutdown_ros()
        print("[INFO] Cleaned up. Bye.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
