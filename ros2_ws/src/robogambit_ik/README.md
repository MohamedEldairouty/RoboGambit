# robogambit_ik

The **IK translator** node for RoboGambit. This is "Node 2" in the pipeline.

```
GUI publishes → /robogambit/move ("e2e4")
                       ↓
              this package (ik_node)
                       ↓
              /nano_serial ("90,45,120,60,30")
                       ↓
              existing serial bridge
                       ↓
              Arduino → servos
```

## What it does

Subscribes to `/robogambit/move` (UCI chess move strings from the GUI),
computes a sequence of pick-and-place waypoints, and publishes each waypoint
as servo angles to `/nano_serial`. Handles captures, castling, and promotion.

## Approach: lookup table, no math

Rather than computing inverse kinematics from joint geometry, this package
uses a **calibrated lookup table**: you manually walk the arm to each chess
square and record the servo angles. This trades calibration time
(~30-60 minutes, once) for zero math, zero modeling errors, and 100%
accuracy within the limits of your servos.

## Setup

### Build the package

```bash
cd ~/Downloads/robogambit/ros2_ws
colcon build --packages-select robogambit_ik
source install/setup.bash
```

### Calibrate the arm (one-time, before first use)

The calibration tool publishes to `/nano_serial`, so you need:
1. The hardware team's existing serial bridge node running
2. The Arduino connected and powered
3. The robot arm in a safe starting position

Then run:

```bash
ros2 run robogambit_ik calibrate_arm
```

Use the keyboard to move the servos in real time:

| Key | Action |
|---|---|
| `1`/`q` | Servo 0 +/- |
| `2`/`w` | Servo 1 +/- |
| `3`/`e` | Servo 2 +/- |
| `4`/`r` | Servo 3 +/- |
| `5`/`t` | Gripper +/- |
| `h` | Save current pose as **HOVER** for the current square |
| `p` | Save as **PICK** and advance to next square |
| `n` | Skip to next square |
| `b` | Back to previous square |
| `R` | Save as **REST** position (parking) |
| `G` | Save current gripper angle as **OPEN** |
| `C` | Save current gripper angle as **CLOSED** |
| `F` | **Finish** and write `arm_config.json` |
| `Q` | Quit without saving |

**Recommended order:**
1. Find a safe REST position (arm folded up, well clear of the board) → press `R`
2. Adjust gripper, press `G` for OPEN, then close it and press `C` for CLOSED
3. Walk through all 64 squares: hover above each, press `h`; lower to pick height, press `p`
4. Walk through 16 graveyard slots (places where captured pieces go off-board)
5. Walk through 4 spare-piece slots (where extra queens etc. live for promotion)
6. Press `F` to finish and save

The output goes to:
```
ros2_ws/src/robogambit_ik/robogambit_ik/arm_config.json
```

You can edit this JSON by hand later if any angles need tweaking.

### Run the IK node

Once calibration is done:

```bash
ros2 run robogambit_ik ik_node
```

The node will start, send the arm to the rest position, and then wait for
moves on `/robogambit/move`.

## Full pipeline (4 terminals)

After calibration is complete, a real chess game uses 4 terminals:

```bash
# Terminal 1: existing serial bridge (you already have this)
source /opt/ros/jazzy/setup.bash
ros2 run <existing_serial_pkg> <existing_serial_node>

# Terminal 2: this IK translator
source /opt/ros/jazzy/setup.bash
source ~/Downloads/robogambit/ros2_ws/install/setup.bash
ros2 run robogambit_ik ik_node

# Terminal 3: the GUI (with ROBOT_BACKEND="ros" in config/settings.py)
cd ~/Downloads/robogambit
source venv/bin/activate
source /opt/ros/jazzy/setup.bash
python main.py

# Terminal 4 (optional): watch the move stream
source /opt/ros/jazzy/setup.bash
ros2 topic echo /robogambit/move
```

## Tunable parameters

In `ik_node.py`:

- `WAYPOINT_DELAY = 1.2` — seconds between waypoints. Increase if servos
  miss positions; decrease for faster gameplay.

In `arm_config.json` (after calibration):

- Edit any individual angle if you want to tweak a specific square without
  re-running calibration.

## Special move handling

| Move type | Behavior |
|---|---|
| Standard | hover → pick → close → lift → hover dest → place → open → retreat |
| Capture (e.g. `dxe5`) | First removes captured piece on `e5` to graveyard, *then* moves attacker |
| Castling (`e1g1` etc.) | Moves king first, then rook |
| Promotion (`e7e8q`) | Moves pawn to `e8`, removes pawn to graveyard, places spare queen on `e8` |

## Troubleshooting

**"Cannot load arm config"** — `arm_config.json` is missing or empty.
Run `calibrate_arm` first.

**"Square X not calibrated"** — That square didn't get a HOVER and PICK
recorded. Re-run `calibrate_arm`, navigate to that square (use `n`/`b`),
record both poses.

**Arm jerky / overshoots / misses positions** — Increase `WAYPOINT_DELAY`
in `ik_node.py`. Servos need time to reach commanded positions.

**Capture handling wrong** — The IK node tracks the chess board internally
to know what's a capture. If the GUI publishes a move that doesn't match
expected board state, captures may fail. Make sure no one resets only one
side: if the GUI starts a new game, restart `ik_node` too.

**Promotion fails** — Make sure you calibrated `spare_q`, `spare_r`,
`spare_b`, `spare_n` slots. The physical spare pieces must be present at
those slots when promotion happens.

## Future improvements (out of scope for now)

- Subscribe to a `/robogambit/reset` topic so the GUI can tell the IK node
  to reset its internal board state when a new game starts.
- Publish `/robogambit/move_complete` so the GUI knows when the arm is done.
- Add safety zones / collision checks so the arm doesn't crash into the camera.
