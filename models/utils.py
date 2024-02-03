from typing import Dict, List

import pandas as pd

from models.brick import Brick


def get_distribution(bricks: List[List[Brick]]) -> Dict[int, int]:
    """
    Get the distribution of bricks on the canvas.

    :return: A dictionary mapping Brick objects to their count on the canvas.
    """
    distribution = {}
    for row in bricks:
        for brick in row:
            distribution[brick.id] = distribution.get(brick.id, 0) + 1
    return distribution


def make_map(df: pd.DataFrame) -> str:
    dict_data = [df.to_dict(), df.to_dict('index')]

    html = '<table class="table table-borderless" style="text-align: center;"><thead><tr><th></th>'
    for key in dict_data[0].keys():
        html += f'<th style="height: 40px; width: 40px">{key + 1}</th>'

    html += '</tr></thead><tbody class="table-group-divider">'
    for key in dict_data[1].keys():
        html += f'<tr><th style="height: 40px; width: 40px">{key + 1}</th>'
        for subkey in dict_data[1][key]:
            html += f'<td class="{get_class(dict_data[1], key, subkey)}" style="height: 40px; width: 40px">{dict_data[1][key][subkey]}</td>'
    html += '</tr></tbody></table>'
    return html


def get_class(map, row, col):
    css_class = "p-0"
    if map.get(row - 1, {}).get(col, -1) != map[row][col]:
        css_class += " border-top"
    if map.get(row + 1, {}).get(col, -1) != map[row][col]:
        css_class += " border-bottom"
    if map.get(row, {}).get(col - 1, -1) != map[row][col]:
        css_class += " border-start"
    if map.get(row, {}).get(col + 1, -1) != map[row][col]:
        css_class += " border-end"
    return css_class
