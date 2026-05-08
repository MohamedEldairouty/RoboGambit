"""
Robot backend abstraction.

GUI talks to this interface only. Concrete implementations:
  - FakeRobot: prints commands to console (for development without hardware)
  - ROSRobot:  publishes commands to a ROS topic (for real hardware)

Switch via config.settings.ROBOT_BACKEND.
"""
from abc import ABC, abstractmethod


class RobotInterface(ABC):
    """All robot backends must implement this."""

    @abstractmethod
    def send_move(self, move_data: dict) -> None:
        """
        Send a move command to the robot.

        move_data keys:
          uci: str            e.g. "e2e4"
          from: str           e.g. "e2"
          to: str             e.g. "e4"
          captured_piece:     chess.Piece or None
          fen: str            board FEN after the move (optional)
        """
        ...

    @abstractmethod
    def is_ready(self) -> bool:
        """True if the robot is connected and ready to accept moves."""
        ...

    def close(self) -> None:
        """Optional cleanup. Default: no-op."""
        pass


def get_robot_backend() -> RobotInterface:
    """Factory: returns the backend selected in config."""
    from config.settings import ROBOT_BACKEND

    if ROBOT_BACKEND == "ros":
        from robot.ros_robot import ROSRobot
        return ROSRobot()
    else:
        from robot.fake_robot import FakeRobot
        return FakeRobot()
