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

The `robot/` package is designed for clean handoff to the hardware team:

1. The hardware team fills in `robot/ros_robot.py` with their ROS publisher
   logic. The `send_move()` method receives a dict with `from`, `to`, `uci`,
   and `captured_piece` — all the info needed to plan a pick-and-place.
2. They publish to a topic (suggested: `/robogambit/robot_command`).
3. Their robot node subscribes and executes.
4. To activate ROS mode, change one line in `config/settings.py`:
   ```python
   ROBOT_BACKEND = "ros"   # was "fake"
   ```

For full ROS integration, the vision worker can also be wrapped in a ROS node
that publishes detected moves on `/robogambit/detected_move`. The GUI would
subscribe to that topic instead of receiving Qt signals directly. This is
straightforward because the move-detection logic in `move_detector.py` is
already decoupled from Qt.

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
