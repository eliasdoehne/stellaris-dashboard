import contextlib
import datetime
import io
from collections import defaultdict

import cv2
import matplotlib.pyplot as plt
import networkx as nx
import numpy as np
import tqdm
from PIL import Image
import matplotlib
from matplotlib.collections import PatchCollection

from stellarisdashboard import config, datamodel
from stellarisdashboard.dashboard_app.visualization_data import (
    GalaxyMapData,
    get_color_vals,
)

matplotlib.use("Agg")

config.CONFIG.show_everything = True


class TimelapseExporter:
    DPI = 120
    WIDTH = 16
    HEIGHT = 9
    FRAMES_PER_DATE = 5

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

    def create_video(self, start_date: int, end_date: int, step_days: int):
        ts = datetime.datetime.now().isoformat(timespec="seconds")
        video = cv2.VideoWriter(
            str(
                config.CONFIG.base_output_path
                / f"galaxy-timelapse/{self.game_id}-{ts}.mp4"
            ),
            fourcc=cv2.VideoWriter_fourcc(*"mp4v"),
            fps=30,
            frameSize=(self.WIDTH * self.DPI, self.HEIGHT * self.DPI),
        )

        for day in tqdm.tqdm(range(start_date, end_date, step_days)):
            with self.draw_frame(day) as frame:
                img = self.pil_image_to_np(frame)
                for _ in range(self.FRAMES_PER_DATE):
                    video.write(img)

        video.release()

    def rgb(self, name: str):
        r, g, b = get_color_vals(name)
        return r / 255.0, g / 255.0, b / 255.0

    @contextlib.contextmanager
    def draw_frame(self, day):
        galaxy = self.galaxy_map_data.get_graph_for_date(day)

        fig = plt.figure(figsize=(self.WIDTH, self.HEIGHT))
        ax = fig.add_subplot(111)

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
        fig.savefig(buf, format="png", dpi=self.DPI)
        buf.seek(0)
        yield Image.open(buf)
        plt.close(fig)
        del buf

    def _draw_systems(self, ax, galaxy):
        systems_by_country = defaultdict(lambda: defaultdict(int))
        polygon_patches = []
        for node in galaxy:
            country = galaxy.nodes[node]["country"]
            nodecolour = (
                self.rgb(country) if country != GalaxyMapData.UNCLAIMED else (0, 0, 0)
            )

            if country != GalaxyMapData.UNCLAIMED:
                systems_by_country[country]["x"] += galaxy.nodes[node]["pos"][0]
                systems_by_country[country]["y"] += galaxy.nodes[node]["pos"][1]
                systems_by_country[country]["count"] += 1
                systems_by_country[country]["color"] = nodecolour

            polygon_patches.append(
                matplotlib.patches.Polygon(
                    np.array(list(galaxy.nodes[node]["shape"])).T,
                    fill=True,
                    facecolor=nodecolour,
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

    def pil_image_to_np(self, image) -> np.array:
        i = np.array(image)
        # After mapping from PIL to numpy : [R,G,B,A]
        # numpy Image Channel system: [B,G,R,A]
        red = i[:, :, 0].copy()
        i[:, :, 0] = i[:, :, 2].copy()
        i[:, :, 2] = red
        return i[:, :, :3]


if __name__ == "__main__":
    TimelapseExporter("chimmconfederation_-579287761").create_video(30, 720, 120)
