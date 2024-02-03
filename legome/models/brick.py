from __future__ import annotations

from typing import Tuple


class Brick:
    def __init__(self, id: int = -1, color: Tuple[int, int, int] = (0, 0, 0)):
        """
        Initialize a Brick object with an ID and a color.

        :param brick_id: Unique identifier for the brick.
        :param color: RGB tuple representing the color of the brick.
        """
        self.id = id
        self.color = color

    def __repr__(self):
        """
        Return a string representation of the Brick.

        :return: String representation of the Brick.
        """
        return f"Brick: {self.id} {self.color}"

    def __hash__(self):
        """
        Return the hash value of the Brick.

        :return: Hash value of the Brick.
        """
        return self.id

    def __copy__(self):
        """
        Create a shallow copy of the Brick.

        :return: Shallow copy of the Brick.
        """
        brick = Brick()
        brick.id, brick.color = self.id, self.color
        return brick

    def set_from(self, other: Brick):
        self.id = other.id
        self.color = other.color
