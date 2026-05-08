# ♟ RoboGambit

A robotic chess system: a real player faces a robotic arm on a real board, while
a camera watches the game and a GUI mirrors every move in real time. The AI
opponent is powered by Stockfish.

This repo holds the **vision + AI + GUI** side of the project. The hardware
team's robot/ROS code lives in a separate repo (or sub-folder, TBD) and plugs
into this one through the `robot/` interface.

---

## 🧱 Architecture

```
            ┌──────────────────────────────────┐
            │            main.py               │
            │  PySide6 application entry point │
            └─────────────┬────────────────────┘
                          │
               ┌──────────▼──────────┐
               │     GameWindow      │
               │  (GUI thread)       │
               │  • Owns ChessEngine │
               │  • Orchestrates all │
               └─┬───────┬─────────┬─┘
                 │       │         │
                 │       │         │
       ┌─────────▼─┐ ┌───▼──────┐ ┌▼──────────────┐
       │ ChessEngine│ │VisionWorker│ │ RobotInterface │
       │            │ │ (QThread)  │ │  (abstract)    │
       │ • Stockfish│ │ • Camera   │ │ ├─ FakeRobot   │
       │ • Board    │ │ • Diff det.│ │ └─ ROSRobot    │
       └────────────┘ └─────┬──────┘ └────────┬───────┘
                            │ signals          │ ROS topic
                            │ (move_detected)  │ (later)
                            ▼                  ▼
                       MoveDetector      Hardware team's
                       (pure logic,      ROS node
                        no Qt)
```

**Why this split:**

- **Vision is in its own thread** — the camera doesn't freeze the GUI.
  Communication is via Qt signals, which are thread-safe.
- **Robot is behind an interface** — swap `FakeRobot` (prints to console) for
  `ROSRobot` (publishes to a topic) by changing one line in `config/settings.py`.
- **Pure detector has no Qt or chess engine** — easy to unit test and reuse.

---

## 📁 Project structure

```
robogambit/
├── main.py                    # Application entry point
├── config/
│   ├── settings.py            # Camera index, paths, thresholds — edit here
│   └── sqdict.json            # Calibration data (gitignored, generated)
├── engine/
│   └── chess_engine.py        # python-chess + Stockfish wrapper
├── gui/
│   ├── menu_window.py         # Main menu (game mode + settings)
│   └── game_window.py         # In-game window with board, camera, history
├── vision/
│   ├── calibrate.py           # One-time board corner calibration tool
│   ├── move_detector.py       # Pure detection logic (no Qt, no engine)
│   └── vision_worker.py       # QThread wrapper that emits Qt signals
├── robot/
│   ├── robot_interface.py     # Abstract base class + factory
│   ├── fake_robot.py          # Console-only stub (default)
│   └── ros_robot.py           # ROS publisher stub (filled in by HW team)
├── assets/
│   └── logo.png
├── scripts/
│   └── run_calibration.sh     # Convenience launcher for calibration
├── requirements.txt
├── .gitignore
└── README.md
```

---

## 🚀 Setup (Ubuntu 22.04+)

### 1. System packages

```bash
sudo apt update
sudo apt install -y stockfish python3-pip python3-venv
```

### 2. Clone and create venv

```bash
git clone <your-repo-url> robogambit
cd robogambit
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Find your camera index

```bash
ls /dev/video*
```

Most laptops have the built-in webcam at `/dev/video0` and external USB cameras
at `/dev/video1`. Set `CAMERA_INDEX` in `config/settings.py` accordingly.

### 4. Calibrate the board (one-time per setup)

Place your camera in its final fixed position, then:

```bash
./scripts/run_calibration.sh
# or:  python -m vision.calibrate
```

Click the four corners of the board in this order:
1. top-left (a8 from white's perspective)
2. top-right (h8)
3. bottom-right (h1)
4. bottom-left (a1)

Press `s` to save, `r` to reset, `q` to quit.

If your camera is mounted at an angle (e.g. on the right side of the board),
pass `--rotate 90` (clockwise rotation of the board relative to the camera):

```bash
python -m vision.calibrate --rotate 90
```

This generates `config/sqdict.json`, which maps each chess square to its
polygon in pixel space.

### 5. Run the game

```bash
python main.py
```

Pick **Player vs AI** to use the camera. The other modes (PvP, AI vs AI demo)
work without a camera.

---

## 🎮 How to play (AI mode)

The flow is button-driven for clarity (mapping cleanly to ROS triggers later):

1. **Save Reference** — captures the "before" frame. Click before each of your moves.
2. Make your physical move on the real board.
3. **Detect Human Move** — vision compares before/after, finds your move,
   GUI updates, AI computes its reply, robot command is sent.
4. **Robot Move Done** — click after the arm finishes its move. The GUI
   updates and auto-saves a new reference frame for your next turn.

Click the on-screen squares any time to inspect legal moves — clicks don't
move pieces in AI mode (the real board is the source of truth).

---

## 🤖 Integrating with ROS / the hardware team

The `robot/` package handles the bridge to the robotic arm. Two backends:

- **`fake`** (default): prints commands to console — for development without hardware
- **`ros`**: publishes UCI move strings to a ROS 2 topic — for real hardware

### What we publish

| Field | Value |
|---|---|
| Topic | `/robogambit/move` (configurable in `config/settings.py`) |
| Type | `std_msgs/String` |
| Payload | UCI move string, e.g. `"e2e4"`, `"g8f6"`, `"e1g1"` (castling), `"e7e8q"` (promotion) |
| Node name | `robogambit_gui` |

### Hardware team's responsibilities

The hardware team's existing ROS node (subscribed to `/nano_serial`) currently
forwards strings directly to Arduino. It needs to be modified to:

1. Subscribe to **`/robogambit/move`** instead of `/nano_serial`
2. Parse the UCI string (`"e2e4"` → from-square `e2`, to-square `e4`)
3. Run **inverse kinematics** to compute servo angles for pick + place sequence
4. Publish or forward those angles to Arduino over serial (their existing logic)

Alternatively, they can keep their architecture and add a translator node:
**`/robogambit/move`** → IK node → **`/nano_serial`** (angles) → existing serial node → Arduino.

### Activating ROS mode on our side

1. Install ROS 2 on your machine:
   ```bash
   # Ubuntu 24.04 → ROS 2 Jazzy
   # Ubuntu 22.04 → ROS 2 Humble
   ```
2. Recreate the venv with system site packages so it can see `rclpy`:
   ```bash
   rm -rf venv
   python3 -m venv venv --system-site-packages
   source venv/bin/activate
   pip install -r requirements.txt
   ```
3. Source ROS in every terminal where you run the GUI:
   ```bash
   source /opt/ros/$ROS_DISTRO/setup.bash
   ```
4. Switch the backend in `config/settings.py`:
   ```python
   ROBOT_BACKEND = "ros"
   ```
5. Run the GUI as usual:
   ```bash
   python main.py
   ```

### Testing the publisher without the GUI

A standalone test script is included so you can verify ROS publishing
independently of the GUI / camera:

**Terminal 1** — watch the topic:
```bash
source /opt/ros/$ROS_DISTRO/setup.bash
ros2 topic echo /robogambit/move
```

**Terminal 2** — publish manually:
```bash
cd robogambit
source venv/bin/activate
source /opt/ros/$ROS_DISTRO/setup.bash
python -m scripts.test_publisher
# Type moves: e2e4, g8f6, etc.
```

You should see each move appear in Terminal 1.

### Optional future extension

If we ever need to receive feedback from the hardware ("move executed",
"emergency stop", etc.), we'd add a subscriber alongside the publisher and
spin a `SingleThreadedExecutor` in a `QThread`. The current publish-only
design keeps it simple.

---

## 🛠 Configuration knobs

All in `config/settings.py`:

| Variable           | What it controls                                            |
|--------------------|-------------------------------------------------------------|
| `CAMERA_INDEX`     | Which `/dev/videoN` to open                                 |
| `STOCKFISH_PATH`   | Path to Stockfish binary (auto-detected by default)         |
| `MOVE_THRESHOLD`   | Pixel diff threshold (lower = more sensitive)               |
| `MIN_CONTOUR_AREA` | Min contour size to be a real change (filters shadow noise) |
| `PREVIEW_FPS`      | Live camera feed frame rate                                 |
| `ROBOT_BACKEND`    | `"fake"` or `"ros"`                                         |

---

## 🐛 Troubleshooting

**"Calibration file not found"**
Run calibration first: `python -m vision.calibrate`.

**"Could not open camera at index N"**
Check `ls /dev/video*` and update `CAMERA_INDEX` in `config/settings.py`.

**Moves detected wrongly**
- Make sure lighting is consistent (no moving shadows from the player's hand).
- Make sure the calibration is precise — re-run it if the camera was bumped.
- Try lowering `MIN_CONTOUR_AREA` if small pieces are missed, or raising
  `MOVE_THRESHOLD` if you get false positives from light flicker.

**Stockfish not found**
`sudo apt install stockfish` should put it at `/usr/games/stockfish`. If you
built from source, set `STOCKFISH_PATH` manually in `config/settings.py`.

---

## 👥 Team

- **Vision / AI / GUI** — this repo
- **Hardware / Mechanical** — robotic arm design and assembly
- **ROS / Control** — robot motion planning, fills in `robot/ros_robot.py`

---

## 📜 License

TBD — discuss with the team before public release.
