"""
ROS robot backend: publishes move commands to a ROS topic.

NOTE: This is a stub. The hardware/ROS team should fill in the
actual rospy/rclpy code. Suggested topic structure:

    Topic: /robogambit/robot_command
    Msg:   custom message with fields {from_sq, to_sq, uci, capture_flag}

Or if using std_msgs/String, just publish JSON-serialized move_data.

Activate by setting ROBOT_BACKEND = "ros" in config/settings.py.
"""
from robot.robot_interface import RobotInterface


class ROSRobot(RobotInterface):
    def __init__(self):
        # TODO (hardware team): initialize ROS node + publisher here
        # Example (rclpy / ROS 2):
        #   import rclpy
        #   from rclpy.node import Node
        #   from std_msgs.msg import String
        #   rclpy.init()
        #   self.node = Node("robogambit_gui")
        #   self.publisher = self.node.create_publisher(
        #       String, "/robogambit/robot_command", 10
        #   )
        #
        # Example (rospy / ROS 1):
        #   import rospy
        #   from std_msgs.msg import String
        #   rospy.init_node("robogambit_gui", anonymous=True)
        #   self.publisher = rospy.Publisher(
        #       "/robogambit/robot_command", String, queue_size=10
        #   )

        self._ready = False
        print("[ROS ROBOT] Stub initialized — fill in ros_robot.py to enable")

    def send_move(self, move_data: dict) -> None:
        # TODO (hardware team): publish move_data to the ROS topic
        # import json
        # from std_msgs.msg import String
        # msg = String()
        # msg.data = json.dumps({
        #     "from": move_data["from"],
        #     "to":   move_data["to"],
        #     "uci":  move_data["uci"],
        #     "capture": move_data.get("captured_piece") is not None,
        # })
        # self.publisher.publish(msg)
        print(f"[ROS ROBOT] (stub) would publish: {move_data['uci']}")

    def is_ready(self) -> bool:
        return self._ready

    def close(self) -> None:
        # TODO (hardware team): shut down the ROS node cleanly
        # rclpy.shutdown()  # ROS 2
        # or just let rospy handle it on process exit (ROS 1)
        pass
