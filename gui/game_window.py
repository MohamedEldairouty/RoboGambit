"""
Main game window.

Threading model:
  - GUI thread:  owns ChessEngine, board UI, all click handlers.
  - Vision QThread: owns camera, runs MoveDetector. Communicates ONLY via
    signals: frame_ready (preview), move_detected (uci squares), error/status.

Game flow (AI mode):
  1. Game starts. Vision thread runs, showing live preview.
  2. User clicks "Save Reference" before making a physical move.
  3. User makes their move on the real board, then clicks "Detect Move".
  4. Vision diff -> emits move_detected(from, to).
  5. GUI validates move via ChessEngine, updates display.
  6. GUI asks engine for AI reply, sends to robot (FakeRobot or ROSRobot).
  7. User clicks "Robot Move Done" once the arm finishes.
  8. GUI auto-saves new reference frame, ready for next human move.
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

        # === Domain objects ===
        self.engine = ChessEngine()
        self.engine.set_difficulty(settings["difficulty"])
        self.ai_time = self._ai_time_for(settings["difficulty"])

        self.robot = get_robot_backend()

        # Vision worker (only started in AI mode where camera is needed)
        self.vision = None

        # === UI state ===
        self.selected_square = None
        self.square_buttons = {}
        self.legal_target_squares = []
        self.last_move_squares = []
        self.suggested_move_squares = []
        self.captured_white = []
        self.captured_black = []

        # === Scoreboard ===
        self.white_wins = 0
        self.black_wins = 0
        self.draws = 0
        self.total_games = 0
        self.game_counted = False

        # === AI move pipeline state ===
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
        if self.mode == "ai_vs_ai":
            self.mode_label.setText("Mode: AI vs AI Demo")
            self.current_move_label.setText("AI Demo Running...")
            self.robot_status_label.setText("Robot Status: Demo Mode")
            self.ai_vs_ai_timer.start(1300)

        elif self.mode == "pvp":
            self.mode_label.setText("Mode: Player vs Player")
            self.robot_status_label.setText("Robot Status: Not Used")
            if self.settings["pvp_hints"]:
                self._update_suggested_move()

        else:  # "ai" mode — start the vision thread
            self.mode_label.setText("Mode: Player vs AI")
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

        self.hint_btn = QPushButton(
            "AI Hint: ON" if self.settings["pvp_hints"] else "AI Hint: OFF"
        )
        self.hint_btn.clicked.connect(self.toggle_pvp_hint)

        # === Vision/robot controls (AI mode only) ===
        self.camera_ref_btn = QPushButton("1. Save Reference")
        self.camera_ref_btn.clicked.connect(self.on_save_reference_clicked)

        self.detect_move_btn = QPushButton("2. Detect Human Move")
        self.detect_move_btn.clicked.connect(self.on_detect_move_clicked)

        self.robot_done_btn = QPushButton("3. Robot Move Done")
        self.robot_done_btn.clicked.connect(self.on_robot_done_clicked)
        self.robot_done_btn.setEnabled(False)

        fullscreen_btn = QPushButton("Toggle Fullscreen")
        fullscreen_btn.clicked.connect(self.toggle_fullscreen)

        back_btn = QPushButton("Back to Menu")
        back_btn.clicked.connect(self.back_to_menu)

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

        if self.mode == "pvp":
            layout.addWidget(self.hint_btn)

        if self.mode == "ai":
            layout.addSpacing(8)
            vision_title = QLabel("— Camera & Robot —")
            vision_title.setAlignment(Qt.AlignCenter)
            vision_title.setStyleSheet("color: #00ff99; font-weight: bold;")
            layout.addWidget(vision_title)
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

        title = QLabel("Move + Robot Panel")
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

        # === Camera live preview (AI mode only) ===
        self.camera_view = QLabel("Camera feed will appear here\n(AI mode only)")
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
        layout.addWidget(self.robot_status_label)
        layout.addWidget(self.robot_preview_label)
        if self.mode == "ai":
            layout.addWidget(self.camera_view, 1)
        layout.addWidget(self.captured_label)
        layout.addWidget(history_title)
        layout.addWidget(self.move_history)

        panel.setLayout(layout)
        return panel

    # === Vision thread lifecycle ===

    def _start_vision_thread(self):
        self.vision = VisionWorker(parent=self)
        self.vision.frame_ready.connect(self._on_vision_frame)
        self.vision.move_detected.connect(self._on_vision_move_detected)
        self.vision.status.connect(self._on_vision_status)
        self.vision.error.connect(self._on_vision_error)
        self.vision.start()

    def _stop_vision_thread(self):
        if self.vision is not None:
            self.vision.stop()
            self.vision.quit()
            self.vision.wait(2000)
            self.vision = None

    # === Vision signal handlers ===

    def _on_vision_frame(self, qimg: QImage):
        # Scale to fit the camera_view label without distortion
        pix = QPixmap.fromImage(qimg)
        scaled = pix.scaled(
            self.camera_view.size(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self.camera_view.setPixmap(scaled)

    def _on_vision_status(self, msg: str):
        # Show in robot_status_label so it's visible
        self.robot_status_label.setText(f"Vision: {msg}")

    def _on_vision_error(self, msg: str):
        self.robot_status_label.setText(f"Vision Error: {msg[:60]}")
        self.move_history.append(f"[VISION ERROR] {msg}")

    def _on_vision_move_detected(self, from_sq: str, to_sq: str):
        """Called from GUI thread (auto-marshalled by Qt) when vision finds a move."""
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

    # === Vision/robot button handlers ===

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
        # Pass a copy of the current board for legal-move disambiguation
        self.vision.request_detection(self.engine.board.copy())

    def on_robot_done_clicked(self):
        if self.pending_ai_move is None:
            return

        # Now actually push the AI move onto our board (we held off until
        # the physical robot finished, to keep GUI in sync with reality)
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

        # Auto-save a fresh reference frame for the next human move
        if self.vision is not None:
            self.vision.set_reference()
            self.current_move_label.setText("Reference auto-saved. Make your move.")

    # === AI move pipeline ===

    def _prepare_ai_robot_move(self):
        """Compute the AI's reply but DON'T apply it to the board yet —
        we wait for the physical robot to finish moving first."""
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

        # Send to robot backend (FakeRobot prints; ROSRobot publishes)
        try:
            self.robot.send_move(self.pending_ai_move_data)
        except Exception as e:
            self.move_history.append(f"[ROBOT ERROR] {e}")

        self.robot_done_btn.setEnabled(True)

    # === Click-based moves (PvP / AI-vs-AI / pre-camera fallback) ===

    def handle_square_click(self, square_name):
        if self.mode == "ai":
            # In AI mode the human plays on the physical board, not by clicking.
            # Allow clicks only for inspection/highlighting.
            self._inspect_only(square_name)
            return

        if self.mode == "ai_vs_ai":
            return

        if self.engine.board.is_game_over():
            self.show_game_result()
            return

        if self.selected_square is None:
            piece = self.engine.board.piece_at(chess.parse_square(square_name))
            if piece is None or piece.color != self.engine.board.turn:
                return
            self.selected_square = square_name
            self.legal_target_squares = self._legal_targets_from(square_name)
            self._update_board_display()
            return

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

        self._apply_move_visuals(move_data, "Player")

        if self.engine.board.is_game_over():
            self.show_game_result()
            return

        if self.mode == "pvp" and self.settings["pvp_hints"]:
            self._update_suggested_move()

        self._update_board_display()

    def _inspect_only(self, square_name):
        """Allow click highlighting in AI mode without moving pieces."""
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

    # === Board rendering ===

    def _square_color(self, name):
        f = ord(name[0]) - ord("a")
        r = int(name[1]) - 1
        return "#f0d9b5" if (f + r) % 2 == 0 else "#b58863"

    def _style_square(self, name):
        button = self.square_buttons[name]
        bg = self._square_color(name)
        border = "1px solid #1a1a1a"

        if name in self.last_move_squares:
            bg = "#f7ec6e"
        if name in self.suggested_move_squares:
            bg = "#c77dff"
        if name in self.legal_target_squares:
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

        if player_name in ("AI", "White AI", "Black AI"):
            self.ai_move_label.setText(f"AI Move: {move_data['uci']}")
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

    # === Hints / suggestions ===

    def _update_suggested_move(self):
        if self.mode != "pvp" or not self.settings["pvp_hints"]:
            self.suggested_move_squares = []
            self.ai_move_label.setText("Suggested Move: OFF")
            return

        move = self.engine.get_best_move(time_limit=0.25)
        self.suggested_move_squares = [
            chess.square_name(move.from_square),
            chess.square_name(move.to_square),
        ]
        self.ai_move_label.setText(f"Suggested Move: {move}")
        self._update_board_display()

    def toggle_pvp_hint(self):
        self.settings["pvp_hints"] = not self.settings["pvp_hints"]
        self.hint_btn.setText(
            "AI Hint: ON" if self.settings["pvp_hints"] else "AI Hint: OFF"
        )
        if self.settings["pvp_hints"]:
            self._update_suggested_move()
        else:
            self.suggested_move_squares = []
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
            title = "♟ STALEMATE ♟"
            winner = "Draw"
            reason = "No legal moves, but the king is not in check."
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
        self.suggested_move_squares = []
        self.captured_white = []
        self.captured_black = []
        self.pending_ai_move = None
        self.pending_ai_move_data = None
        self.game_counted = False

        self.current_move_label.setText(
            "Click 'Save Reference' before your move" if self.mode == "ai" else "Player Move: -"
        )
        self.ai_move_label.setText("AI Move: -")
        self.robot_status_label.setText(
            "Robot Status: Idle" if self.mode == "ai" else "Robot Status: Idle"
        )
        self.robot_preview_label.setText("Robot Preview:\nPick: -\nDrop: -")
        self.captured_label.setText("Captured White:\n-\n\nCaptured Black:\n-")
        self.move_history.clear()
        self.robot_done_btn.setEnabled(False)

        self._update_board_display()

        if self.mode == "ai_vs_ai":
            self.ai_vs_ai_timer.start(1300)
        elif self.mode == "pvp" and self.settings["pvp_hints"]:
            self._update_suggested_move()

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showMaximized()
        else:
            self.showFullScreen()

    def back_to_menu(self):
        self.ai_vs_ai_timer.stop()
        self._stop_vision_thread()
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
        try:
            self.robot.close()
        except Exception:
            pass
        try:
            self.engine.close()
        except Exception:
            pass
        event.accept()
