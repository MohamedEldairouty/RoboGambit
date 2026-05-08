"""
Central configuration for RoboGambit.

Edit values here instead of hunting through code. Keep this file lightweight —
nothing here should import from the rest of the project.
"""
import os
import shutil

# === Paths ===
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS_DIR = os.path.join(PROJECT_ROOT, "assets")
CONFIG_DIR = os.path.join(PROJECT_ROOT, "config")
LOGO_PATH = os.path.join(ASSETS_DIR, "logo.png")
SQDICT_PATH = os.path.join(CONFIG_DIR, "sqdict.json")

# === Camera ===
# Linux camera index. Try 0 first; many laptops have built-in cam at 0
# and external USB cam at 1. Run `ls /dev/video*` to see what's connected.
CAMERA_INDEX = 1

# Live preview frame rate target (Hz). Vision detection is event-driven so
# this only affects the live feed shown in the GUI.
PREVIEW_FPS = 20

# === Stockfish ===
# Auto-detect first; fall back to apt's default install path.
STOCKFISH_PATH = shutil.which("stockfish") or "/usr/games/stockfish"

# === Move detection thresholds ===
# Pixel intensity diff threshold for the binary thresholding step.
# Lower = more sensitive (catches subtle changes but may have false positives).
MOVE_THRESHOLD = 25

# Minimum contour area (px^2) to be considered a real piece change.
# Filters out shadow flicker, hand glints, etc.
MIN_CONTOUR_AREA = 250

# === Robot backend ===
# Switch between "fake" (prints to console) and "ros" (publishes to ROS topic).
# Your teammates' ROS node should subscribe to whatever ros_robot.py publishes.
ROBOT_BACKEND = "fake"  # "fake" | "ros"
