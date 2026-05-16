"""
RoboGambit IK Translator Node.

Subscribes to:
    /robogambit/move        std_msgs/String   UCI move like "e2e4"

Publishes:
    /nano_serial            std_msgs/String   single-servo commands like "S1 155"

Protocol matches friend's Arduino firmware (arduino/chess_arm_controller):
  - 9600 baud
  - One servo per line: "S1 155\n", "S2 80\n", ...
  - Arduino smoothly interpolates to target (delay set by SPEED command)
  - Per-servo limits enforced on the Arduino itself

Logic:
  1. Receive UCI move from GUI.
  2. For each waypoint in the pick-and-place sequence, send the 4 arm
     servos (S2, S3, S4, S5) to the calibrated angles for the target
     square, plus a gripper command (S1) when opening or closing.
  3. Wait between waypoints so servos have time to finish moving.

Special cases:
  - Captures: remove enemy piece to graveyard first, then move attacker.
  - Castling: move king then rook.
  - Promotion: NOT physically handled — logged as warning. The pawn stays
    on the back rank; engine treats it as the promoted piece logically.
"""
import json
import os
import time
from typing import Dict, List, Optional

import rclpy
from rclpy.node import Node
from std_msgs.msg import String


# === Configuration ===

# Try common locations for arm_config.json (dev path first, then install)
def _find_arm_config() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.expanduser("~/Downloads/robogambit/ros2_ws/src/robogambit_ik/robogambit_ik/arm_config.json"),
        os.path.join(here, "arm_config.json"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return candidates[0]


ARM_CONFIG_PATH = _find_arm_config()

# Time between full waypoints (seconds). Tune based on how long the
# slowest single servo takes to traverse its full range. With the Arduino's
# default 15 ms/deg and a 180° swing this is ~2.7 s, so 1.5 s is enough
# for typical moves (~50-80° between adjacent squares).
WAYPOINT_DELAY = 1.5

# Small delay between individual servo commands within the same waypoint.
# The Arduino processes them serially but each one is non-blocking once
# it returns "Done", so spacing them helps avoid serial buffer overflow.
SUBCOMMAND_DELAY = 0.05


class IKTranslatorNode(Node):
    def __init__(self):
        super().__init__("robogambit_ik")

        # Load calibrated arm positions
        self.arm_config = self._load_arm_config()
        if self.arm_config is None:
            self.get_logger().error(
                f"Cannot load arm config from {ARM_CONFIG_PATH}. "
                f"Run calibrate_arm.py first to generate it."
            )
            return

        # Track the chess board state to detect captures and special moves.
        try:
            import chess
            self._chess = chess
            self.board = chess.Board()
            self._has_chess_lib = True
            self.get_logger().info("Using python-chess for state tracking")
        except ImportError:
            self._chess = None
            self.board = None
            self._has_chess_lib = False
            self.get_logger().warn(
                "python-chess not installed. Capture detection will be limited."
            )

        # ROS pub/sub
        self.subscription = self.create_subscription(
            String, "/robogambit/move", self.on_move_received, 10,
        )
        self.publisher = self.create_publisher(String, "/nano_serial", 10)

        self.get_logger().info("RoboGambit IK Translator ready")
        self.get_logger().info(f"  Listening on:  /robogambit/move")
        self.get_logger().info(f"  Publishing to: /nano_serial")
        self.get_logger().info(f"  Squares loaded: {len(self.arm_config.get('squares', {}))}")
        self.get_logger().info(f"  Config: {ARM_CONFIG_PATH}")

        # Set Arduino speed if specified
        if "speed" in self.arm_config:
            self._send_command(f"SPEED {self.arm_config['speed']}", "set arm speed")

        # Send arm to rest on startup
        self._go_to_rest()

    # === Public callback ===

    def on_move_received(self, msg: String):
        """Main entry point: a UCI move string arrived from the GUI."""
        uci = msg.data.strip()
        self.get_logger().info(f"Received move: {uci}")

        if len(uci) < 4:
            self.get_logger().error(f"Invalid UCI move: {uci}")
            return

        from_sq = uci[:2]
        to_sq = uci[2:4]
        promotion = uci[4] if len(uci) >= 5 else None

        is_capture = self._is_capture(uci)
        is_castling = self._is_castling(uci)

        if is_castling:
            self.get_logger().info("Castling — moving king then rook")
            self._execute_castling(uci)
        else:
            if is_capture:
                self.get_logger().info(f"Capture detected on {to_sq}")
                self._remove_to_graveyard(to_sq)
            self._execute_move(from_sq, to_sq)

            if promotion:
                self.get_logger().warn(
                    f"Promotion to {promotion} — physical piece not replaced "
                    "(no spare pieces calibrated). Game continues logically."
                )

        # Sync internal board state
        self._apply_move_to_state(uci)

        # Return arm to rest (clears camera view)
        self._go_to_rest()
        self.get_logger().info("Move complete\n")

    # === High-level movement primitives ===

    def _execute_move(self, from_sq: str, to_sq: str):
        """Standard pick-and-place: pick up at from_sq, place at to_sq."""
        # 1. Open gripper, swing to source square
        self._open_gripper()
        self._go_to_square(from_sq)

        # 2. Close gripper (grab the piece)
        self._close_gripper()

        # 3. Swing to destination square (gripper holding piece)
        self._go_to_square(to_sq)

        # 4. Open gripper (release)
        self._open_gripper()

    def _remove_to_graveyard(self, square: str):
        """Pick up the piece on `square` and drop it in the graveyard."""
        graveyard_slot = self._next_graveyard_slot(square)
        if graveyard_slot is None:
            self.get_logger().error("Could not determine graveyard slot")
            return
        self._execute_move(square, graveyard_slot)

    def _execute_castling(self, uci: str):
        """Castling: move king, then move the rook."""
        if uci == "e1g1":
            self._execute_move("e1", "g1")
            self._execute_move("h1", "f1")
        elif uci == "e1c1":
            self._execute_move("e1", "c1")
            self._execute_move("a1", "d1")
        elif uci == "e8g8":
            self._execute_move("e8", "g8")
            self._execute_move("h8", "f8")
        elif uci == "e8c8":
            self._execute_move("e8", "c8")
            self._execute_move("a8", "d8")

    # === Low-level servo control ===

    def _go_to_square(self, square: str):
        """Send the 4 arm servos (S2-S5) to a square's calibrated angles."""
        if square not in self.arm_config["squares"]:
            self.get_logger().error(f"Square {square} not calibrated")
            return

        sq = self.arm_config["squares"][square]

        # Check for missing/null angles (placeholders for uncalibrated squares)
        missing = [k for k in ("S2", "S3", "S4", "S5") if sq.get(k) is None]
        if missing:
            self.get_logger().error(
                f"Square {square} not calibrated: missing {missing}. "
                f"Edit arm_config.json to fill in these values."
            )
            return

        # Send S2, S3, S4, S5 in order
        for servo_key in ("S2", "S3", "S4", "S5"):
            self._send_command(
                f"{servo_key} {int(sq[servo_key])}",
                f"{square} {servo_key}",
                waypoint=False,
            )
        # One waypoint-level pause after all 4 servos sent
        time.sleep(WAYPOINT_DELAY)

    def _go_to_rest(self):
        """Send all 5 servos to their rest positions."""
        rest = self.arm_config.get("rest", {})
        for servo_key in ("S1", "S2", "S3", "S4", "S5"):
            if servo_key in rest:
                self._send_command(
                    f"{servo_key} {int(rest[servo_key])}",
                    f"rest {servo_key}",
                    waypoint=False,
                )
        time.sleep(WAYPOINT_DELAY)

    def _open_gripper(self):
        angle = self.arm_config.get("gripper_open", 130)
        self._send_command(f"S1 {int(angle)}", "gripper OPEN", waypoint=True)

    def _close_gripper(self):
        angle = self.arm_config.get("gripper_closed", 180)
        self._send_command(f"S1 {int(angle)}", "gripper CLOSE", waypoint=True)

    def _send_command(self, command: str, label: str = "", waypoint: bool = True):
        """Publish a single Arduino command (e.g. 'S1 155' or 'SPEED 20').

        waypoint=True means wait WAYPOINT_DELAY after sending (use for full moves).
        waypoint=False means a short SUBCOMMAND_DELAY (use within a multi-servo move).
        """
        msg = String()
        msg.data = command
        self.publisher.publish(msg)
        self.get_logger().info(f"  -> {command}    ({label})")
        time.sleep(WAYPOINT_DELAY if waypoint else SUBCOMMAND_DELAY)

    # === State tracking helpers ===

    def _is_capture(self, uci: str) -> bool:
        if not self._has_chess_lib:
            return False
        try:
            move = self._chess.Move.from_uci(uci)
            return self.board.is_capture(move)
        except Exception:
            return False

    def _is_castling(self, uci: str) -> bool:
        if not self._has_chess_lib:
            return uci in ("e1g1", "e1c1", "e8g8", "e8c8")
        try:
            move = self._chess.Move.from_uci(uci)
            return self.board.is_castling(move)
        except Exception:
            return False

    def _apply_move_to_state(self, uci: str):
        if not self._has_chess_lib:
            return
        try:
            move = self._chess.Move.from_uci(uci)
            if move not in self.board.legal_moves:
                move_q = self._chess.Move.from_uci(uci + ("" if len(uci) >= 5 else "q"))
                if move_q in self.board.legal_moves:
                    move = move_q
            self.board.push(move)
        except Exception as e:
            self.get_logger().warn(f"Could not update internal state: {e}")

    def _next_graveyard_slot(self, captured_square: str) -> Optional[str]:
        """One graveyard per color — pieces pile up."""
        if self._has_chess_lib:
            piece = self.board.piece_at(self._chess.parse_square(captured_square))
            if piece is None:
                return None
            is_white = piece.color == self._chess.WHITE
        else:
            is_white = int(captured_square[1]) <= 4
        return "graveyard_white" if is_white else "graveyard_black"

    def _load_arm_config(self) -> Optional[Dict]:
        if not os.path.exists(ARM_CONFIG_PATH):
            return None
        try:
            with open(ARM_CONFIG_PATH, "r") as f:
                return json.load(f)
        except Exception as e:
            self.get_logger().error(f"Failed to load arm config: {e}")
            return None


def main(args=None):
    rclpy.init(args=args)
    node = IKTranslatorNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
