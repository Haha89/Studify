from typing import Dict, List

import cv2
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from legome.models.brick import Brick
from legome.models.brickstore import BrickStore
from legome.models.static import DIM_BASE


class Canvas:
    def __init__(self, size: int, bricks: List[List[Brick]] = None, name: str = None):
        """
        Initialize a Canvas object with a specified size.

        :param size: Size of the canvas.
        :param store: BrickStore containing available brick colors.
        """
        self.size = size * DIM_BASE
        self.bricks: List[List[Brick]] = bricks or []
        self.store = BrickStore()
        self.source = ""
        self.name = name

    def __repr__(self):
        return f"Canvas L{self.size} - Distrib: {self.get_distribution()}"

    def get_distribution(self) -> Dict[int, int]:
        """
        Get the distribution of bricks on the canvas.

        :return: A dictionary mapping Brick objects to their count on the canvas.
        """
        from legome.models.utils import get_distribution
        return get_distribution(self.bricks)

    def get_brick_ids(self) -> np.array:
        return np.vectorize(lambda x: x.id)(np.array(self.bricks))

    def get_cost(self) -> float:
        return (len(self.bricks) * len(self.bricks[0])) * (
                self.store.get_price_tile() + self.store.get_price_base() / (DIM_BASE * DIM_BASE))

    def plot_canvas(self) -> None:
        # Convert the bricks on the canvas to a NumPy array of pixel values and normalize
        pixels = np.array([[brick.color for brick in row] for row in self.bricks]) / 255
        self.plot(pixels)

    def to_html(self) -> str:
        from legome.models.utils import make_map
        return make_map(pd.DataFrame(self.get_brick_ids()))

    @staticmethod
    def plot(pixels):
        """
        Display the canvas using matplotlib.

        :param pixels: NumPy array representing the pixel values of the canvas.
        """
        plt.figure()
        plt.clf()
        plt.axis('off')
        plt.title("Canvas")
        plt.imshow(pixels)
        plt.show()

    def resize_img(self, src):
        width, height = len(src[0]), len(src)
        if width < height:
            height_resized = self.size // DIM_BASE * DIM_BASE
            width_resized = height_resized * width / height // DIM_BASE * DIM_BASE
        else:
            width_resized = self.size // DIM_BASE * DIM_BASE
            height_resized = width_resized * height / width // DIM_BASE * DIM_BASE
        return cv2.resize(src, (int(width_resized), int(height_resized)), interpolation=cv2.INTER_AREA)

    def process_image(self, path: str):
        """
        Process an image and update the canvas with the closest available brick colors.

        :param path: Path to the image file.
        """
        from pathlib import Path
        self.source = Path(path).stem
        # Read and resize the image
        src = cv2.cvtColor(cv2.imread(path), cv2.COLOR_BGR2RGB)
        resized = self.resize_img(src)

        # Extract available brick colors from the store
        available_bricks = self.store.available_bricks
        colors = np.array([brick.color for brick in available_bricks])

        # Calculate color differences between each pixel and available brick colors
        deltas = np.linalg.norm(colors[:, np.newaxis, np.newaxis, :] - resized, axis=3)
        # Find the index of the closest brick for each pixel
        closest_brick_indices = np.argmin(deltas, axis=0)

        # Update the canvas with the closest bricks
        for row in range(resized.shape[0]):
            brick_row = []
            for col in range(resized.shape[1]):
                index = closest_brick_indices[row, col]
                closest_brick = available_bricks[index]
                brick_row.append(Brick(closest_brick.id, closest_brick.color))
            self.bricks.append(brick_row)
