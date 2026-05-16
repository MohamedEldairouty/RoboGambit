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
CAMERA_INDEX = 0

# Live preview frame rate target (Hz). Vision detection is event-driven so
# this only affects the live feed shown in the GUI.
PREVIEW_FPS = 20

# Rotate every camera frame before processing. Use this when your phone or
# webcam is mounted sideways and DroidCam (or your camera app) doesn't expose
# a rotation option. Allowed: 0, 90, 180, 270 (clockwise degrees).
#
# IMPORTANT: This affects BOTH calibration and live tracking. If you change
# this value, you MUST re-run calibration so sqdict.json matches the new
# orientation. Otherwise the polygons won't align with the real squares.
FRAME_ROTATION = 0

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

# === Auto-detection (motion-then-stillness state machine) ===
# When enabled in the GUI, vision watches the board continuously instead of
# requiring "Save Reference" + "Detect Move" button presses.
#
# State flow:
#   STABLE  --(motion spike)-->  MOTION
#   MOTION  --(stillness for STABLE_DURATION sec)-->  CHECK
#   CHECK   --(diff vs baseline)-->  emit move OR back to STABLE
#
# Tune these for your lighting/camera. Watch the terminal output of the
# motion level to pick good values for your setup.

# Pixel-diff sum threshold below which we consider the scene "still".
# Compute: count of pixels that changed > MOTION_PIXEL_DELTA between consecutive frames.
AUTO_STILLNESS_THRESHOLD = 800

# Pixel-diff sum threshold above which motion is detected (entering MOTION state).
# Should be higher than AUTO_STILLNESS_THRESHOLD to avoid chattering.
AUTO_MOTION_THRESHOLD = 2500

# Per-pixel intensity delta to count as "changed" when computing motion level.
# Higher = ignore more lighting noise; lower = catch subtler movement.
MOTION_PIXEL_DELTA = 20

# How long the scene must remain "still" (seconds) before we trigger detection.
# Too short = catches mid-move pauses; too long = sluggish feel.
AUTO_STABLE_DURATION = 2.0

# Safety: if more than this many squares appear changed, reject the detection.
# A real chess move changes at most 4 squares (castling). More than that means
# something went wrong (camera bumped, lighting flickered, hand still in frame).
AUTO_MAX_CHANGED_SQUARES = 4

# === Robot backend ===
# Switch between "fake" (prints to console) and "ros" (publishes to ROS topic).
# Your teammates' ROS node should subscribe to whatever ros_robot.py publishes.
ROBOT_BACKEND = "ros"  # "fake" | "ros"

# ROS topic the GUI publishes chess moves to.
# Hardware team's node should subscribe to this topic. The published message
# is a std_msgs/String containing UCI-formatted chess moves like "e2e4".
ROS_MOVE_TOPIC = "/robogambit/move"

# ROS node name for our publisher. Visible in `ros2 node list`.
ROS_NODE_NAME = "robogambit_gui"