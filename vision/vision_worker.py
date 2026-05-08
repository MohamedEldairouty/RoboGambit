"""
Qt-side vision worker.

Runs the camera in a background thread and emits frames + detected moves
as Qt signals. The GUI connects to these signals to update its display.

Two operating modes (toggleable from GUI):

  MANUAL mode (default)
    - GUI calls set_reference() before each move.
    - GUI calls request_detection(board) after the player moves.
    - One detection per button press.

  AUTO mode (motion-then-stillness state machine)
    - GUI calls set_auto_enabled(True, board) once.
    - Worker watches motion level continuously.
    - Player puts hand on board (motion spike) -> enters MOTION state.
    - Player removes hand, scene stays still for AUTO_STABLE_DURATION
      seconds -> triggers detection automatically.
    - Worker emits move_detected exactly the same as manual mode.
    - GUI calls set_auto_baseline(board) after AI/robot finishes,
      to reset the state machine for the next human move.

States:
    STABLE    : nothing moving, baseline frame held
    MOTION    : motion detected, waiting for it to stop
    PENDING   : motion stopped, waiting for stillness duration
    COOLDOWN  : detection just emitted; waiting for GUI baseline reset
"""
import time

import cv2
import numpy as np
import chess
from PySide6.QtCore import QThread, Signal, Slot, QMutex, QMutexLocker
from PySide6.QtGui import QImage

from config.settings import (
    CAMERA_INDEX,
    SQDICT_PATH,
    MOVE_THRESHOLD,
    MIN_CONTOUR_AREA,
    PREVIEW_FPS,
    FRAME_ROTATION,
    AUTO_STILLNESS_THRESHOLD,
    AUTO_MOTION_THRESHOLD,
    MOTION_PIXEL_DELTA,
    AUTO_STABLE_DURATION,
    AUTO_MAX_CHANGED_SQUARES,
)
from vision.move_detector import MoveDetector


# Map rotation degrees to OpenCV constants for fast lookup
_ROTATION_MAP = {
    90: cv2.ROTATE_90_CLOCKWISE,
    180: cv2.ROTATE_180,
    270: cv2.ROTATE_90_COUNTERCLOCKWISE,
}


def _rotate_frame(frame):
    """Apply FRAME_ROTATION to a captured frame (no-op if 0)."""
    if FRAME_ROTATION in _ROTATION_MAP:
        return cv2.rotate(frame, _ROTATION_MAP[FRAME_ROTATION])
    return frame


# Auto-detect state names (also emitted via auto_state_changed signal)
STATE_STABLE = "stable"
STATE_MOTION = "motion"
STATE_PENDING = "pending"
STATE_COOLDOWN = "cooldown"


class VisionWorker(QThread):
    # === Signals ===
    frame_ready = Signal(QImage)
    move_detected = Signal(str, str)
    status = Signal(str)
    error = Signal(str)
    square_highlight = Signal(str, str)
    auto_state_changed = Signal(str)  # one of STATE_*

    def __init__(self, camera_index=None, sqdict_path=None, parent=None):
        super().__init__(parent)
        self._camera_index = camera_index if camera_index is not None else CAMERA_INDEX
        self._sqdict_path = sqdict_path or SQDICT_PATH

        self._cap = None
        self._detector = None
        self._ref_frame = None
        self._latest_frame = None
        self._prev_frame_gray = None  # For motion-level computation
        self._running = False

        self._mutex = QMutex()
        self._detection_requested = False
        self._detection_board = None

        # Auto-detect state
        self._auto_enabled = False
        self._auto_state = STATE_STABLE
        self._auto_board = None
        self._still_since = None  # timestamp when we entered PENDING

    # === Public slots (called from GUI thread) ===

    @Slot()
    def set_reference(self):
        """Manual mode: capture the 'before' frame for the next detection."""
        with QMutexLocker(self._mutex):
            if self._latest_frame is not None:
                self._ref_frame = self._latest_frame.copy()
                self.status.emit("Reference frame saved")
            else:
                self.error.emit("No camera frame yet — wait a moment and retry")

    def request_detection(self, board: chess.Board):
        """Manual mode: compare current frame against reference, emit move."""
        with QMutexLocker(self._mutex):
            if self._ref_frame is None:
                self.error.emit("Save reference frame first")
                return
            self._detection_board = board
            self._detection_requested = True

    # --- Auto-mode controls ---

    def set_auto_enabled(self, enabled: bool, board: chess.Board = None):
        """
        Toggle auto-detect on/off. When enabling, pass the current board so
        the worker can use it for legal-move disambiguation. The current
        camera frame becomes the baseline.
        """
        with QMutexLocker(self._mutex):
            self._auto_enabled = enabled
            if enabled:
                if self._latest_frame is not None:
                    self._ref_frame = self._latest_frame.copy()
                if board is not None:
                    self._auto_board = board
                self._auto_state = STATE_STABLE
                self._still_since = None
                self.status.emit("Auto-detect ON — make your move")
            else:
                self._auto_state = STATE_STABLE
                self._still_since = None
                self.status.emit("Auto-detect OFF")
        self.auto_state_changed.emit(self._auto_state)

    def set_auto_baseline(self, board: chess.Board):
        """
        Reset the auto-detect baseline. GUI calls this AFTER the AI/robot
        move completes — the new physical state is now the 'stable' truth.
        """
        with QMutexLocker(self._mutex):
            if self._latest_frame is not None:
                self._ref_frame = self._latest_frame.copy()
            self._auto_board = board
            self._auto_state = STATE_STABLE
            self._still_since = None
            self.status.emit("Auto baseline reset — your turn")
        self.auto_state_changed.emit(self._auto_state)

    def stop(self):
        self._running = False

    # === Run loop ===

    def run(self):
        try:
            self._detector = MoveDetector(
                self._sqdict_path,
                move_threshold=MOVE_THRESHOLD,
                min_contour_area=MIN_CONTOUR_AREA,
            )
        except FileNotFoundError as e:
            self.error.emit(str(e))
            return
        except Exception as e:
            self.error.emit(f"Detector init failed: {e}")
            return

        self._cap = cv2.VideoCapture(self._camera_index)
        if not self._cap.isOpened():
            self.error.emit(
                f"Could not open camera at index {self._camera_index}. "
                f"Check config/settings.py CAMERA_INDEX."
            )
            return

        self.status.emit(f"Camera {self._camera_index} ready")
        self._running = True
        frame_interval = 1.0 / max(PREVIEW_FPS, 1)
        last_emit = 0.0

        while self._running:
            ret, frame = self._cap.read()
            if not ret:
                self.msleep(20)
                continue

            # Apply rotation immediately so all downstream code (preview,
            # detection, baseline storage) operates on the rotated frame.
            frame = _rotate_frame(frame)

            with QMutexLocker(self._mutex):
                self._latest_frame = frame
                detection_requested = self._detection_requested
                detection_board = self._detection_board
                if detection_requested:
                    self._detection_requested = False
                    self._detection_board = None
                auto_enabled = self._auto_enabled
                auto_board = self._auto_board

            # Auto-detect tick (state machine)
            if auto_enabled:
                self._auto_tick(frame, auto_board)

            # Throttled preview emission
            now = time.monotonic()
            if now - last_emit >= frame_interval:
                self._emit_preview(frame)
                last_emit = now

            # Manual detection request (still works even in auto mode)
            if detection_requested:
                self._handle_detection(frame, detection_board)

        if self._cap is not None:
            self._cap.release()
            self._cap = None

    # === Auto-detect state machine ===

    def _compute_motion_level(self, current_frame):
        """
        How many pixels changed significantly vs the previous frame,
        masked to the board area only.
        """
        gray = cv2.cvtColor(current_frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (5, 5), 0)

        if self._prev_frame_gray is None or self._prev_frame_gray.shape != gray.shape:
            self._prev_frame_gray = gray
            return 0

        diff = cv2.absdiff(self._prev_frame_gray, gray)
        if self._detector is not None:
            mask = self._detector._ensure_mask(current_frame.shape)
            diff = cv2.bitwise_and(diff, mask)

        self._prev_frame_gray = gray
        return int(np.sum(diff > MOTION_PIXEL_DELTA))

    def _auto_tick(self, frame, board):
        """One iteration of the auto-detect state machine."""
        motion = self._compute_motion_level(frame)
        now = time.monotonic()
        prev_state = self._auto_state

        if self._auto_state == STATE_STABLE:
            if motion > AUTO_MOTION_THRESHOLD:
                self._auto_state = STATE_MOTION

        elif self._auto_state == STATE_MOTION:
            if motion < AUTO_STILLNESS_THRESHOLD:
                self._auto_state = STATE_PENDING
                self._still_since = now

        elif self._auto_state == STATE_PENDING:
            if motion > AUTO_MOTION_THRESHOLD:
                # Motion came back — keep waiting
                self._auto_state = STATE_MOTION
                self._still_since = None
            elif motion < AUTO_STILLNESS_THRESHOLD:
                if (now - self._still_since) >= AUTO_STABLE_DURATION:
                    self._auto_state = STATE_COOLDOWN
                    self._auto_trigger_detection(frame, board)
            # else: middle zone — neither still nor moving — keep waiting

        elif self._auto_state == STATE_COOLDOWN:
            # Stay until GUI resets baseline (after AI move completes)
            pass

        if prev_state != self._auto_state:
            self.auto_state_changed.emit(self._auto_state)

    def _auto_trigger_detection(self, after_frame, board):
        """Stillness confirmed; run the diff and emit move."""
        ref = None
        with QMutexLocker(self._mutex):
            if self._ref_frame is not None:
                ref = self._ref_frame.copy()

        if ref is None or board is None:
            self.status.emit("Auto-detect: missing baseline or board state")
            return

        try:
            from_sq, to_sq = self._detector.detect(ref, after_frame, board)
        except Exception as e:
            self.error.emit(f"Auto-detect failed: {e}")
            return

        if from_sq is None or to_sq is None:
            self.status.emit("Auto-detect: no clear move; watching again")
            with QMutexLocker(self._mutex):
                self._auto_state = STATE_STABLE
                self._still_since = None
            self.auto_state_changed.emit(STATE_STABLE)
            return

        # Sanity check: legal chess moves change at most 4 squares (castling).
        # Anything more = lighting flicker / camera bump / hand still in frame.
        n_changed = self._count_changed_squares(ref, after_frame)
        if n_changed > AUTO_MAX_CHANGED_SQUARES:
            self.status.emit(
                f"Auto-detect: {n_changed} squares changed; ignoring (likely noise)."
            )
            with QMutexLocker(self._mutex):
                self._auto_state = STATE_STABLE
                self._still_since = None
            self.auto_state_changed.emit(STATE_STABLE)
            return

        self.status.emit(f"Auto-detected: {from_sq} -> {to_sq}")
        self.move_detected.emit(from_sq, to_sq)
        # Stay in COOLDOWN until GUI calls set_auto_baseline()

    def _count_changed_squares(self, before, after):
        """How many squares show meaningful pixel change between the two frames?"""
        if self._detector is None:
            return 0
        diff = cv2.absdiff(
            cv2.cvtColor(before, cv2.COLOR_BGR2GRAY),
            cv2.cvtColor(after, cv2.COLOR_BGR2GRAY),
        )
        _, mask = cv2.threshold(diff, MOVE_THRESHOLD, 255, cv2.THRESH_BINARY)

        n_changed = 0
        for sq, pts in self._detector.sq_points.items():
            poly = np.array(pts, np.int32)
            sq_mask = np.zeros_like(mask)
            cv2.fillPoly(sq_mask, [poly], 255)
            overlap = cv2.bitwise_and(mask, sq_mask)
            if int(np.sum(overlap > 0)) > MIN_CONTOUR_AREA:
                n_changed += 1
        return n_changed

    # === Preview rendering ===

    def _emit_preview(self, frame):
        display = self._draw_overlay(frame)
        rgb = cv2.cvtColor(display, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        qimg = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888).copy()
        self.frame_ready.emit(qimg)

    def _draw_overlay(self, frame):
        """Draw board grid, REF indicator, and auto-state indicator."""
        if self._detector is None:
            return frame

        out = frame.copy()
        for sq, pts in self._detector.sq_points.items():
            poly = np.array(pts, np.int32)
            cv2.polylines(out, [poly], True, (255, 255, 255), 1)

        # Reference frame indicator (top-left)
        if self._ref_frame is not None:
            cv2.circle(out, (20, 20), 8, (0, 255, 0), -1)
            cv2.putText(out, "REF SAVED", (35, 26),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1, cv2.LINE_AA)
        else:
            cv2.circle(out, (20, 20), 8, (0, 0, 255), -1)
            cv2.putText(out, "NO REF", (35, 26),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1, cv2.LINE_AA)

        # Auto-detect state indicator (bottom-left)
        if self._auto_enabled:
            state_colors = {
                STATE_STABLE: (0, 255, 0),       # green: ready
                STATE_MOTION: (0, 255, 255),     # yellow: motion detected
                STATE_PENDING: (0, 165, 255),    # orange: waiting for still
                STATE_COOLDOWN: (200, 0, 200),   # purple: waiting for baseline
            }
            color = state_colors.get(self._auto_state, (200, 200, 200))
            h_img = out.shape[0]
            cv2.circle(out, (20, h_img - 20), 8, color, -1)
            cv2.putText(out, f"AUTO: {self._auto_state}", (35, h_img - 14),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)

        return out

    # === Manual detection path ===

    def _handle_detection(self, after_frame, board):
        ref = None
        with QMutexLocker(self._mutex):
            if self._ref_frame is not None:
                ref = self._ref_frame.copy()

        if ref is None:
            self.error.emit("Reference lost — save it again")
            return

        try:
            from_sq, to_sq = self._detector.detect(ref, after_frame, board)
        except Exception as e:
            self.error.emit(f"Detection failed: {e}")
            return

        if from_sq is None or to_sq is None:
            self.status.emit("No move detected — try again")
            return

        self.status.emit(f"Detected: {from_sq} -> {to_sq}")
        self.move_detected.emit(from_sq, to_sq)

        with QMutexLocker(self._mutex):
            self._ref_frame = None