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

# Time between full waypoints (seconds). Bumped up to give time to watch each
# move complete. For production speed, reduce back to ~1.5-2.0s.
WAYPOINT_DELAY = 3.0

# Delay between individual servo commands within the same waypoint.
# Bumped up so each command is visible in the logs and the arm moves
# one servo at a time clearly. For production speed, reduce back to ~0.05.
SUBCOMMAND_DELAY = 1.5


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
                # If anything goes wrong with the capture (no graveyard slot,
                # uncalibrated target, etc.), log and continue with the move
                # anyway — better to make a slightly wrong move than crash.
                try:
                    captured_ok = self._remove_to_graveyard(to_sq)
                    if not captured_ok:
                        self.get_logger().warn(
                            "Capture handling failed — proceeding with the move "
                            "anyway. Captured piece will be displaced by attacker."
                        )
                except Exception as e:
                    self.get_logger().error(f"Capture error: {e}")
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
        """Standard pick-and-place: pick up at from_sq, place at to_sq.

        Servo sequencing prevents the gripper from dragging across pieces:
        - APPROACHING a square: S5 → S2 → S3 → S4 (wrist drops onto piece LAST)
        - LEAVING a square:     S4 lifts FIRST, then S5/S2/S3, then S4 descends
        """
        # Validate both squares are fully calibrated BEFORE moving
        for s in (from_sq, to_sq):
            if s not in self.arm_config["squares"]:
                self.get_logger().error(
                    f"{s} not in arm_config — aborting move {from_sq}{to_sq}"
                )
                return
            entry = self.arm_config["squares"][s]
            missing = [k for k in ("S2", "S3", "S4", "S5") if entry.get(k) is None]
            if missing:
                self.get_logger().error(
                    f"{s} missing {missing} — aborting move {from_sq}{to_sq}. "
                    f"Calibrate this square in arm_config.json before retrying."
                )
                return

        # 1. Open gripper, descend onto source square
        self._open_gripper()
        self._go_to_square(from_sq, approaching=True)

        # 2. Close gripper (grab the piece)
        self._close_gripper()

        # 3. Lift wrist, swing to destination, descend onto destination
        self._go_to_square(to_sq, approaching=False)

        # 4. Open gripper (release)
        self._open_gripper()

    def _remove_to_graveyard(self, square: str) -> bool:
        """Pick up the piece on `square` and drop it in the graveyard.

        Returns True if the capture was successfully handled, False otherwise.
        Caller should still execute the rest of the move even if this fails.
        """
        graveyard_slot = self._next_graveyard_slot(square)
        if graveyard_slot is None:
            self.get_logger().error("Could not determine graveyard slot")
            return False

        # Verify both the captured square and the graveyard slot are calibrated
        for s in (square, graveyard_slot):
            if s not in self.arm_config["squares"]:
                self.get_logger().error(f"{s} not in arm_config — skipping capture")
                return False
            entry = self.arm_config["squares"][s]
            if any(entry.get(k) is None for k in ("S2", "S3", "S4", "S5")):
                self.get_logger().error(
                    f"{s} not fully calibrated — skipping capture. "
                    f"Calibrate this position in arm_config.json."
                )
                return False

        self._execute_move(square, graveyard_slot)
        return True

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

    def _go_to_square(self, square: str, approaching: bool = True):
        """Send the 4 arm servos to a square's calibrated angles.

        Servo order: S2 → S3 → S5 → S4 (wrist LAST so it descends onto
        the piece only after the arm is correctly aligned over the square).

        approaching=True   → first time going to a square. No pre-lift.
        approaching=False  → leaving a square. Lifts S4 first, then runs the
                             same S2 → S3 → S5 → S4 sequence on the target.
        """
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

        # 1. Lift the wrist FIRST if we're carrying a piece away from a square.
        #    Use a generous "safe lift" angle so the gripper clears all pieces.
        if not approaching:
            lift_angle = self.arm_config.get("wrist_lift", 60)  # safe high wrist
            self._send_command(
                f"S4 {int(lift_angle)}",
                f"lift wrist before move",
                waypoint=False,
            )

        # 2. Send S2 (shoulder) → S3 (elbow) → S5 (base rotation).
        #    The wrist (S4) is intentionally LAST so it descends onto the piece
        #    only after the arm is correctly aligned over the square.
        for servo_key in ("S2", "S3", "S5"):
            self._send_command(
                f"{servo_key} {int(sq[servo_key])}",
                f"{square} {servo_key}",
                waypoint=False,
            )

        # 3. Finally drop S4 (wrist) onto the target square.
        self._send_command(
            f"S4 {int(sq['S4'])}",
            f"{square} S4 (descend)",
            waypoint=False,
        )

        # One waypoint-level pause after all servos sent
        time.sleep(WAYPOINT_DELAY)

    def _go_to_rest(self):
        """Send all 5 servos to their rest positions.

        Order: S4(lift) → S2 → S3 → S5 → S4(settle) → S1
        Wrist lifts first so it clears the board; gripper changes LAST.
        """
        rest = self.arm_config.get("rest", {})

        # 1. Lift the wrist away from the board first
        lift_angle = self.arm_config.get("wrist_lift", 60)
        self._send_command(
            f"S4 {int(lift_angle)}",
            "lift wrist before rest",
            waypoint=False,
        )

        # 2. Move arm joints: S2 → S3 → S5
        for servo_key in ("S2", "S3", "S5"):
            if servo_key in rest:
                self._send_command(
                    f"{servo_key} {int(rest[servo_key])}",
                    f"rest {servo_key}",
                    waypoint=False,
                )

        # 3. Settle S4 (wrist) to its rest angle
        if "S4" in rest:
            self._send_command(
                f"S4 {int(rest['S4'])}",
                "rest S4 (settle)",
                waypoint=False,
            )

        # 4. Finally, set S1 (gripper) to its rest state — LAST
        if "S1" in rest:
            self._send_command(
                f"S1 {int(rest['S1'])}",
                "rest S1",
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
            if not self.board.is_capture(move):
                return False
            # Only report a capture if we can actually identify the piece on
            # the destination square. If the internal board has drifted from
            # reality (common during manual test moves), is_capture() might
            # return True but piece_at() returns None — meaning we don't know
            # which graveyard to send the captured piece to. In that case, skip.
            piece = self.board.piece_at(move.to_square)
            if piece is None and not self.board.is_en_passant(move):
                return False
            return True
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
