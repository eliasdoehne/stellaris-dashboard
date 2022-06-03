import contextlib
import pathlib
from collections import defaultdict

import matplotlib.pyplot as plt
import networkx as nx
import numpy as np
from PIL import Image
from matplotlib import patches
from matplotlib.collections import PatchCollection

import cv2
import io
import datamodel
from stellarisdashboard import config
from stellarisdashboard.dashboard_app.visualization_data import (
    GalaxyMapData,
    get_color_vals,
)


config.CONFIG.show_everything = True
game_id = "chimmconfederation_-579287761"
gmd = GalaxyMapData(game_id=game_id)
gmd.initialize_galaxy_graph()

DPI = 120
WIDTH = 16
HEIGHT = 9


def rgb(x):
    r, g, b = get_color_vals(x)
    return r / 255.0, g / 255.0, b / 255.0


@contextlib.contextmanager
def draw_frame(day):
    print(f"Frame {datamodel.days_to_date(day)}")
    galaxy = gmd.get_graph_for_date(day)
    fig = plt.figure(figsize=(WIDTH, HEIGHT))

    nx.draw(
        galaxy,
        pos={node: galaxy.nodes[node]["pos"] for node in galaxy.nodes},
        node_color=[rgb(galaxy.nodes[node]["country"]) for node in galaxy.nodes],
        edge_color=[rgb(galaxy.edges[e]["country"]) for e in galaxy.edges],
        with_labels=False,
        font_weight="bold",
        node_size=10,
        width=0.5,
    )
    fig.set_facecolor("k")

    systems_by_country = defaultdict(lambda: defaultdict(int))
    y = []
    for node in galaxy:
        z = np.array(list(galaxy.nodes[node]["shape"]))
        country = galaxy.nodes[node]["country"]
        nodecolour = rgb(country) if country != GalaxyMapData.UNCLAIMED else (0, 0, 0)

        if country != GalaxyMapData.UNCLAIMED:
            systems_by_country[country]["x"] += galaxy.nodes[node]["pos"][0]
            systems_by_country[country]["y"] += galaxy.nodes[node]["pos"][1]
            systems_by_country[country]["count"] += 1
            systems_by_country[country]["color"] = nodecolour

        y.append(
            patches.Polygon(
                z.T,
                fill=True,
                facecolor=nodecolour,
                # edgecolor=nodecolour,
                # color=nodecolour,
                alpha=0.2,
                linewidth=0,
            )
        )
    p = PatchCollection(y, match_original=True)
    del galaxy

    plt.gca().add_collection(p)
    plt.text(
        0.05,
        0.05,
        datamodel.days_to_date(day),
        color="white",
        fontfamily="monospace",
        transform=plt.gca().transAxes,
    )
    for country, avg_pos in systems_by_country.items():
        x = avg_pos["x"] / avg_pos["count"]
        y = avg_pos["y"] / avg_pos["count"]
        plt.text(
            x,
            y,
            country,
            color=avg_pos["color"],
            # fontfamily="monospace",
            size="x-small",
            ha="center",
        )

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=DPI)
    buf.seek(0)
    yield Image.open(buf)
    plt.close(fig)
    del buf


def create_gifs():
    imgs = (buf for buf in frames())
    img = next(imgs)  # extract first image from iterator
    img.save(
        fp=pathlib.Path(
            "/home/elias/Documents/projects/stellaris-timeline/output/graph/timelapse.gif"
        ),
        format="GIF",
        append_images=imgs,
        save_all=True,
        duration=500,
        loop=0,
    )


def toImgOpenCV(image: Image.Image) -> np.array:  # Conver image to imgOpenCV
    i = np.array(image)
    # After mapping from PIL to numpy : [R,G,B,A]
    # numpy Image Channel system: [B,G,R,A]
    red = i[:, :, 0].copy()
    i[:, :, 0] = i[:, :, 2].copy()
    i[:, :, 2] = red
    return i[:, :, :3]


def create_video():
    video = cv2.VideoWriter(
        str(config.CONFIG.base_output_path / f"{game_id}-timelapse.mp4"),
        fourcc=cv2.VideoWriter_fourcc(*"mp4v"),
        fps=30,
        frameSize=(WIDTH * DPI, HEIGHT * DPI),
    )

    # Appending the images to the video one by one
    for f in frames():
        img = toImgOpenCV(f)
        for _ in range(5):
            video.write(img)

        # Deallocating memories taken for window creation
    video.release()  # releasing the video generated


def frames():
    for day in range(30, 120 * 360, 120):
        with draw_frame(day) as frame:
            yield frame


# create_gifs()
create_video()
