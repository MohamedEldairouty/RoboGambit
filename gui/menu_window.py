from PySide6.QtWidgets import (
    QMainWindow, QWidget, QLabel, QPushButton, QVBoxLayout,
    QHBoxLayout, QLineEdit, QComboBox, QDialog, QFormLayout,
    QDialogButtonBox, QCheckBox,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap

from config.settings import LOGO_PATH


class SettingsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)

        self.setWindowTitle("RoboGambit Settings")
        self.setMinimumSize(440, 360)
        self.settings = settings

        layout = QFormLayout()

        self.difficulty_box = QComboBox()
        self.difficulty_box.addItems(["Easy", "Medium", "Hard"])
        self.difficulty_box.setCurrentText(settings["difficulty"])

        self.white_name = QLineEdit(settings["white_name"])
        self.black_name = QLineEdit(settings["black_name"])

        self.hints_checkbox = QCheckBox("Enable AI suggested move in PvP")
        self.hints_checkbox.setChecked(settings["pvp_hints"])

        self.fullscreen_checkbox = QCheckBox("Start games in fullscreen")
        self.fullscreen_checkbox.setChecked(settings["fullscreen"])

        layout.addRow("AI Difficulty:", self.difficulty_box)
        layout.addRow("White Player Name:", self.white_name)
        layout.addRow("Black Player / AI Name:", self.black_name)
        layout.addRow("", self.hints_checkbox)
        layout.addRow("", self.fullscreen_checkbox)

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
            QCheckBox { color: white; font-size: 15px; }
            QPushButton {
                background-color: #ff8c00; color: black;
                border-radius: 8px; padding: 8px 16px; font-weight: bold;
            }
        """)

    def save_settings(self):
        self.settings["difficulty"] = self.difficulty_box.currentText()
        self.settings["white_name"] = self.white_name.text().strip() or "White"
        self.settings["black_name"] = self.black_name.text().strip() or "Black"
        self.settings["pvp_hints"] = self.hints_checkbox.isChecked()
        self.settings["fullscreen"] = self.fullscreen_checkbox.isChecked()
        self.accept()


class MenuWindow(QMainWindow):
    def __init__(self, start_game_callback):
        super().__init__()
        self.start_game_callback = start_game_callback

        self.settings = {
            "difficulty": "Medium",
            "white_name": "White",
            "black_name": "Black",
            "pvp_hints": True,
            "fullscreen": False,
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

        ai_btn = QPushButton("Player vs AI")
        pvp_btn = QPushButton("Player vs Player")
        ai_vs_ai_btn = QPushButton("AI vs AI Demo")
        settings_btn = QPushButton("Settings")
        exit_btn = QPushButton("Exit")

        ai_btn.clicked.connect(lambda: self.start_game_callback("ai", self.settings))
        pvp_btn.clicked.connect(lambda: self.start_game_callback("pvp", self.settings))
        ai_vs_ai_btn.clicked.connect(lambda: self.start_game_callback("ai_vs_ai", self.settings))
        settings_btn.clicked.connect(self.open_settings)
        exit_btn.clicked.connect(self.close)

        button_row_1 = QHBoxLayout()
        button_row_1.addWidget(ai_btn)
        button_row_1.addWidget(pvp_btn)
        button_row_1.addWidget(ai_vs_ai_btn)

        button_row_2 = QHBoxLayout()
        button_row_2.addWidget(settings_btn)
        button_row_2.addWidget(exit_btn)

        layout.addWidget(logo)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addSpacing(30)
        layout.addLayout(button_row_1)
        layout.addSpacing(10)
        layout.addLayout(button_row_2)

        root.setLayout(layout)
        self.setCentralWidget(root)

        self.setStyleSheet("""
            QMainWindow { background-color: #0b0f14; }
            QPushButton {
                background-color: #ff8c00; color: black;
                border-radius: 14px; padding: 16px 22px;
                font-size: 17px; font-weight: bold; min-width: 180px;
            }
            QPushButton:hover {
                background-color: #ffaa33; border: 2px solid white;
            }
        """)

    def open_settings(self):
        dialog = SettingsDialog(self.settings, self)
        dialog.exec()
