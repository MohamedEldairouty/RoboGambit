"""
ROS 2 robot backend.

Publishes UCI chess moves (e.g. "e2e4") as std_msgs/String to the topic
defined in config.settings.ROS_MOVE_TOPIC.

The hardware team's ROS node subscribes to this topic, performs inverse
kinematics, and forwards servo angles to the Arduino over serial.

DESIGN NOTE — why no rclpy.spin():
-----------------------------------
rclpy.spin() blocks the calling thread, which would freeze our PySide6 GUI.
Since we ONLY publish (we don't receive any ROS messages), we don't need
to spin at all. Publishing is non-blocking in rclpy. We just init the node
once and call publish() whenever the GUI sends a move.

If we ever need to subscribe to feedback topics (e.g. "robot move complete"),
we'd add a SingleThreadedExecutor and spin it in a small QThread. For now,
publish-only is simpler and works.

REQUIREMENTS:
    - ROS 2 (Humble, Iron, or Jazzy) installed
    - rclpy importable in the venv
    - On Ubuntu, after sourcing /opt/ros/<distro>/setup.bash, rclpy becomes
      available system-wide. To use it inside a venv, create the venv with
      --system-site-packages so it can see rclpy.

Activate by setting ROBOT_BACKEND = "ros" in config/settings.py.
"""
from robot.robot_interface import RobotInterface

# rclpy is only imported lazily when ROSRobot is actually used.
# This way, users without ROS installed can still run the GUI in fake mode
# without crashing on the import line.
_RCLPY_AVAILABLE = None
_rclpy = None
_String = None
_Node = None


def _try_import_rclpy():
    """Lazy import. Returns True if rclpy and std_msgs are importable."""
    global _RCLPY_AVAILABLE, _rclpy, _String, _Node
    if _RCLPY_AVAILABLE is not None:
        return _RCLPY_AVAILABLE
    try:
        import rclpy as rclpy_mod
        from rclpy.node import Node
        from std_msgs.msg import String
        _rclpy = rclpy_mod
        _Node = Node
        _String = String
        _RCLPY_AVAILABLE = True
    except ImportError as e:
        print(f"[ROS ROBOT] rclpy/std_msgs not importable: {e}")
        print("[ROS ROBOT] Install ROS 2 and source /opt/ros/<distro>/setup.bash, "
              "or use ROBOT_BACKEND='fake' in config/settings.py")
        _RCLPY_AVAILABLE = False
    return _RCLPY_AVAILABLE


class ROSRobot(RobotInterface):
    """Publishes chess moves to a ROS 2 topic. No subscriptions needed."""

    def __init__(self):
        from config.settings import ROS_MOVE_TOPIC, ROS_NODE_NAME
        self._topic = ROS_MOVE_TOPIC
        self._node_name = ROS_NODE_NAME
        self._node = None
        self._publisher = None
        self._ready = False

        if not _try_import_rclpy():
            print("[ROS ROBOT] Disabled — falling back to no-op mode")
            return

        try:
            # Initialize ROS context if not already initialized.
            # (Could already be initialized if another part of the app uses ROS.)
            if not _rclpy.ok():
                _rclpy.init()
            self._node = _Node(self._node_name)
            self._publisher = self._node.create_publisher(_String, self._topic, 10)
            self._ready = True
            print(f"[ROS ROBOT] Publishing on {self._topic} as node '{self._node_name}'")
        except Exception as e:
            print(f"[ROS ROBOT] Init failed: {e}")
            self._ready = False

    def send_move(self, move_data: dict) -> None:
        """Publish UCI move to ROS topic. Format: 'e2e4' for std move,
        'e7e8q' for promotion."""
        if not self._ready:
            print(f"[ROS ROBOT] (not ready) would publish: {move_data['uci']}")
            return

        try:
            msg = _String()
            msg.data = move_data["uci"]
            self._publisher.publish(msg)
            self._node.get_logger().info(f"Published move: {msg.data}")
        except Exception as e:
            print(f"[ROS ROBOT] Publish failed: {e}")

    def is_ready(self) -> bool:
        return self._ready

    def close(self) -> None:
        """Cleanly tear down the ROS node."""
        if self._node is not None:
            try:
                self._node.destroy_node()
            except Exception:
                pass
            self._node = None
        # Note: we don't call rclpy.shutdown() here in case other components
        # still need ROS. Call it only on full app exit if needed.
        self._ready = False


def shutdown_ros():
    """Call once on application exit to fully shut down ROS context."""
    if _RCLPY_AVAILABLE and _rclpy is not None:
        try:
            if _rclpy.ok():
                _rclpy.shutdown()
        except Exception:
            pass