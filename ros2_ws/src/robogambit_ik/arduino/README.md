# RoboGambit Arduino Firmware

Firmware that runs on the **Arduino Nano** controlling the 5-servo robotic arm.

## What it does

- Listens on USB serial (**9600 baud**) for one servo command at a time
- Format: `"Sn ANGLE\n"` (e.g. `"S1 155\n"` to set servo 1 to 155°)
- Performs **smooth interpolated movement** to the target angle (configurable speed)
- Built-in **per-servo angle limits** prevent damage from out-of-range commands

## Wiring

| Servo  | Arduino Pin | Function       | Safe Range  |
|--------|-------------|----------------|-------------|
| S1     | D3          | Gripper        | 130 – 180   |
| S2     | D5          | Shoulder       |  40 – 180   |
| S3     | D11         | Elbow          |   0 –  60   |
| S4     | D9          | Wrist          |  50 – 120   |
| S5     | D10         | Base rotation  |   0 – 180   |

⚠️ **Power:** Servos require an external 5V power supply (≥2A).
The Arduino's USB power is NOT enough — servos will brownout/reset
the board if powered from USB. Connect:
- External 5V → servo VCC rail
- External GND → servo GND rail AND Arduino GND (common ground)
- Arduino USB → laptop (data + Arduino power only)

## How to flash

1. Open `chess_arm_controller/chess_arm_controller.ino` in Arduino IDE
2. Tools → Board → Arduino Nano
3. Tools → Processor → ATmega328P (Old Bootloader)  *(or "ATmega328P" if newer Nano)*
4. Tools → Port → select the Nano's `/dev/ttyUSB*` port
5. Click Upload (→ arrow icon)

After flashing, open Serial Monitor (9600 baud) — it should print:
```
──── Servo Status ────
  S1: 180°  [130-180]
  S2: 110°  [40-180]
  S3: 30°   [0-60]
  S4: 85°   [50-120]
  S5: 90°   [0-180]
  Speed: 15 ms/deg
──────────────────────
Commands:  S1 155 | S2 80 | SPEED 20
Ready.
```

## Command reference

| Command | Effect |
|---|---|
| `S1 155` | Move servo 1 to 155° |
| `S2 80`  | Move servo 2 to 80° |
| `SPEED 20` | Set movement speed (1-100 ms per degree, lower = faster) |
| `STATUS` | Print current positions and limits |

⚠️ Always send `\n` (newline) at the end of each command.

## Testing without the ROS pipeline

Open Serial Monitor (9600 baud, Newline line ending):
```
S1 155       → gripper closes to 155°
S5 90        → base rotates to 90°
SPEED 30     → makes all subsequent moves slower
STATUS       → prints current positions
```

## Integration with ROS

The `ik_node` in this package translates chess moves into a sequence of
single-servo commands and publishes them to `/nano_serial`. The
`serial_node` opens the Arduino's USB port and forwards each command
line directly. See the parent `README.md` for the full pipeline.
