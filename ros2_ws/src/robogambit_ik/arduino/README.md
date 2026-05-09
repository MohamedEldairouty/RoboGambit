# RoboGambit Arduino Firmware

Firmware that runs on the **Arduino Nano** controlling the 5-servo robotic arm.

## What it does

- Listens on USB serial (115200 baud) for comma-separated angle strings
- Format: `"angle1,angle2,angle3,angle4,angle5\n"` (each 0–180 degrees)
- Drives 5 servos to those positions

## Wiring

| Servo  | Arduino Pin | Function          |
|--------|-------------|-------------------|
| Servo 0 | D3         | Joint 0 (base)    |
| Servo 1 | D5         | Joint 1           |
| Servo 2 | D6         | Joint 2           |
| Servo 3 | D9         | Joint 3           |
| Servo 4 | D10        | Gripper           |

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

After flashing, the serial monitor (115200 baud) should print "Ready" on boot.

## Testing without the ROS pipeline

Open the Serial Monitor (Tools → Serial Monitor, 115200 baud, Newline ending),
type `90,90,90,90,90` and press Enter. All 5 servos should snap to mid-position.

Try `45,90,90,90,90` — only servo 0 should move.

## Integration with ROS

The `serial_node` in this same package opens this Arduino over USB
(auto-detected by VID/PID), and forwards strings published to
`/nano_serial` directly to the Arduino. See the parent `README.md`
for the full pipeline.
