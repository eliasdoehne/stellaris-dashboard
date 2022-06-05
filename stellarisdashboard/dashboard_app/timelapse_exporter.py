import datetime
import io
import logging
from collections import defaultdict

import matplotlib
import matplotlib.pyplot as plt
import networkx as nx
import numpy as np
import tqdm
from PIL import Image
from matplotlib.collections import PatchCollection

from stellarisdashboard import config, datamodel
from stellarisdashboard.dashboard_app.visualization_data import (
    GalaxyMapData,
    get_color_vals,
)

matplotlib.use("Agg")
logger = logging.getLogger(__name__)


class TimelapseExporter:
    dpi = 120
    width = 16
    height = 9
    save_frames = True

    _instances = {}

    @classmethod
    def from_game_id(cls, game_id) -> "TimelapseExporter":
        if game_id not in cls._instances:
            cls._instances[game_id] = cls(game_id)
        return cls._instances[game_id]

    def __init__(self, game_id):
        self.game_id = game_id
        self.galaxy_map_data = GalaxyMapData(game_id=game_id)
        self.galaxy_map_data.initialize_galaxy_graph()
        self._ts = datetime.datetime.now()

    def create_timelapse(self, start_date: int, end_date: int, step_days: int, tl_duration: int):
        self._ts = datetime.datetime.now()
        output_file = self.output_file()
        logger.info(f"Exporting galaxy timelapse to {output_file}")

        frames = (self.draw_frame(day) for day in tqdm.tqdm(range(start_date, end_date, step_days)))
        first_frame = next(frames)
        first_frame.save(
            output_file, save_all=True, append_images=frames, duration=tl_duration, loop=0
        )

    def output_file(self) -> str:
        out_dir = config.CONFIG.base_output_path / "galaxy-timelapse"
        out_dir.mkdir(parents=True, exist_ok=True)
        return str((self._output_dir() / f"{self._timelapse_id()}.gif").absolute())

    def _output_dir(self):
        out_dir = config.CONFIG.base_output_path / "galaxy-timelapse"
        out_dir.mkdir(parents=True, exist_ok=True)
        return out_dir

    def _timelapse_id(self):
        ts = self._ts.strftime("%Y-%m-%d-%H%M%S")
        return f"{self.game_id}-{ts}"

    def rgb(self, name: str):
        r, g, b = get_color_vals(name)
        return r / 255.0, g / 255.0, b / 255.0

    def draw_frame(self, day):
        galaxy = self.galaxy_map_data.get_graph_for_date(day)

        fig = plt.figure(figsize=(self.width, self.height))
        ax = fig.add_subplot(111)

        nx.draw(
            galaxy,
            ax=ax,
            pos={node: galaxy.nodes[node]["pos"] for node in galaxy.nodes},
            node_color=[self.rgb(galaxy.nodes[node]["country"]) for node in galaxy.nodes],
            edge_color=[self.rgb(galaxy.edges[e]["country"]) for e in galaxy.edges],
            with_labels=False,
            font_weight="bold",
            node_size=10,
            width=0.5,
        )
        fig.set_facecolor("k")

        self._draw_systems(ax, galaxy)

        ax.text(
            0.05,
            0.05,
            datamodel.days_to_date(day),
            color="white",
            fontfamily="monospace",
            transform=ax.transAxes,
        )

        if self.save_frames:
            out_path = (
                config.CONFIG.base_output_path / f"galaxy-timelapse/frames/{self._timelapse_id()}"
            )
            out_path.mkdir(parents=True, exist_ok=True)
            fig.savefig(out_path / f"{datamodel.days_to_date(day)}.png", format="png", dpi=self.dpi)
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=self.dpi)
        plt.close(fig)
        buf.seek(0)
        return Image.open(buf)

    def _draw_systems(self, ax, galaxy):
        systems_by_country = defaultdict(lambda: defaultdict(int))
        polygon_patches = []
        for node in galaxy:
            country = galaxy.nodes[node]["country"]
            nodecolor = self.rgb(country) if country != GalaxyMapData.UNCLAIMED else (0, 0, 0)

            if country != GalaxyMapData.UNCLAIMED:
                systems_by_country[country]["x"] += galaxy.nodes[node]["pos"][0]
                systems_by_country[country]["y"] += galaxy.nodes[node]["pos"][1]
                systems_by_country[country]["count"] += 1
                systems_by_country[country]["color"] = nodecolor

            polygon_patches.append(
                matplotlib.patches.Polygon(
                    np.array(list(galaxy.nodes[node]["shape"])).T,
                    fill=True,
                    facecolor=nodecolor,
                    alpha=0.2,
                    linewidth=0,
                )
            )
        p = PatchCollection(polygon_patches, match_original=True)
        ax.add_collection(p)
        self._draw_country_names(ax, systems_by_country)

    def _draw_country_names(self, ax, systems_by_country):
        for country, avg_pos in systems_by_country.items():
            x = avg_pos["x"] / avg_pos["count"]
            position = avg_pos["y"] / avg_pos["count"]
            ax.text(
                x, position, country, color=avg_pos["color"], size="x-small", ha="center",
            )
