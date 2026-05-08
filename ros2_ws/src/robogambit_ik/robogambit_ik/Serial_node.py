import time
from typing import Any, Optional
import serial
from serial.tools.list_ports import comports
import os

import rclpy
from rclpy.node import Node

from std_msgs.msg import String


BAUDRATE = 115200
RETRY_DELAY = 2
TIMEOUT_DELAY = 2
NANO_PID = 29987
NANO_VID = 6790



class ArduinoWriteException(Exception): ...


class Arduino:
    def __init__(self):
        self.__connection = None

    def __find_arduino_serial_port(self) -> Optional[str]:
        ports = comports()
        for port in ports:
            if port.pid == NANO_PID and port.vid == NANO_VID:
                return port.device

        if os.path.isfile("/dev/ttyAMA0"):
            return "/dev/ttyAMA0"

        return None

    def __create_serial_connection(self) -> serial.Serial:
        while True:
            try:
                port = self.__find_arduino_serial_port()
                if port is None:
                    continue

                connection = serial.Serial(
                    port=port,
                    baudrate=BAUDRATE,
                    timeout=TIMEOUT_DELAY,
                )

                if hasattr(connection, "set_low_latency_mode"):
                    connection.set_low_latency_mode(True)


                return connection
            except Exception as e:
                time.sleep(RETRY_DELAY)
                continue

    def connect(self):
        self.__connection = self.__create_serial_connection()

    def close_connection(self):
        if self.__connection is None:
            return

        self.__connection.close()

    def write(self, data: Any):
        if self.__connection is None:
            return

        try:
            self.__connection.write(f"{data}\n".encode())
        except Exception as e:
            raise ArduinoWriteException()

    def read(self) -> Optional[str]:
        if self.__connection is None:
            return None

        try:
            line = self.__connection.readline().decode("ascii").strip()
        except Exception as e:
            return None

        return line

    def keep_alive(self):
        self.write(27)



class MinimalSubscriber(Node):

    def __init__(self):
        super().__init__('minimal_subscriber')

        self.arduino = Arduino()
        self.get_logger().info("Connecting to Arduino...")
        self.arduino.connect()
        self.get_logger().info("Arduino connected.")

        self.subscription = self.create_subscription(
            String,
            '/nano_serial',
            self.listener_callback,
            10
        )

    def listener_callback(self, msg):
        self.get_logger().info(f'I heard: "{msg.data}"')

        try:
            self.arduino.write(msg.data)
            self.get_logger().info(f'Sent to Arduino: {msg.data}')
        except ArduinoWriteException:
            self.get_logger().error("Failed to write to Arduino")

def main(args=None):
    rclpy.init(args=args)

    node = MinimalSubscriber()

    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.arduino.close_connection()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()