from PySide6.QtWidgets import (
    QMainWindow, QWidget, QLabel, QPushButton, QVBoxLayout,
    QHBoxLayout, QLineEdit, QComboBox, QDialog, QFormLayout,
    QDialogButtonBox,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap

from config.settings import LOGO_PATH


class SettingsDialog(QDialog):
    """Stripped down — only difficulty + player names. No more toggles."""

    def __init__(self, settings, parent=None):
        super().__init__(parent)

        self.setWindowTitle("RoboGambit Settings")
        self.setMinimumSize(420, 240)
        self.settings = settings

        layout = QFormLayout()

        self.difficulty_box = QComboBox()
        self.difficulty_box.addItems(["Easy", "Medium", "Hard"])
        self.difficulty_box.setCurrentText(settings["difficulty"])

        self.white_name = QLineEdit(settings["white_name"])
        self.black_name = QLineEdit(settings["black_name"])

        layout.addRow("AI Difficulty:", self.difficulty_box)
        layout.addRow("White Player Name:", self.white_name)
        layout.addRow("Black Player / AI Name:", self.black_name)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save_settings)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.setLayout(layout)

        self.setStyleSheet("""
            QDialog { background-color: #0b0f14; color: white; }
            QLabel { color: white; font-size: 15px; }
            QLineEdit, QComboBox {
                background-color: #101820; color: white;
                border: 1px solid #ff8c00; border-radius: 8px;
                padding: 8px; font-size: 15px;
            }
            QComboBox QAbstractItemView {
                background-color: #101820; color: white;
                selection-background-color: #ff8c00; selection-color: black;
                border: 1px solid #ff8c00;
            }
            QPushButton {
                background-color: #ff8c00; color: black;
                border-radius: 8px; padding: 8px 16px; font-weight: bold;
            }
        """)

    def save_settings(self):
        self.settings["difficulty"] = self.difficulty_box.currentText()
        self.settings["white_name"] = self.white_name.text().strip() or "White"
        self.settings["black_name"] = self.black_name.text().strip() or "Black"
        self.accept()


class MenuWindow(QMainWindow):
    def __init__(self, start_game_callback):
        super().__init__()
        self.start_game_callback = start_game_callback

        # Settings: simplified. Hints are now per-game-mode and default OFF;
        # they're toggled inside the game window, not here.
        # Fullscreen is the default for every mode.
        self.settings = {
            "difficulty": "Medium",
            "white_name": "White",
            "black_name": "Black",
        }

        self.setWindowTitle("RoboGambit Main Menu")
        self.setMinimumSize(1100, 750)

        root = QWidget()
        layout = QVBoxLayout()
        layout.setAlignment(Qt.AlignCenter)

        logo = QLabel()
        logo.setAlignment(Qt.AlignCenter)
        import os as _os
        if _os.path.exists(LOGO_PATH):
            pixmap = QPixmap(LOGO_PATH)
            logo.setPixmap(pixmap.scaled(360, 360, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            logo.setText("♟ RoboGambit")
            logo.setStyleSheet("font-size: 54px; font-weight: bold; color: #ff8c00;")

        title = QLabel("ROBOGAMBIT")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 48px; font-weight: bold; color: #ff8c00;")

        subtitle = QLabel("Smart Moves. Precision Play.")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet("font-size: 20px; color: #00ff99;")

        # === Game mode buttons ===
        ai_robot_btn = QPushButton("Player vs AI (Robot)")
        ai_pure_btn = QPushButton("Player vs AI")
        pvp_btn = QPushButton("Player vs Player")
        ai_vs_ai_btn = QPushButton("AI vs AI Demo")
        settings_btn = QPushButton("Settings")
        exit_btn = QPushButton("Exit")

        ai_robot_btn.clicked.connect(lambda: self.start_game_callback("ai_robot", self.settings))
        ai_pure_btn.clicked.connect(lambda: self.start_game_callback("ai_pure", self.settings))
        pvp_btn.clicked.connect(lambda: self.start_game_callback("pvp", self.settings))
        ai_vs_ai_btn.clicked.connect(lambda: self.start_game_callback("ai_vs_ai", self.settings))
        settings_btn.clicked.connect(self.open_settings)
        exit_btn.clicked.connect(self.close)

        # First row: the two human-vs-AI modes (the headline features)
        button_row_1 = QHBoxLayout()
        button_row_1.addWidget(ai_robot_btn)
        button_row_1.addWidget(ai_pure_btn)

        # Second row: PvP + demo
        button_row_2 = QHBoxLayout()
        button_row_2.addWidget(pvp_btn)
        button_row_2.addWidget(ai_vs_ai_btn)

        # Third row: settings + exit
        button_row_3 = QHBoxLayout()
        button_row_3.addWidget(settings_btn)
        button_row_3.addWidget(exit_btn)

        layout.addWidget(logo)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addSpacing(30)
        layout.addLayout(button_row_1)
        layout.addSpacing(10)
        layout.addLayout(button_row_2)
        layout.addSpacing(10)
        layout.addLayout(button_row_3)

        root.setLayout(layout)
        self.setCentralWidget(root)

        self.setStyleSheet("""
            QMainWindow { background-color: #0b0f14; }
            QPushButton {
                background-color: #ff8c00; color: black;
                border-radius: 14px; padding: 16px 22px;
                font-size: 17px; font-weight: bold; min-width: 220px;
            }
            QPushButton:hover {
                background-color: #ffaa33; border: 2px solid white;
            }
        """)

    def open_settings(self):
        dialog = SettingsDialog(self.settings, self)
        dialog.exec()
