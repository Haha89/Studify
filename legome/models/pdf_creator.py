from typing import Dict, List

import numpy as np
import pandas as pd

from legome.models.canvas import Canvas
from legome.models.static import DIM_BASE


class PdfCreator:

    def __init__(self, canvas: Canvas):
        self.canvas = canvas

    def get_bases(self) -> List[Canvas]:
        arr, bases = np.array(self.canvas.bricks), []
        for r in range(arr.shape[0] // DIM_BASE):
            for c in range(arr.shape[1] // DIM_BASE):
                bases.append(Canvas(1, arr[DIM_BASE * r:DIM_BASE * (r + 1), DIM_BASE * c:DIM_BASE * (c + 1)].tolist(),
                                    name=f"{self.canvas.source} - Base Row{r + 1}:Col{c + 1}"))
        return bases

    def get_bases_maps_and_repartition(self) -> List[Dict[str, str]]:
        return [{"name": c.name, "table": c.to_html(), "distribution": c.get_distribution()} for c in self.get_bases()]

    def generate_html(self):
        from jinja2 import Environment, FileSystemLoader
        from os.path import dirname, abspath, join
        base_path = dirname(dirname(abspath(__file__)))
        env = Environment(loader=FileSystemLoader(join(base_path, "export")))
        template = env.get_template("template.html")
        template_vars = {"title": f"LegoMe: {self.canvas.source}",
                         "bases": self.get_bases_maps_and_repartition(),
                         "colors": pd.DataFrame(
                             [{"color": k, "RGB": v} for k, v in self.canvas.store.COLORS.items()]).to_html()}
        html_out = template.render(template_vars)
        filename = join(dirname(base_path), "output", f"{self.canvas.source}.html")
        with open(filename, "wb+") as f:
            f.write(bytes(html_out, 'utf-8'))
        import webbrowser, os
        webbrowser.open(os.path.abspath(filename), new=2)
