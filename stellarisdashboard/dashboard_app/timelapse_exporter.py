import datetime
import io
import logging
from collections import defaultdict
from typing import Tuple, Optional

import matplotlib
import matplotlib.pyplot as plt
import matplotlib.patheffects as path_effects
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
    def __init__(self, game_id, width=16, height=16, dpi=100):
        self.game_id = game_id
        self.galaxy_map_data = GalaxyMapData(game_id=game_id)
        self.galaxy_map_data.initialize_galaxy_graph()
        self._ts = datetime.datetime.now()

        self.width = width
        self.height = height
        self.dpi = dpi

    def create_timelapse(
        self,
        start_date: int,
        end_date: int,
        step_days: int,
        tl_duration: int,
        export_gif: bool,
        export_webp: bool,
        export_frames: bool,
        x_range: Optional[Tuple[float, float]] = None,
        y_range: Optional[Tuple[float, float]] = None,
    ):
        if not any([export_gif, export_webp, export_frames]):
            logger.info("Nothing to do, cancelling timelapse")
            return
        self._ts = datetime.datetime.now()

        frames_path = (
            config.CONFIG.base_output_path / f"galaxy-timelapse/{self._timelapse_id()}/"
        )

        if export_frames:
            logger.info(f"Exporting timelapse frames to {frames_path}")
        if export_gif or export_webp:
            logger.info(f"Exporting animated image")

        export_days = self._day_list(start_date, end_date, step_days)
        frames = []
        for i, day in enumerate(tqdm.tqdm(export_days)):
            frame = self.draw_frame(day, x_range, y_range)
            if export_gif or export_webp:
                frames.append(frame)
            if export_frames:
                frames_path.mkdir(parents=True, exist_ok=True)
                frame.save(
                    frames_path / f"{datamodel.days_to_date(day)}.png",
                    format="png",
                    dpi=(self.dpi, self.dpi),
                    optimize=True,
                )
        if export_gif:
            path = self.output_file("gif")
            logger.info(f"Exporting timelapse gif to {path}")
            frames[0].save(
                path,
                save_all=True,
                append_images=frames[1:],
                duration=tl_duration,
                loop=0,
                optimize=True,
            )
        if export_webp:
            path = self.output_file("webp")
            logger.info(f"Exporting timelapse webp to {path}")
            frames[0].save(
                path,
                save_all=True,
                append_images=frames[1:],
                duration=tl_duration,
                loop=0,
                method=4,
                lossless=True,
                quality=80,
            )
        logger.info("Exports complete.")

    def _day_list(self, start_date, end_date, step_days):
        export_days = list(range(start_date, end_date, step_days))
        if export_days[-1] != end_date:
            export_days.append(end_date)
        return export_days

    def output_file(self, ext="gif") -> str:
        out_dir = config.CONFIG.base_output_path / "galaxy-timelapse"
        out_dir.mkdir(parents=True, exist_ok=True)
        return str((self._output_dir() / f"{self._timelapse_id()}.{ext}").absolute())

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
        self.galaxy_map_data.update_graph_for_date(day)
        nx_galaxy = self.galaxy_map_data.galaxy_graph

        fig = plt.figure(figsize=(self.width, self.height))
        ax = fig.add_subplot(111)
        if x_range:
            ax.set_xlim(*x_range)
        if y_range:
            ax.set_ylim(*y_range)

        nx.draw(
            nx_galaxy,
            ax=ax,
            pos={node: nx_galaxy.nodes[node]["pos"] for node in nx_galaxy.nodes},
            node_color=[
                self.rgb(nx_galaxy.nodes[node]["country"]) for node in nx_galaxy.nodes
            ],
            edge_color=[
                self.rgb(nx_galaxy.edges[e]["country"]) for e in nx_galaxy.edges
            ],
            with_labels=False,
            font_weight="bold",
            node_size=10,
            width=0.5,
        )
        fig.set_facecolor("k")

        self._draw_systems(ax)

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

    def _draw_systems(self, ax):
        nx_graph = self.galaxy_map_data.galaxy_graph
        systems_by_country = defaultdict(lambda: defaultdict(int))
        country_border_ridges = defaultdict(set)
        country_border_ridges[GalaxyMapData.UNCLAIMED] |= nx_graph.graph[
            "system_borders"
        ].get(GalaxyMapData.ARTIFICIAL_NODE, set())

        polygon_patches = []
        for node in nx_graph:
            country = nx_graph.nodes[node]["country"]
            nodecolor = (
                self.rgb(country) if country != GalaxyMapData.UNCLAIMED else (0, 0, 0)
            )

            systems_by_country[country]["x"] += nx_graph.nodes[node]["pos"][0]
            systems_by_country[country]["y"] += nx_graph.nodes[node]["pos"][1]
            systems_by_country[country]["count"] += 1
            systems_by_country[country]["color"] = nodecolor

            polygon_patches.append(
                matplotlib.patches.Polygon(
                    np.array(list(nx_graph.nodes[node]["shape"])).T,
                    fill=True,
                    facecolor=nodecolor,
                    alpha=0.2,
                    linewidth=0,
                )
            )
            country_border_ridges[country] |= nx_graph.graph["system_borders"].get(
                node, set()
            )

        ax.add_collection(PatchCollection(polygon_patches, match_original=True))

        for country, avg_pos in systems_by_country.items():
            if country != GalaxyMapData.UNCLAIMED:
                x = avg_pos["x"] / avg_pos["count"]
                position = avg_pos["y"] / avg_pos["count"]
                ax.text(
                    x,
                    position,
                    country,
                    color="white",
                    size="medium",
                    ha="center",
                    path_effects=[path_effects.withStroke(linewidth=2, foreground="black")],
                )

        for x_values, y_values in self.galaxy_map_data.get_country_border_ridges(
            country_border_ridges
        ):
            ax.plot(
                x_values,
                y_values,
                linewidth=0.75,
                color="w",
                alpha=1,
            )
