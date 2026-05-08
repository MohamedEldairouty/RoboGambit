"""
Qt-side vision worker.

Runs the camera in a background thread and emits frames + detected moves
as Qt signals. The GUI connects to these signals to update its display.

Usage from GUI:
    self.vision = VisionWorker(camera_index=1)
    self.vision.frame_ready.connect(self.on_frame)
    self.vision.move_detected.connect(self.on_move_detected)
    self.vision.error.connect(self.on_vision_error)
    self.vision.start()

    # When user clicks "Save Reference":
    self.vision.set_reference()

    # When user clicks "Detect Move":
    self.vision.request_detection(self.engine.board.copy())
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
)
from vision.move_detector import MoveDetector


class VisionWorker(QThread):
    # Live preview: emitted on every captured frame (~ PREVIEW_FPS Hz)
    frame_ready = Signal(QImage)

    # Move detected: emitted after detect_move() succeeds. (from_sq, to_sq) in UCI.
    move_detected = Signal(str, str)

    # Status updates for the GUI status bar
    status = Signal(str)

    # Errors that the GUI should show prominently
    error = Signal(str)

    # Highlight overlay: emitted when we want the GUI to flash squares
    # (square_name, color_hint) where color_hint is "from", "to", or "info"
    square_highlight = Signal(str, str)

    def __init__(self, camera_index=None, sqdict_path=None, parent=None):
        super().__init__(parent)
        self._camera_index = camera_index if camera_index is not None else CAMERA_INDEX
        self._sqdict_path = sqdict_path or SQDICT_PATH

        self._cap = None
        self._detector = None
        self._ref_frame = None
        self._latest_frame = None
        self._running = False

        # Detection requests are async: GUI sets these via request_detection()
        # and the camera loop processes them on its next iteration.
        self._mutex = QMutex()
        self._detection_requested = False
        self._detection_board = None

    # === Public slots (call from GUI thread) ===

    @Slot()
    def set_reference(self):
        """Capture the 'before' frame for the next detection."""
        with QMutexLocker(self._mutex):
            if self._latest_frame is not None:
                self._ref_frame = self._latest_frame.copy()
                self.status.emit("Reference frame saved")
            else:
                self.error.emit("No camera frame yet — wait a moment and retry")

    def request_detection(self, board: chess.Board):
        """
        Ask the worker to compare the current frame against the reference
        and emit a move_detected signal if something is found.

        Pass a *copy* of the current chess.Board (the worker will use it
        for legal-move disambiguation but won't mutate it).
        """
        with QMutexLocker(self._mutex):
            if self._ref_frame is None:
                self.error.emit("Save reference frame first")
                return
            self._detection_board = board
            self._detection_requested = True

    def stop(self):
        """Signal the run loop to exit; call this before quit()."""
        self._running = False

    # === The thread's run loop ===

    def run(self):
        # Initialize detector
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

        # Open camera
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

            with QMutexLocker(self._mutex):
                self._latest_frame = frame
                detection_requested = self._detection_requested
                detection_board = self._detection_board
                if detection_requested:
                    self._detection_requested = False
                    self._detection_board = None

            # Throttle preview emission
            now = time.monotonic()
            if now - last_emit >= frame_interval:
                self._emit_preview(frame)
                last_emit = now

            # Handle detection request
            if detection_requested:
                self._handle_detection(frame, detection_board)

        # Cleanup
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    # === Internal helpers ===

    def _emit_preview(self, frame):
        """Convert BGR frame to QImage and emit, with board grid overlay."""
        display = self._draw_overlay(frame)
        rgb = cv2.cvtColor(display, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        bytes_per_line = ch * w
        qimg = QImage(rgb.data, w, h, bytes_per_line, QImage.Format_RGB888).copy()
        self.frame_ready.emit(qimg)

    def _draw_overlay(self, frame):
        """Draw the board grid + reference-saved indicator on the live feed."""
        if self._detector is None:
            return frame

        out = frame.copy()
        for sq, pts in self._detector.sq_points.items():
            poly = np.array(pts, np.int32)
            cv2.polylines(out, [poly], True, (255, 255, 255), 1)

        # Indicator dot when reference frame is saved
        if self._ref_frame is not None:
            cv2.circle(out, (20, 20), 8, (0, 255, 0), -1)
            cv2.putText(
                out, "REF SAVED", (35, 26),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1, cv2.LINE_AA,
            )
        else:
            cv2.circle(out, (20, 20), 8, (0, 0, 255), -1)
            cv2.putText(
                out, "NO REF", (35, 26),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1, cv2.LINE_AA,
            )
        return out

    def _handle_detection(self, after_frame, board):
        """Run move detection and emit the result."""
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

        # Reset reference; GUI will save a new one after the AI/robot move completes
        with QMutexLocker(self._mutex):
            self._ref_frame = None
