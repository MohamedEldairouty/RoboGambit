"""
RoboGambit IK Translator Node.

Subscribes to:
    /robogambit/move        std_msgs/String   UCI move like "e2e4"

Publishes:
    /nano_serial            std_msgs/String   "angle1,angle2,angle3,angle4,angle5"

Logic:
  1. Receive UCI move from GUI.
  2. Look up calibrated angles for source and destination squares.
  3. Build a pick-and-place sequence:
       hover_above_from -> pick_at_from -> close_gripper ->
       hover_above_from -> hover_above_to -> place_at_to ->
       open_gripper -> hover_above_to -> rest_position
  4. Publish each waypoint as a comma-separated angle string.
  5. Wait between waypoints so servos have time to move.

Special cases:
  - Captures: remove enemy piece to graveyard first, then move attacker.
  - Castling: move king then rook.
  - Promotion: not physically handled (logged as warning) — game continues
    logically; pawn stays on back rank but is treated as the promoted piece
    by the engine.
"""
import json
import os
import time
from typing import Dict, List, Optional, Tuple

import rclpy
from rclpy.node import Node
from std_msgs.msg import String


# === Configuration ===
ARM_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "arm_config.json",
)

# Time to wait between waypoints (seconds) so servos can finish moving.
# Tune this based on your servo speed. Too short = jerky/missed waypoints.
WAYPOINT_DELAY = 1.2

# Number of servos (matches Arduino firmware)
NUM_SERVOS = 5


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

        # Track the chess board state so we know when a move is a capture.
        # Uses python-chess if available; falls back to a manual flag otherwise.
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
                "python-chess not installed. Capture detection will be limited. "
                "Install: pip install python-chess"
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

        # Send arm to rest on startup
        self._publish_angles(self.arm_config["rest"], "rest position")

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
        promotion = uci[4] if len(uci) >= 5 else None  # 'q', 'r', 'b', 'n'

        # Detect capture and special moves before applying
        is_capture = self._is_capture(uci)
        is_castling = self._is_castling(uci)

        if is_castling:
            self.get_logger().info("Castling move — moving king then rook")
            self._execute_castling(uci)
        else:
            if is_capture:
                self.get_logger().info(f"Capture detected on {to_sq}")
                self._remove_to_graveyard(to_sq)

            self._execute_move(from_sq, to_sq)

            if promotion:
                # Promotion is not physically handled by the robot (no spare
                # pieces calibrated). The pawn stays on the back rank.
                # The GUI still tracks it as the promoted piece internally.
                self.get_logger().warn(
                    f"Promotion to {promotion} — physical piece not replaced "
                    "(no spare pieces calibrated). Game continues logically."
                )

        # Update internal board state
        self._apply_move_to_state(uci)

        # Return to rest position so the arm doesn't block the camera
        self._publish_angles(self.arm_config["rest"], "rest position")

        self.get_logger().info("Move complete\n")

    # === Movement primitives ===

    def _execute_move(self, from_sq: str, to_sq: str):
        """Standard pick-and-place: pick up at from_sq, place at to_sq."""
        sequence = [
            ("hover", from_sq, "open"),       # arrive over source
            ("pick",  from_sq, "open"),       # lower
            ("pick",  from_sq, "close"),      # grab piece
            ("hover", from_sq, "close"),      # lift
            ("hover", to_sq,   "close"),      # travel to destination
            ("pick",  to_sq,   "close"),      # lower
            ("pick",  to_sq,   "open"),       # release
            ("hover", to_sq,   "open"),       # retreat
        ]
        for height, sq, gripper in sequence:
            self._move_to(height, sq, gripper)

    def _move_to(self, height: str, square: str, gripper: str):
        """Move arm to (square, height) with gripper in given state."""
        if square not in self.arm_config["squares"]:
            self.get_logger().error(f"Square {square} not calibrated")
            return

        sq_data = self.arm_config["squares"][square]
        # First 4 servos come from the calibration entry
        angles = list(sq_data[height])[:4]
        # 5th servo is the gripper
        gripper_angle = self.arm_config["gripper_open" if gripper == "open" else "gripper_closed"]
        angles.append(gripper_angle)

        self._publish_angles(angles, f"{height} {square} grip={gripper}")

    def _remove_to_graveyard(self, square: str):
        """Captured piece sequence: pick up from `square`, drop in graveyard."""
        graveyard_slot = self._next_graveyard_slot(square)
        if graveyard_slot is None:
            self.get_logger().error("No graveyard slots left!")
            return

        sequence = [
            ("hover", square,         "open"),
            ("pick",  square,         "open"),
            ("pick",  square,         "close"),
            ("hover", square,         "close"),
            ("hover", graveyard_slot, "close"),
            ("pick",  graveyard_slot, "close"),
            ("pick",  graveyard_slot, "open"),
            ("hover", graveyard_slot, "open"),
        ]
        for height, sq, gripper in sequence:
            self._move_to(height, sq, gripper)

    def _execute_castling(self, uci: str):
        """Castling moves both king and rook. UCI: e1g1, e1c1, e8g8, e8c8."""
        if uci == "e1g1":  # White kingside
            self._execute_move("e1", "g1")  # King
            self._execute_move("h1", "f1")  # Rook
        elif uci == "e1c1":  # White queenside
            self._execute_move("e1", "c1")
            self._execute_move("a1", "d1")
        elif uci == "e8g8":  # Black kingside
            self._execute_move("e8", "g8")
            self._execute_move("h8", "f8")
        elif uci == "e8c8":  # Black queenside
            self._execute_move("e8", "c8")
            self._execute_move("a8", "d8")

    # === Helpers ===

    def _publish_angles(self, angles: List[int], label: str = ""):
        """Format angles as 'a,b,c,d,e' and publish to /nano_serial."""
        if len(angles) != NUM_SERVOS:
            self.get_logger().error(
                f"Expected {NUM_SERVOS} angles, got {len(angles)}: {angles}"
            )
            return

        msg = String()
        msg.data = ",".join(str(int(a)) for a in angles)
        self.publisher.publish(msg)
        self.get_logger().info(f"  -> {msg.data}    ({label})")
        time.sleep(WAYPOINT_DELAY)

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
            # Fallback: check known castling UCI strings
            return uci in ("e1g1", "e1c1", "e8g8", "e8c8")
        try:
            move = self._chess.Move.from_uci(uci)
            return self.board.is_castling(move)
        except Exception:
            return False

    def _apply_move_to_state(self, uci: str):
        """Keep our internal board in sync with what's on the physical board."""
        if not self._has_chess_lib:
            return
        try:
            move = self._chess.Move.from_uci(uci)
            if move not in self.board.legal_moves:
                # Try with queen promotion
                move_q = self._chess.Move.from_uci(uci + ("" if len(uci) >= 5 else "q"))
                if move_q in self.board.legal_moves:
                    move = move_q
            self.board.push(move)
        except Exception as e:
            self.get_logger().warn(f"Could not update internal state: {e}")

    def _next_graveyard_slot(self, captured_square: str) -> Optional[str]:
        """Return the graveyard slot key (just one per color — pieces stack)."""
        # Determine if the captured piece was white or black
        if self._has_chess_lib:
            piece = self.board.piece_at(self._chess.parse_square(captured_square))
            if piece is None:
                return None
            is_white = piece.color == self._chess.WHITE
        else:
            # Heuristic fallback when python-chess isn't available
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
