"""
RoboGambit Serial Bridge Node.

Subscribes to:
    /nano_serial    std_msgs/String   Arduino command strings like "S1 155" or "SPEED 20"

Forwards each message line over USB serial (9600 baud) to the Arduino.

Matches the protocol expected by arduino/chess_arm_controller:
  - Each command is one line, terminated with \n
  - Examples: "S1 155", "S2 80", "SPEED 20", "STATUS"
  - The Arduino interpolates smoothly and replies "Done." per command

Auto-detects the Arduino by USB VID/PID. Adjust NANO_VID/NANO_PID if your
Arduino clone shows up under a different identifier.
"""
import os
import time
from typing import Optional

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

import serial
from serial.tools.list_ports import comports


# === Configuration ===
BAUDRATE = 9600
RETRY_DELAY = 2
TIMEOUT_DELAY = 0.2  # short timeout — we actively poll for Done. response

# USB identifiers for the Arduino Nano (CH340 clone)
# Use `lsusb` or `udevadm info` to find your board's actual values.
NANO_PID = 29987
NANO_VID = 6790


class ArduinoWriteException(Exception):
    pass


class Arduino:
    def __init__(self, logger=None):
        self._connection: Optional[serial.Serial] = None
        self._logger = logger

    def _log(self, msg: str):
        if self._logger is not None:
            self._logger.info(msg)
        else:
            print(msg)

    def _find_arduino_port(self) -> Optional[str]:
        ports = comports()
        for port in ports:
            if port.pid == NANO_PID and port.vid == NANO_VID:
                return port.device
        # Fallback: Raspberry Pi GPIO UART
        if os.path.isfile("/dev/ttyAMA0"):
            return "/dev/ttyAMA0"
        # Final fallback: first /dev/ttyUSB* we find
        for port in ports:
            if "ttyUSB" in (port.device or ""):
                return port.device
        return None

    def connect(self):
        """Block until we successfully open the serial port."""
        while True:
            try:
                port = self._find_arduino_port()
                if port is None:
                    self._log("Arduino not found, retrying...")
                    time.sleep(RETRY_DELAY)
                    continue
                connection = serial.Serial(
                    port=port,
                    baudrate=BAUDRATE,
                    timeout=TIMEOUT_DELAY,
                )
                if hasattr(connection, "set_low_latency_mode"):
                    try:
                        connection.set_low_latency_mode(True)
                    except Exception:
                        pass
                self._connection = connection
                self._log(f"Arduino connected on {port} @ {BAUDRATE} baud")
                # Arduino resets on serial open; wait for it to finish booting
                # and drain the boot banner so it doesn't get mixed with the
                # replies of the first real commands.
                time.sleep(2.5)
                drained = 0
                while connection.in_waiting:
                    line = connection.readline().decode(errors="ignore").strip()
                    if line:
                        drained += 1
                if drained:
                    self._log(f"Drained {drained} lines of boot banner")
                return
            except Exception as e:
                self._log(f"Connection error: {e}, retrying...")
                time.sleep(RETRY_DELAY)

    def close(self):
        if self._connection is None:
            return
        try:
            self._connection.close()
        except Exception:
            pass
        self._connection = None

    def write(self, data: str):
        if self._connection is None:
            return
        try:
            # Ensure exactly one newline at the end
            line = data.strip() + "\n"
            self._connection.write(line.encode("ascii", errors="ignore"))
            self._connection.flush()
        except Exception:
            raise ArduinoWriteException()

    def read(self) -> Optional[str]:
        if self._connection is None:
            return None
        try:
            return self._connection.readline().decode("ascii", errors="ignore").strip()
        except Exception:
            return None


class SerialBridgeNode(Node):
    def __init__(self):
        super().__init__("robogambit_serial")

        self.arduino = Arduino(logger=self.get_logger())
        self.get_logger().info("Connecting to Arduino...")
        self.arduino.connect()
        self.get_logger().info("Arduino ready.")

        # Use a LARGE queue depth — the Arduino takes ~0.5-2s per command due
        # to smooth-movement, while the IK node fires commands every ~50ms.
        # Without a deep queue, ROS drops the oldest messages and the arm
        # skips servo positions.
        self.subscription = self.create_subscription(
            String, "/nano_serial", self.on_command, 200,
        )

    def on_command(self, msg: String):
        """Process a single Arduino command synchronously.

        Each command is fully completed (we wait for the Arduino's "Done."
        reply) before this callback returns. This means the next queued
        ROS message can't be processed until this one finishes — which is
        exactly what we want so commands aren't reordered or lost.
        """
        cmd = msg.data.strip()
        self.get_logger().info(f"Sending: {cmd}")
        try:
            self.arduino.write(cmd)
        except ArduinoWriteException:
            self.get_logger().error("Failed to write to Arduino")
            return

        # Wait for the Arduino's "Done." line (or timeout). The Arduino
        # always emits one or two intermediate lines like "S1: 180 -> 168"
        # followed by "Done." — we stop reading as soon as we see "Done."
        deadline = time.monotonic() + 5.0  # max 5s per command
        while time.monotonic() < deadline:
            line = self.arduino.read()
            if line is None or line == "":
                continue
            self.get_logger().info(f"  reply: {line}")
            if line.startswith("Done") or line.startswith("Error") \
                    or line.startswith("Unknown") or line.startswith("Speed"):
                return
        self.get_logger().warn(f"Timeout waiting for Done. on command: {cmd}")


def main(args=None):
    rclpy.init(args=args)
    node = SerialBridgeNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.arduino.close()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
