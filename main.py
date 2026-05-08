"""
RoboGambit entry point.

Run with:  python main.py
"""
import sys

from PySide6.QtWidgets import QApplication

from gui.menu_window import MenuWindow
from gui.game_window import GameWindow


class RoboGambitApp:
    def __init__(self):
        self.app = QApplication(sys.argv)
        self.app.setApplicationName("RoboGambit")
        self.menu = None
        self.game = None

    def show_menu(self):
        self.menu = MenuWindow(self.start_game)
        self.menu.showMaximized()

    def start_game(self, mode, settings):
        self.menu.hide()
        self.game = GameWindow(mode, settings, self.return_to_menu)
        if settings.get("fullscreen", False):
            self.game.showFullScreen()
        else:
            self.game.showMaximized()

    def return_to_menu(self):
        self.show_menu()

    def run(self):
        self.show_menu()
        exit_code = self.app.exec()

        # Cleanly shut down ROS context if it was used.
        # No-op if rclpy was never imported.
        try:
            from robot.ros_robot import shutdown_ros
            shutdown_ros()
        except Exception:
            pass

        sys.exit(exit_code)


if __name__ == "__main__":
    RoboGambitApp().run()