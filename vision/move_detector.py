"""
Pure chess move detector — no Qt, no Stockfish, no GUI.

Given a calibration JSON and a pair of frames (before + after), figures out
which two squares changed and returns (from_sq, to_sq).

Disambiguation uses the legal moves of a chess.Board passed in, so the
detector knows which side moved and what's possible. The board itself is
NOT mutated here — that's the engine module's job.
"""
import json
import os

import cv2
import numpy as np
import chess


FILES = "abcdefgh"
RANKS = "12345678"


class MoveDetector:
    """
    Stateless-ish detector. Holds calibration data and detection params,
    but no game state. Call detect() with the two frames and current board.
    """

    def __init__(self, sqdict_path, move_threshold=25, min_contour_area=250):
        if not os.path.exists(sqdict_path):
            raise FileNotFoundError(
                f"Calibration file not found: {sqdict_path}. "
                f"Run calibration first (python -m vision.calibrate)."
            )

        with open(sqdict_path, "r") as f:
            self.sq_points = json.load(f)

        self.move_threshold = move_threshold
        self.min_contour_area = min_contour_area

        # Pre-build a board mask once (assumes calibration doesn't change at runtime)
        self._mask_board = None

    def _ensure_mask(self, frame_shape):
        """Build the board mask lazily once we know the frame size."""
        if self._mask_board is None or self._mask_board.shape != frame_shape[:2]:
            mask = np.zeros(frame_shape[:2], dtype=np.uint8)
            for pts in self.sq_points.values():
                cv2.fillPoly(mask, [np.array(pts, np.int32)], 255)
            self._mask_board = mask
        return self._mask_board

    def find_square(self, x, y):
        """Return square name (e.g. 'e4') if (x, y) lies inside that square."""
        pt = (float(x), float(y))
        for sq, pts in self.sq_points.items():
            poly = np.array(pts, np.int32)
            if cv2.pointPolygonTest(poly, pt, False) >= 0:
                return sq
        return None

    def _diff_frames(self, before, after):
        """Weighted-grayscale diff that's robust to light/dark boards."""
        # Heavier weight on the red channel: pieces tend to differ from the
        # board most strongly there under tungsten/white light.
        g1 = (0.5 * before[:, :, 2] + 0.4 * before[:, :, 1] + 0.1 * before[:, :, 0]).astype(np.uint8)
        g2 = (0.5 * after[:, :, 2] + 0.4 * after[:, :, 1] + 0.1 * after[:, :, 0]).astype(np.uint8)

        g1 = cv2.GaussianBlur(g1, (5, 5), 0)
        g2 = cv2.GaussianBlur(g2, (5, 5), 0)

        diff = cv2.absdiff(g1, g2)
        diff = cv2.GaussianBlur(diff, (3, 3), 0)
        diff = cv2.convertScaleAbs(diff, alpha=1.3, beta=0)

        _, thresh = cv2.threshold(diff, self.move_threshold, 255, cv2.THRESH_BINARY)
        thresh = cv2.dilate(thresh, None, iterations=4)
        thresh = cv2.erode(thresh, None, iterations=2)

        # Mask to board area only
        mask = self._ensure_mask(after.shape)
        thresh = cv2.bitwise_and(thresh, mask)

        kernel = np.ones((3, 3), np.uint8)
        thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)
        thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)

        return thresh

    def _candidates_for_contour(self, c):
        """
        For a single contour, generate plausible square candidates.

        A piece's contour center isn't always inside the square it stands on
        because of camera angle (tall pieces lean toward camera). We sample
        multiple Y biases and X jitters to find the best matching square.
        """
        x, y, w, h = cv2.boundingRect(c)
        M = cv2.moments(c)
        cx_m = int(M["m10"] / M["m00"]) if M["m00"] != 0 else x + w // 2

        y_factors = [0.20, 0.30, 0.40]
        x_jitters = [0, -6, 6]

        seen = set()
        out = []
        for yf in y_factors:
            cy_try = int(y + yf * h)
            for xj in x_jitters:
                cx_try = cx_m + xj
                sq = self.find_square(cx_try, cy_try)
                if sq and sq not in seen:
                    seen.add(sq)
                    out.append((sq, cx_try, cy_try))
        return out

    def detect(self, before_frame, after_frame, board: chess.Board):
        """
        Detect the move that happened between before_frame and after_frame.

        Returns:
            (from_sq, to_sq) on success, or
            (None, None) if no move could be inferred.

        The `board` arg is the chess state BEFORE the move — used for
        legality-based disambiguation. Not mutated.
        """
        diff_m = self._diff_frames(before_frame, after_frame)
        contours, _ = cv2.findContours(diff_m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        contours = [c for c in contours if cv2.contourArea(c) > self.min_contour_area]

        if not contours:
            return None, None

        contour_cand_lists = [(c, self._candidates_for_contour(c)) for c in contours]
        # Keep the two largest contours — the moved piece origin and destination
        contour_cand_lists.sort(key=lambda it: cv2.contourArea(it[0]), reverse=True)
        contour_cand_lists = contour_cand_lists[:2]

        # === Two-contour case: typical move ===
        if len(contour_cand_lists) >= 2:
            (_, list0), (_, list1) = contour_cand_lists

            # Try every (s0, s1) pair in both directions; pick the legal one
            for s0, _, _ in list0:
                for s1, _, _ in list1:
                    for fr, to in [(s0, s1), (s1, s0)]:
                        try:
                            mv = chess.Move.from_uci(fr + to)
                        except ValueError:
                            continue
                        if mv in board.legal_moves:
                            return fr, to
                        # Try queen promotion as fallback
                        try:
                            mv_q = chess.Move.from_uci(fr + to + "q")
                            if mv_q in board.legal_moves:
                                return fr, to
                        except ValueError:
                            pass

            # No legal pair found — fall back to "piece-was-there" heuristic
            if list0 and list1:
                a, b = list0[0][0], list1[0][0]
                return self._disambiguate_by_occupancy(a, b, board)

        # === One-contour case: capture (piece replaces piece, only one delta) ===
        if len(contour_cand_lists) == 1:
            _, list0 = contour_cand_lists[0]
            if not list0:
                return None, None

            for to_sq, _, _ in list0:
                # Find any legal move ending on this square
                candidates = [m for m in board.legal_moves if m.uci()[2:4] == to_sq]
                if candidates:
                    chosen = candidates[0]
                    return chosen.uci()[:2], chosen.uci()[2:4]

            return None, None

        return None, None

    @staticmethod
    def _disambiguate_by_occupancy(sq_a, sq_b, board):
        """If we have two squares but no legal pair, infer by which had a piece."""
        piece_a = board.piece_at(chess.parse_square(sq_a))
        piece_b = board.piece_at(chess.parse_square(sq_b))

        if piece_a and not piece_b:
            return sq_a, sq_b
        if piece_b and not piece_a:
            return sq_b, sq_a

        # Both or neither — best guess by side-to-move direction
        if board.turn == chess.WHITE:
            return tuple(sorted([sq_a, sq_b], key=lambda s: int(s[1])))
        return tuple(sorted([sq_a, sq_b], key=lambda s: int(s[1]), reverse=True))
