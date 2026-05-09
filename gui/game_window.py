"""
Main game window.

Four modes:
  - ai_robot   : player vs AI, with camera detection + robot arm output
  - ai_pure    : player vs AI, click-only (no camera, no robot)
  - pvp        : player vs player, both human, click-only
  - ai_vs_ai   : demo, both AI, no human input

Threading model:
  - GUI thread:  owns ChessEngine, board UI, all click handlers.
  - Vision QThread (only in ai_robot mode): owns camera, runs MoveDetector.
    Communicates with GUI ONLY via signals.

Robot backend (only in ai_robot mode): publishes to ROS topic
(or prints to console if backend is "fake" — see config/settings.py).
"""
import os
import sys

import chess
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QGridLayout, QHBoxLayout, QLabel,
    QMainWindow, QPushButton, QTextEdit, QVBoxLayout, QWidget,
)

# Make sibling packages importable when running from various working dirs
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.chess_engine import ChessEngine
from robot import get_robot_backend
from vision import VisionWorker


PIECE_SYMBOLS = {
    "P": "♙", "N": "♘", "B": "♗", "R": "♖", "Q": "♕", "K": "♔",
    "p": "♟", "n": "♞", "b": "♝", "r": "♜", "q": "♛", "k": "♚",
}


class GameOverDialog(QDialog):
    def __init__(self, title, winner, result, reason, parent=None):
        super().__init__(parent)
        self.setWindowTitle("RoboGambit Result")
        self.setMinimumSize(480, 320)

        layout = QVBoxLayout()

        title_label = QLabel(title)
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet("font-size: 34px; font-weight: bold; color: #ff8c00;")

        winner_label = QLabel(winner)
        winner_label.setAlignment(Qt.AlignCenter)
        winner_label.setStyleSheet("font-size: 30px; font-weight: bold; color: #00ff99;")

        result_label = QLabel(f"Final Result: {result}")
        result_label.setAlignment(Qt.AlignCenter)
        result_label.setStyleSheet("font-size: 18px; color: white;")

        reason_label = QLabel(reason)
        reason_label.setAlignment(Qt.AlignCenter)
        reason_label.setWordWrap(True)
        reason_label.setStyleSheet("font-size: 16px; color: #d0d0d0;")

        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.accepted.connect(self.accept)

        layout.addStretch()
        layout.addWidget(title_label)
        layout.addSpacing(15)
        layout.addWidget(winner_label)
        layout.addSpacing(10)
        layout.addWidget(result_label)
        layout.addWidget(reason_label)
        layout.addStretch()
        layout.addWidget(buttons)

        self.setLayout(layout)
        self.setStyleSheet("""
            QDialog { background-color: #0b0f14; border: 2px solid #ff8c00; }
            QPushButton {
                background-color: #ff8c00; color: black;
                border-radius: 10px; padding: 10px 22px; font-weight: bold;
            }
            QPushButton:hover { background-color: #ffaa33; }
        """)


class GameWindow(QMainWindow):
    def __init__(self, mode, settings, back_callback):
        super().__init__()

        self.mode = mode
        self.settings = settings
        self.back_callback = back_callback

        # === Per-mode flags so we don't sprinkle string-checks everywhere ===
        self.uses_camera = (mode == "ai_robot")
        self.uses_robot = (mode == "ai_robot")
        self.allows_clicks = mode in ("ai_pure", "pvp")
        self.is_demo = (mode == "ai_vs_ai")

        # === Hint state (per-mode behaviour) ===
        # ai_pure: single hint button, default OFF
        # pvp: two hint buttons (one per player), both default OFF
        # ai_robot / ai_vs_ai: no hints
        self.hint_white = False  # PvP: hint for white?  ai_pure: hint for human?
        self.hint_black = False  # PvP: hint for black?

        # === Domain objects ===
        self.engine = ChessEngine()
        self.engine.set_difficulty(settings["difficulty"])
        self.ai_time = self._ai_time_for(settings["difficulty"])

        # Robot only in ai_robot mode
        self.robot = get_robot_backend() if self.uses_robot else None

        # Vision only in ai_robot mode
        self.vision = None

        # === UI state ===
        self.selected_square = None
        self.square_buttons = {}
        self.legal_target_squares = []
        self.last_move_squares = []
        self.last_move_was_capture = False
        self.suggested_move_squares = []
        self.suggested_move_is_capture = False
        self.captured_white = []
        self.captured_black = []

        # === Scoreboard ===
        self.white_wins = 0
        self.black_wins = 0
        self.draws = 0
        self.total_games = 0
        self.game_counted = False

        # === AI move pipeline state (ai_robot mode only) ===
        self.pending_ai_move = None
        self.pending_ai_move_data = None

        self.ai_vs_ai_timer = QTimer()
        self.ai_vs_ai_timer.timeout.connect(self.make_ai_vs_ai_move)

        # === Build window ===
        self.setWindowTitle("RoboGambit - Game")
        self.setMinimumSize(1400, 800)

        main_layout = QHBoxLayout()
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(14)

        left_panel = self._build_left_panel()
        board = self._build_board()
        right_panel = self._build_right_panel()

        main_layout.addWidget(left_panel, 1)
        main_layout.addWidget(board, 3)
        main_layout.addWidget(right_panel, 2)

        container = QWidget()
        container.setLayout(main_layout)
        self.setCentralWidget(container)

        self._apply_main_style()
        self._update_board_display()
        self._update_scoreboard()

        # === Mode-specific initialization ===
        self._init_for_mode()

    def _init_for_mode(self):
        if self.mode == "ai_vs_ai":
            self.mode_label.setText("Mode: AI vs AI Demo")
            self.current_move_label.setText("AI Demo Running...")
            self.ai_vs_ai_timer.start(1300)

        elif self.mode == "pvp":
            self.mode_label.setText("Mode: Player vs Player")
            self.robot_status_label.setText("Robot Status: Not Used")
            # Both hints default OFF; no auto-suggest at game start

        elif self.mode == "ai_pure":
            self.mode_label.setText("Mode: Player vs AI")
            self.robot_status_label.setText("Robot Status: Not Used")
            self.current_move_label.setText("Click a piece to move")
            # Hint defaults OFF

        else:  # ai_robot
            self.mode_label.setText("Mode: Player vs AI (Robot)")
            self.current_move_label.setText("Click 'Save Reference' before your move")
            self.robot_status_label.setText("Robot Status: Idle")
            self.ai_move_label.setText("AI Move: -")
            self._start_vision_thread()

    # === Helpers ===

    def _ai_time_for(self, difficulty):
        return {"Easy": 0.2, "Medium": 0.7, "Hard": 1.2}.get(difficulty, 0.7)

    def _apply_main_style(self):
        self.setStyleSheet("""
            QMainWindow { background-color: #0b0f14; }
            QLabel { color: #f5f5f5; font-size: 15px; }
            QPushButton {
                background-color: #ff8c00; color: #111111;
                border-radius: 12px; padding: 10px;
                font-weight: bold; font-size: 14px;
            }
            QPushButton:hover {
                background-color: #ffaa33; border: 2px solid #ffffff;
            }
            QPushButton:disabled {
                background-color: #555555; color: #aaaaaa;
            }
            QTextEdit {
                background-color: #101820; color: #00ff99;
                border: 1px solid #2f3b45; border-radius: 12px;
                padding: 10px; font-family: Consolas; font-size: 13px;
            }
        """)

    def _make_card_label(self, text):
        label = QLabel(text)
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet("""
            background-color: #101820; border: 1px solid #2f3b45;
            border-radius: 12px; padding: 10px;
            font-size: 15px; color: #ffffff;
        """)
        return label

    # === Layout builders ===

    def _build_left_panel(self):
        panel = QWidget()
        layout = QVBoxLayout()

        title = QLabel("♟ RoboGambit")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 28px; font-weight: bold; color: #ff8c00;")

        self.mode_label = self._make_card_label("Mode: -")
        self.difficulty_info = self._make_card_label(f"Difficulty: {self.settings['difficulty']}")

        self.players_label = QLabel(
            f"Player 1: {self.settings['white_name']}\n"
            f"Player 2: {self.settings['black_name']}"
        )
        self.players_label.setAlignment(Qt.AlignCenter)
        self.players_label.setStyleSheet("""
            background-color: #101820; border: 1px solid #ff8c00;
            border-radius: 14px; padding: 12px;
            font-size: 15px; color: white;
        """)

        score_title = QLabel("Scoreboard")
        score_title.setAlignment(Qt.AlignCenter)
        score_title.setStyleSheet("font-size: 18px; font-weight: bold; color: #00ff99;")

        self.scoreboard_label = QLabel()
        self.scoreboard_label.setAlignment(Qt.AlignCenter)
        self.scoreboard_label.setStyleSheet("""
            background-color: #101820; border: 1px solid #00ff99;
            border-radius: 14px; padding: 12px;
            font-size: 15px; color: white;
        """)

        new_match_btn = QPushButton("New Match")
        new_match_btn.clicked.connect(self.reset_game)

        reset_score_btn = QPushButton("Reset Scoreboard")
        reset_score_btn.clicked.connect(self.reset_scoreboard)

        # === Hint buttons (per-mode) ===
        # ai_pure: single hint for the human (white)
        self.hint_btn_single = QPushButton("AI Hint: OFF")
        self.hint_btn_single.clicked.connect(self._toggle_hint_single)

        # pvp: two hint buttons, one per side
        self.hint_btn_white = QPushButton(
            f"{self.settings['white_name']} Hint: OFF"
        )
        self.hint_btn_white.clicked.connect(self._toggle_hint_white)

        self.hint_btn_black = QPushButton(
            f"{self.settings['black_name']} Hint: OFF"
        )
        self.hint_btn_black.clicked.connect(self._toggle_hint_black)

        # === Vision/robot controls (ai_robot mode only) ===
        self.camera_ref_btn = QPushButton("1. Save Reference")
        self.camera_ref_btn.clicked.connect(self.on_save_reference_clicked)

        self.detect_move_btn = QPushButton("2. Detect Human Move")
        self.detect_move_btn.clicked.connect(self.on_detect_move_clicked)

        self.robot_done_btn = QPushButton("3. Robot Move Done")
        self.robot_done_btn.clicked.connect(self.on_robot_done_clicked)
        self.robot_done_btn.setEnabled(False)

        self.auto_detect_btn = QPushButton("Auto Detect: OFF")
        self.auto_detect_btn.setCheckable(True)
        self.auto_detect_btn.clicked.connect(self.on_auto_detect_toggled)

        fullscreen_btn = QPushButton("Toggle Fullscreen")
        fullscreen_btn.clicked.connect(self.toggle_fullscreen)

        back_btn = QPushButton("Back to Menu")
        back_btn.clicked.connect(self.back_to_menu)

        # === Compose layout ===
        layout.addWidget(title)
        layout.addSpacing(10)
        layout.addWidget(self.mode_label)
        layout.addWidget(self.difficulty_info)
        layout.addWidget(self.players_label)
        layout.addSpacing(8)
        layout.addWidget(score_title)
        layout.addWidget(self.scoreboard_label)
        layout.addSpacing(12)
        layout.addWidget(new_match_btn)
        layout.addWidget(reset_score_btn)

        # Mode-specific buttons
        if self.mode == "pvp":
            layout.addSpacing(6)
            layout.addWidget(self.hint_btn_white)
            layout.addWidget(self.hint_btn_black)

        elif self.mode == "ai_pure":
            layout.addSpacing(6)
            layout.addWidget(self.hint_btn_single)

        elif self.mode == "ai_robot":
            layout.addSpacing(8)
            vision_title = QLabel("— Camera & Robot —")
            vision_title.setAlignment(Qt.AlignCenter)
            vision_title.setStyleSheet("color: #00ff99; font-weight: bold;")
            layout.addWidget(vision_title)
            layout.addWidget(self.auto_detect_btn)
            layout.addWidget(self.camera_ref_btn)
            layout.addWidget(self.detect_move_btn)
            layout.addWidget(self.robot_done_btn)

        layout.addWidget(fullscreen_btn)
        layout.addWidget(back_btn)
        layout.addStretch()

        panel.setLayout(layout)
        return panel

    def _build_board(self):
        outer = QWidget()
        outer_layout = QVBoxLayout()
        outer_layout.setAlignment(Qt.AlignCenter)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        board_widget = QWidget()
        board_widget.setFixedSize(640, 640)

        grid = QGridLayout()
        grid.setSpacing(0)
        grid.setContentsMargins(0, 0, 0, 0)

        files = "abcdefgh"
        for row in range(8):
            for col in range(8):
                square_name = files[col] + str(8 - row)
                square = QPushButton("")
                square.setFixedSize(80, 80)
                square.clicked.connect(
                    lambda checked=False, name=square_name: self.handle_square_click(name)
                )
                self.square_buttons[square_name] = square
                grid.addWidget(square, row, col)

        board_widget.setLayout(grid)
        outer_layout.addWidget(board_widget, alignment=Qt.AlignCenter)
        outer.setLayout(outer_layout)
        return outer

    def _build_right_panel(self):
        panel = QWidget()
        layout = QVBoxLayout()

        title = QLabel("Move + Robot Panel" if self.uses_robot else "Move Panel")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #ff8c00;")

        self.current_move_label = self._make_card_label("Player Move: -")
        self.ai_move_label = self._make_card_label("AI Move: -")
        self.robot_status_label = self._make_card_label("Robot Status: Idle")

        self.robot_preview_label = QLabel("Robot Preview:\nPick: -\nDrop: -")
        self.robot_preview_label.setStyleSheet("""
            background-color: #101820; border: 1px solid #00ff99;
            border-radius: 12px; padding: 10px;
            color: #00ff99; font-size: 14px; font-family: Consolas;
        """)

        # Camera preview only for ai_robot
        self.camera_view = QLabel("Camera feed will appear here\n(Robot mode only)")
        self.camera_view.setAlignment(Qt.AlignCenter)
        self.camera_view.setMinimumSize(360, 270)
        self.camera_view.setStyleSheet("""
            background-color: #000000; border: 1px solid #2f3b45;
            border-radius: 12px; color: #888888; font-size: 13px;
        """)
        self.camera_view.setScaledContents(False)

        self.captured_label = QLabel("Captured White:\n-\n\nCaptured Black:\n-")
        self.captured_label.setStyleSheet("""
            background-color: #101820; border: 1px solid #2f3b45;
            border-radius: 12px; padding: 10px;
            color: white; font-size: 16px;
        """)

        history_title = QLabel("Move History")
        history_title.setStyleSheet("font-size: 16px; font-weight: bold; color: #ff8c00;")

        self.move_history = QTextEdit()
        self.move_history.setReadOnly(True)
        self.move_history.setMaximumHeight(140)

        layout.addWidget(title)
        layout.addSpacing(6)
        layout.addWidget(self.current_move_label)
        layout.addWidget(self.ai_move_label)

        # Show robot status & preview ONLY in ai_robot mode (the real robot path).
        # ai_vs_ai, pvp, and ai_pure don't use a robot — hide the widgets entirely.
        if self.uses_robot:
            layout.addWidget(self.robot_status_label)
            layout.addWidget(self.robot_preview_label)
        else:
            self.robot_status_label.hide()
            self.robot_preview_label.hide()

        if self.uses_camera:
            layout.addWidget(self.camera_view, 1)
        else:
            self.camera_view.hide()

        layout.addWidget(self.captured_label)
        layout.addWidget(history_title)
        layout.addWidget(self.move_history)

        panel.setLayout(layout)
        return panel

    # === Vision thread lifecycle (ai_robot only) ===

    def _start_vision_thread(self):
        self.vision = VisionWorker(parent=self)
        self.vision.frame_ready.connect(self._on_vision_frame)
        self.vision.move_detected.connect(self._on_vision_move_detected)
        self.vision.status.connect(self._on_vision_status)
        self.vision.error.connect(self._on_vision_error)
        self.vision.auto_state_changed.connect(self._on_auto_state_changed)
        self.vision.start()

    def _stop_vision_thread(self):
        if self.vision is not None:
            self.vision.stop()
            self.vision.quit()
            self.vision.wait(2000)
            self.vision = None

    # === Vision signal handlers ===

    def _on_vision_frame(self, qimg: QImage):
        pix = QPixmap.fromImage(qimg)
        scaled = pix.scaled(
            self.camera_view.size(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self.camera_view.setPixmap(scaled)

    def _on_vision_status(self, msg: str):
        self.robot_status_label.setText(f"Vision: {msg}")

    def _on_vision_error(self, msg: str):
        self.robot_status_label.setText(f"Vision Error: {msg[:60]}")
        self.move_history.append(f"[VISION ERROR] {msg}")

    def _on_vision_move_detected(self, from_sq: str, to_sq: str):
        if self.pending_ai_move is not None:
            self.current_move_label.setText("Wait — AI move is still pending")
            return

        move_uci = from_sq + to_sq
        ok, move_data = self.engine.make_player_move(move_uci)

        if not ok:
            self.current_move_label.setText(f"Detected illegal move: {move_uci}")
            self.move_history.append(f"[REJECTED] {move_uci} — {move_data}")
            return

        self._apply_move_visuals(move_data, self.settings["white_name"])
        self._update_board_display()

        if self.engine.board.is_game_over():
            self.show_game_result()
            return

        self._prepare_ai_robot_move()

    # === Vision/robot button handlers (ai_robot only) ===

    def on_save_reference_clicked(self):
        if self.vision is None:
            self.current_move_label.setText("Vision not running")
            return
        self.vision.set_reference()

    def on_detect_move_clicked(self):
        if self.vision is None:
            self.current_move_label.setText("Vision not running")
            return
        if self.pending_ai_move is not None:
            self.current_move_label.setText("Wait: robot move pending")
            return
        self.vision.request_detection(self.engine.board.copy())

    def on_robot_done_clicked(self):
        if self.pending_ai_move is None:
            return

        self.engine.board.push(self.pending_ai_move)
        self.engine.move_history.append(str(self.pending_ai_move))

        self._apply_move_visuals(self.pending_ai_move_data, "AI")

        self.pending_ai_move = None
        self.pending_ai_move_data = None

        self.robot_done_btn.setEnabled(False)
        self.robot_status_label.setText("Robot Status: Move Completed")
        self._update_board_display()

        if self.engine.board.is_game_over():
            self.show_game_result()
            return

        if self.vision is not None:
            if self.auto_detect_btn.isChecked():
                self.vision.set_auto_baseline(self.engine.board.copy())
                self.current_move_label.setText("Auto-detect armed. Make your move.")
            else:
                self.vision.set_reference()
                self.current_move_label.setText("Reference auto-saved. Make your move.")

    def on_auto_detect_toggled(self, checked):
        if self.vision is None:
            self.auto_detect_btn.setChecked(False)
            return

        if checked:
            self.camera_ref_btn.setEnabled(False)
            self.detect_move_btn.setEnabled(False)
            self.auto_detect_btn.setText("Auto Detect: ON")
            self.vision.set_auto_enabled(True, self.engine.board.copy())
            self.current_move_label.setText("Auto-detect armed. Make your move.")
        else:
            self.camera_ref_btn.setEnabled(True)
            self.detect_move_btn.setEnabled(True)
            self.auto_detect_btn.setText("Auto Detect: OFF")
            self.vision.set_auto_enabled(False)
            self.current_move_label.setText("Manual mode: use Save Reference + Detect Move")

    def _on_auto_state_changed(self, state):
        labels = {
            "stable":   "Auto: Watching board (stable)",
            "motion":   "Auto: Motion detected — keep moving",
            "pending":  "Auto: Confirming stillness…",
            "cooldown": "Auto: Move detected; waiting for AI",
        }
        self.robot_status_label.setText(labels.get(state, f"Auto: {state}"))

    # === AI move pipeline (ai_robot mode) ===

    def _prepare_ai_robot_move(self):
        move = self.engine.get_best_move(time_limit=self.ai_time)
        captured_piece = self.engine.board.piece_at(move.to_square)

        self.pending_ai_move = move
        self.pending_ai_move_data = {
            "uci": str(move),
            "from": chess.square_name(move.from_square),
            "to": chess.square_name(move.to_square),
            "captured_piece": captured_piece,
            "fen": self.engine.board.fen(),
        }

        self.ai_move_label.setText(f"AI Move: {self.pending_ai_move_data['uci']}")
        self.robot_status_label.setText("Robot Status: Move Ready")
        self.robot_preview_label.setText(
            f"Robot Preview:\n"
            f"Pick: {self.pending_ai_move_data['from']}\n"
            f"Drop: {self.pending_ai_move_data['to']}"
        )

        self.move_history.append(f"AI Ready: {self.pending_ai_move_data['uci']}")

        if self.robot is not None:
            try:
                self.robot.send_move(self.pending_ai_move_data)
            except Exception as e:
                self.move_history.append(f"[ROBOT ERROR] {e}")

        self.robot_done_btn.setEnabled(True)

    # === Click-based moves (ai_pure / pvp) ===

    def handle_square_click(self, square_name):
        if self.mode == "ai_robot":
            # Real-board mode: clicks are inspection-only
            self._inspect_only(square_name)
            return

        if self.mode == "ai_vs_ai":
            return  # demo, no clicks

        if self.engine.board.is_game_over():
            self.show_game_result()
            return

        # === Selection phase ===
        if self.selected_square is None:
            piece = self.engine.board.piece_at(chess.parse_square(square_name))
            if piece is None or piece.color != self.engine.board.turn:
                return
            self.selected_square = square_name
            self.legal_target_squares = self._legal_targets_from(square_name)
            self._update_board_display()
            return

        # === Move-execution phase ===
        from_square = self.selected_square
        to_square = square_name
        player_move = from_square + to_square

        self.selected_square = None
        self.legal_target_squares = []
        self.suggested_move_squares = []

        ok, move_data = self.engine.make_player_move(player_move)
        if not ok:
            self.current_move_label.setText("Move: Invalid")
            self._update_board_display()
            return

        # Label move with the player's name (or "Player" for ai_pure)
        if self.mode == "pvp":
            # The move was just pushed; turn now belongs to the OTHER side
            mover = self.settings["white_name"] if self.engine.board.turn == chess.BLACK \
                else self.settings["black_name"]
        else:  # ai_pure
            mover = self.settings["white_name"]

        self._apply_move_visuals(move_data, mover)

        if self.engine.board.is_game_over():
            self.show_game_result()
            return

        # === Mode-specific follow-up ===
        if self.mode == "ai_pure":
            # AI plays its reply automatically
            self._update_board_display()
            QTimer.singleShot(150, self._make_ai_pure_reply)

        elif self.mode == "pvp":
            self._update_board_display()
            self._update_pvp_hint_for_current_turn()

    def _make_ai_pure_reply(self):
        """In ai_pure mode, AI plays a move directly on the GUI board (no robot)."""
        if self.engine.board.is_game_over():
            self.show_game_result()
            return

        move_data = self.engine.make_ai_move(time_limit=self.ai_time)
        self._apply_move_visuals(move_data, self.settings["black_name"])
        self._update_board_display()

        if self.engine.board.is_game_over():
            self.show_game_result()
            return

        # If the human's hint is on, refresh suggestion for the next turn
        if self.hint_white:
            self._show_suggestion_for_current_turn()

    def _inspect_only(self, square_name):
        """Click highlighting in ai_robot mode (no actual move)."""
        if self.engine.board.is_game_over():
            self.show_game_result()
            return

        piece = self.engine.board.piece_at(chess.parse_square(square_name))

        if self.selected_square == square_name:
            self.selected_square = None
            self.legal_target_squares = []
            self._update_board_display()
            return

        if piece is None or piece.color != self.engine.board.turn:
            self.selected_square = None
            self.legal_target_squares = []
            self._update_board_display()
            return

        self.selected_square = square_name
        self.legal_target_squares = self._legal_targets_from(square_name)
        self._update_board_display()

    def _legal_targets_from(self, square_name):
        source = chess.parse_square(square_name)
        return [
            chess.square_name(m.to_square)
            for m in self.engine.board.legal_moves
            if m.from_square == source
        ]

    def _would_be_en_passant(self, target_square):
        """Check if moving the currently selected piece to target_square would
        be an en passant capture (empty target square, but it's a capture)."""
        if self.selected_square is None:
            return False
        try:
            from_sq = chess.parse_square(self.selected_square)
            to_sq = chess.parse_square(target_square)
            for move in self.engine.board.legal_moves:
                if move.from_square == from_sq and move.to_square == to_sq:
                    return self.engine.board.is_en_passant(move)
        except Exception:
            pass
        return False

    # === Board rendering ===

    def _square_color(self, name):
        f = ord(name[0]) - ord("a")
        r = int(name[1]) - 1
        return "#f0d9b5" if (f + r) % 2 == 0 else "#b58863"

    def _style_square(self, name):
        button = self.square_buttons[name]
        bg = self._square_color(name)
        border = "1px solid #1a1a1a"

        # Last move highlight: yellow normally, red on the capture destination
        if name in self.last_move_squares:
            is_capture_dest = (
                self.last_move_was_capture
                and len(self.last_move_squares) >= 2
                and name == self.last_move_squares[1]
            )
            bg = "#ff5555" if is_capture_dest else "#f7ec6e"

        # Suggested move highlight: purple normally, red on the capture destination
        if name in self.suggested_move_squares:
            is_capture_dest = (
                self.suggested_move_is_capture
                and len(self.suggested_move_squares) >= 2
                and name == self.suggested_move_squares[1]
            )
            bg = "#ff5555" if is_capture_dest else "#c77dff"

        # Legal-move targets when piece is selected: green normally,
        # red if moving to that square would capture
        if name in self.legal_target_squares:
            target_piece = self.engine.board.piece_at(chess.parse_square(name))
            if target_piece is not None:
                bg = "#ff5555"
            else:
                # Also check en passant — empty square but the move IS a capture
                if self._would_be_en_passant(name):
                    bg = "#ff5555"
                else:
                    bg = "#55dd88"

        if name == self.selected_square:
            bg = "#00aaff"
            border = "4px solid #ffffff"

        button.setStyleSheet(f"""
            QPushButton {{
                background-color: {bg}; color: #111111;
                border: {border}; font-size: 46px;
                font-family: DejaVu Sans; padding: 0px; border-radius: 0px;
            }}
            QPushButton:hover {{ border: 4px solid #ff8c00; }}
        """)

    def _update_board_display(self):
        for name, button in self.square_buttons.items():
            piece = self.engine.board.piece_at(chess.parse_square(name))
            button.setText(PIECE_SYMBOLS[piece.symbol()] if piece else "")
            self._style_square(name)

    def _apply_move_visuals(self, move_data, player_name):
        captured = move_data.get("captured_piece")
        if captured:
            self._add_captured(captured)

        self.last_move_squares = [move_data["from"], move_data["to"]]
        self.last_move_was_capture = captured is not None

        is_ai_label = player_name in ("AI", "White AI", "Black AI") \
            or (self.mode == "ai_pure" and player_name == self.settings["black_name"])

        if is_ai_label:
            self.ai_move_label.setText(f"AI Move: {move_data['uci']}")
            if self.uses_robot:
                self.robot_status_label.setText("Robot Status: Simulated Move Sent")
                self.robot_preview_label.setText(
                    f"Robot Preview:\nPick: {move_data['from']}\nDrop: {move_data['to']}"
                )
        else:
            self.current_move_label.setText(f"Move: {move_data['uci']}")

        self.move_history.append(f"{player_name}: {move_data['uci']}")

    def _add_captured(self, piece):
        symbol = PIECE_SYMBOLS[piece.symbol()]
        if piece.color == chess.WHITE:
            self.captured_white.append(symbol)
        else:
            self.captured_black.append(symbol)

        self.captured_label.setText(
            f"Captured White:\n{''.join(self.captured_white) or '-'}\n\n"
            f"Captured Black:\n{''.join(self.captured_black) or '-'}"
        )

    # === Hints (per-mode) ===

    def _toggle_hint_single(self):
        """ai_pure: single hint button for the human (white)."""
        self.hint_white = not self.hint_white
        self.hint_btn_single.setText(
            "AI Hint: ON" if self.hint_white else "AI Hint: OFF"
        )
        if self.hint_white and self.engine.board.turn == chess.WHITE:
            self._show_suggestion_for_current_turn()
        else:
            self._clear_suggestion()

    def _toggle_hint_white(self):
        """pvp: hint button for white player."""
        self.hint_white = not self.hint_white
        self.hint_btn_white.setText(
            f"{self.settings['white_name']} Hint: " +
            ("ON" if self.hint_white else "OFF")
        )
        self._update_pvp_hint_for_current_turn()

    def _toggle_hint_black(self):
        """pvp: hint button for black player."""
        self.hint_black = not self.hint_black
        self.hint_btn_black.setText(
            f"{self.settings['black_name']} Hint: " +
            ("ON" if self.hint_black else "OFF")
        )
        self._update_pvp_hint_for_current_turn()

    def _update_pvp_hint_for_current_turn(self):
        """Show hint only for the player whose turn it is, IF their hint is on."""
        if self.engine.board.is_game_over():
            self._clear_suggestion()
            return

        is_white_turn = self.engine.board.turn == chess.WHITE
        show = (is_white_turn and self.hint_white) or (not is_white_turn and self.hint_black)

        if show:
            self._show_suggestion_for_current_turn()
        else:
            self._clear_suggestion()

    def _show_suggestion_for_current_turn(self):
        move = self.engine.get_best_move(time_limit=0.25)
        self.suggested_move_squares = [
            chess.square_name(move.from_square),
            chess.square_name(move.to_square),
        ]
        self.suggested_move_is_capture = self.engine.board.is_capture(move)
        self.ai_move_label.setText(f"Suggested Move: {move}")
        self._update_board_display()

    def _clear_suggestion(self):
        self.suggested_move_squares = []
        self.suggested_move_is_capture = False
        self.ai_move_label.setText("Suggested Move: OFF")
        self._update_board_display()

    # === AI vs AI demo ===

    def make_ai_vs_ai_move(self):
        if self.engine.board.is_game_over():
            self.ai_vs_ai_timer.stop()
            self.show_game_result()
            return

        move_data = self.engine.make_ai_move(time_limit=self.ai_time)
        side = "White AI" if self.engine.board.turn == chess.BLACK else "Black AI"
        self._apply_move_visuals(move_data, side)
        self._update_board_display()

    # === Scoreboard ===

    def _update_scoreboard(self):
        self.scoreboard_label.setText(
            f"{self.settings['white_name']} Wins: {self.white_wins}\n"
            f"{self.settings['black_name']} Wins: {self.black_wins}\n"
            f"Draws: {self.draws}\n"
            f"Total Games: {self.total_games}"
        )

    def reset_scoreboard(self):
        self.white_wins = 0
        self.black_wins = 0
        self.draws = 0
        self.total_games = 0
        self.game_counted = False
        self._update_scoreboard()

    # === Game over ===

    def show_game_result(self):
        result = self.engine.board.result()

        if self.engine.board.is_checkmate():
            title = "♔ CHECKMATE ♔"
            winner = "Black Wins!" if self.engine.board.turn == chess.WHITE else "White Wins!"
            reason = "The king has no escape. Clean finish."
        elif self.engine.board.is_stalemate():
            title, winner, reason = "♟ STALEMATE ♟", "Draw", "No legal moves, but the king is not in check."
        elif self.engine.board.is_insufficient_material():
            title, winner, reason = "♟ DRAW ♟", "Draw", "Insufficient material to checkmate."
        elif self.engine.board.is_seventyfive_moves():
            title, winner, reason = "♟ DRAW ♟", "Draw", "75-move rule reached."
        elif self.engine.board.is_fivefold_repetition():
            title, winner, reason = "♟ DRAW ♟", "Draw", "Fivefold repetition occurred."
        else:
            title, winner, reason = "♟ GAME OVER ♟", "Match Finished", "The game has ended."

        if not self.game_counted:
            self.total_games += 1
            if winner == "White Wins!":
                self.white_wins += 1
            elif winner == "Black Wins!":
                self.black_wins += 1
            else:
                self.draws += 1
            self.game_counted = True
            self._update_scoreboard()

        dialog = GameOverDialog(title, winner, result, reason, self)
        dialog.exec()

    # === Reset / navigation ===

    def reset_game(self):
        self.engine.reset()
        self.engine.set_difficulty(self.settings["difficulty"])

        self.selected_square = None
        self.legal_target_squares = []
        self.last_move_squares = []
        self.last_move_was_capture = False
        self.suggested_move_squares = []
        self.suggested_move_is_capture = False
        self.captured_white = []
        self.captured_black = []
        self.pending_ai_move = None
        self.pending_ai_move_data = None
        self.game_counted = False

        # Reset hints (always default OFF)
        self.hint_white = False
        self.hint_black = False
        if hasattr(self, "hint_btn_single"):
            self.hint_btn_single.setText("AI Hint: OFF")
        if hasattr(self, "hint_btn_white"):
            self.hint_btn_white.setText(f"{self.settings['white_name']} Hint: OFF")
        if hasattr(self, "hint_btn_black"):
            self.hint_btn_black.setText(f"{self.settings['black_name']} Hint: OFF")

        # Reset auto-detect state (ai_robot only)
        if self.mode == "ai_robot" and hasattr(self, "auto_detect_btn"):
            self.auto_detect_btn.setChecked(False)
            self.auto_detect_btn.setText("Auto Detect: OFF")
            self.camera_ref_btn.setEnabled(True)
            self.detect_move_btn.setEnabled(True)
            if self.vision is not None:
                self.vision.set_auto_enabled(False)

        # Mode-specific opening message
        if self.mode == "ai_robot":
            self.current_move_label.setText("Click 'Save Reference' before your move")
        elif self.mode == "ai_pure":
            self.current_move_label.setText("Click a piece to move")
        elif self.mode == "pvp":
            self.current_move_label.setText("Player Move: -")
        else:
            self.current_move_label.setText("AI Demo Running...")

        self.ai_move_label.setText("AI Move: -")
        if self.uses_robot:
            self.robot_status_label.setText("Robot Status: Idle")
            self.robot_preview_label.setText("Robot Preview:\nPick: -\nDrop: -")
        self.captured_label.setText("Captured White:\n-\n\nCaptured Black:\n-")
        self.move_history.clear()
        self.robot_done_btn.setEnabled(False)

        self._update_board_display()

        if self.mode == "ai_vs_ai":
            self.ai_vs_ai_timer.start(1300)

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showMaximized()
        else:
            self.showFullScreen()

    def back_to_menu(self):
        self.ai_vs_ai_timer.stop()
        self._stop_vision_thread()
        if self.robot is not None:
            try:
                self.robot.close()
            except Exception:
                pass
        self.engine.close()
        self.close()
        self.back_callback()

    def closeEvent(self, event):
        self.ai_vs_ai_timer.stop()
        self._stop_vision_thread()
        if self.robot is not None:
            try:
                self.robot.close()
            except Exception:
                pass
        try:
            self.engine.close()
        except Exception:
            pass
        event.accept()
