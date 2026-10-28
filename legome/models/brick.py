from __future__ import annotations

from dataclasses import dataclass

RGB = tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class Brick:
    """A single 1x1 LEGO plate: a palette index and the colour it renders as.

    Bricks are immutable so that one instance per palette colour can be shared
    across a whole canvas instead of copied per pixel.
    """

    id: int = -1
    color: RGB = (0, 0, 0)

    def __repr__(self) -> str:
        return f"Brick: {self.id} {self.color}"
