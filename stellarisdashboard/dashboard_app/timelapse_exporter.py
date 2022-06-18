import datetime
import io
import logging
from collections import defaultdict
from typing import Tuple, Optional

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

    def __init__(self, game_id):
        self.game_id = game_id
        self.galaxy_map_data = GalaxyMapData(game_id=game_id)
        self.galaxy_map_data.initialize_galaxy_graph()
        self._ts = datetime.datetime.now()

    def create_timelapse(
        self,
        start_date: int,
        end_date: int,
        step_days: int,
        tl_duration: int,
        export_gif: bool,
        export_frames: bool,
        x_range: Optional[Tuple[float, float]] = None,
        y_range: Optional[Tuple[float, float]] = None,
    ):
        if not any([export_gif, export_frames]):
            logger.info("Nothing to do, cancelling timelapse")
            return
        self._ts = datetime.datetime.now()

        frames_path = (
            config.CONFIG.base_output_path / f"galaxy-timelapse/{self._timelapse_id()}/"
        )
        gif_path = self.output_file()

        if export_frames:
            logger.info(f"Exporting timelapse frames to {frames_path}")
        if export_gif:
            logger.info(f"Exporting timelapse gif to {gif_path}")

        export_days = self._day_list(start_date, end_date, step_days)
        frames = []
        for i, day in enumerate(tqdm.tqdm(export_days)):
            frame = self.draw_frame(day, x_range, y_range)
            if export_gif:
                frames.append(frame)
            if export_frames:
                frames_path.mkdir(parents=True, exist_ok=True)
                frame.save(
                    frames_path / f"{datamodel.days_to_date(day)}.png",
                    format="png",
                    dpi=(self.dpi, self.dpi),
                )
        if export_gif:
            frames[0].save(
                gif_path,
                save_all=True,
                append_images=frames[1:],
                duration=tl_duration,
                loop=0,
            )

    def _day_list(self, start_date, end_date, step_days):
        export_days = list(range(start_date, end_date, step_days))
        if export_days[-1] != end_date:
            export_days.append(end_date)
        return export_days

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

    def draw_frame(
        self,
        day: int,
        x_range: Optional[Tuple[float, float]],
        y_range: Optional[Tuple[float, float]],
    ) -> Image:
        galaxy = self.galaxy_map_data.get_graph_for_date(day)

        fig = plt.figure(figsize=(self.width, self.height))
        ax = fig.add_subplot(111)
        if x_range:
            ax.set_xlim(*x_range)
        if y_range:
            ax.set_ylim(*y_range)

        nx.draw(
            galaxy,
            ax=ax,
            pos={node: galaxy.nodes[node]["pos"] for node in galaxy.nodes},
            node_color=[
                self.rgb(galaxy.nodes[node]["country"]) for node in galaxy.nodes
            ],
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
            nodecolor = (
                self.rgb(country) if country != GalaxyMapData.UNCLAIMED else (0, 0, 0)
            )

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
                x,
                position,
                country,
                color=avg_pos["color"],
                size="x-small",
                ha="center",
            )
