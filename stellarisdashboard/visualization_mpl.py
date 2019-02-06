import logging
import math
from typing import List, Dict, Set

import pathlib

import itertools
from matplotlib import pyplot as plt
import matplotlib.lines

from stellarisdashboard import models, visualization_data, config

logger = logging.getLogger(__name__)


class MatplotLibVisualization:
    """ Make a static visualization using matplotlib. """
    COLOR_MAP = plt.get_cmap(name=config.CONFIG.colormap)

    def __init__(self, plot_data):
        self.fig = None
        self.axes = None
        self.axes_iter = None
        self.plot_data: visualization_data.PlotDataManager = plot_data

    def make_plots(self):
        for category, plot_specifications in visualization_data.THEMATICALLY_GROUPED_PLOTS.items():
            self._initialize_axes(category, plot_specifications)
            for plot_spec in plot_specifications:
                ax = next(self.axes_iter)
                if plot_spec.style == visualization_data.PlotStyle.stacked:
                    self._stacked_plot(ax, plot_spec)
                elif plot_spec.style == visualization_data.PlotStyle.budget:
                    self._budget_plot(ax, plot_spec)
                else:
                    self._line_plot(ax, plot_spec)
                if plot_spec.yrange is not None:
                    ax.set_ylim(plot_spec.yrange)
            self.save_plot(plot_id=category)

    def _line_plot(self, ax, plot_spec: visualization_data.PlotSpecification):
        ax.set_title(plot_spec.title)
        for i, (key, x, y) in enumerate(self.plot_data.get_data_for_plot(plot_spec)):
            if y:
                plot_kwargs = self._get_country_plot_kwargs(key)
                ax.plot(x, y, **plot_kwargs)
        ax.legend(loc='upper left')

    def _stacked_plot(self, ax, plot_spec: visualization_data.PlotSpecification):
        ax.set_title(plot_spec.title)
        stacked = []
        labels = []
        colors = []
        data = list(self.plot_data.get_data_for_plot(plot_spec))
        for i, (key, x, y) in enumerate(data):
            stacked.append(y)
            labels.append(key)
            if key in ["physics", "society", "engineering"]:
                colors.append(visualization_data.get_color_vals(key))
            else:
                color_index = i / max(1, len(data) - 1)
                colors.append(MatplotLibVisualization.COLOR_MAP(color_index))
        if stacked:
            ax.stackplot(self.plot_data.dates, stacked, labels=labels, colors=colors, alpha=0.75)
        ax.legend(loc='upper left', prop={'size': 6})

    def _budget_plot(self, ax, plot_spec: visualization_data.PlotSpecification):
        ax.set_title(plot_spec.title)
        stacked_pos = []
        labels_pos = []
        stacked_neg = []
        labels_neg = []
        data = self.plot_data.get_data_for_plot(plot_spec)
        data = [(key, x_values, y_values) for (key, x_values, y_values) in data if not all(y == 0 for y in y_values)]
        net = [0 for _ in self.plot_data.dates]
        for i, (key, x_values, y_values) in enumerate(data):
            if all(y == 0 for y in y_values):
                continue
            if y_values[-1] > 0:
                stacked_pos.append(y_values)
                labels_pos.append(key)
            else:
                stacked_neg.append(y_values)
                labels_neg.append(key)
            for j, y in enumerate(y_values):
                net[j] += y

        if stacked_neg:
            num_neg = len(stacked_neg)
            colors_neg = [MatplotLibVisualization.COLOR_MAP(val / num_neg) for val in range(num_neg)]
            ax.stackplot(self.plot_data.dates, stacked_neg, labels=labels_neg, colors=colors_neg, alpha=0.75, )
        if stacked_pos:
            num_pos = len(stacked_pos)
            colors_pos = [MatplotLibVisualization.COLOR_MAP(1.0 - val / num_pos) for val in reversed(range(num_pos))]
            ax.stackplot(self.plot_data.dates, list(reversed(stacked_pos)), labels=list(reversed(labels_pos)), colors=list(reversed(colors_pos)), alpha=0.75, )
        ax.plot(self.plot_data.dates, net, label="Net income", color="k")
        ax.plot([self.plot_data.dates[0], self.plot_data.dates[-1]], [0, 0], color="k", linewidth=0.3)
        ax.legend(loc='upper left')

    def _initialize_axes(self, category: str, plot_specifications: List[visualization_data.PlotSpecification]):
        num_plots = len(plot_specifications)
        cols = int(math.sqrt(num_plots))
        rows = int(math.ceil(num_plots / cols))
        figsize = (16 * cols, 9 * rows)
        self.fig, self.axes = plt.subplots(rows, cols, figsize=figsize)
        self.axes_iter = iter(self.axes.flat)
        title_lines = [
            f"{self.plot_data.player_country}",
            f"{category}",
            f"{models.days_to_date(360 * self.plot_data.dates[0])} - {models.days_to_date(360 * self.plot_data.dates[-1])}"
        ]
        self.fig.suptitle("\n".join(title_lines))
        for ax in self.axes.flat:
            ax.set_xlim((self.plot_data.dates[0], self.plot_data.dates[-1]))
            ax.set_xlabel(f"Time (Years)")

    def _get_country_plot_kwargs(self, country_name: str):
        linewidth = 1
        c = visualization_data.get_color_vals(country_name, range_min=0, range_max=0.9)
        label = f"{country_name}"
        if country_name == self.plot_data.player_country:
            linewidth = 2
            c = "r"
            label += " (player)"
        return {"label": label, "c": c, "linewidth": linewidth}

    def save_plot(self, plot_id):
        plot_filename = self._get_path(plot_id)
        if not plot_filename.parent.exists():
            plot_filename.parent.mkdir()
        logger.info(f"Saving graph to {plot_filename}")
        plt.savefig(str(plot_filename), dpi=250)
        plt.close("all")

    def _get_path(self, plot_id: str) -> pathlib.Path:
        return config.CONFIG.base_output_path / f"./output/{self.plot_data.game_name}/{self.plot_data.game_name}_{plot_id}.png"


class MatplotLibComparativeVisualization:
    """ Make a static visualization using matplotlib. """
    COLOR_MAP = plt.get_cmap(name=config.CONFIG.colormap)
    LINE_STYLES = ['-', '--', '-.', ':']

    def __init__(self, comparison_id: str):
        self.fig = None
        self.axes = None
        self.comparison_id = comparison_id
        self.plot_data: Dict[str, visualization_data.PlotDataManager] = {}
        self.countries: Set[str] = set()
        self.countries_in_legend: Set[str] = set()
        self.games_in_legend: Set[str] = set()

    def add_data(self, game_name: str, pd: visualization_data.PlotDataManager):
        self.plot_data[game_name] = pd
        self.countries |= pd.owned_planets.keys()

    def make_plots(self):
        for category, plot_specifications in visualization_data.THEMATICALLY_GROUPED_PLOTS.items():
            plot_specifications = [ps for ps in plot_specifications if ps.style == visualization_data.PlotStyle.line]
            if not plot_specifications:
                continue
            self._initialize_axes(category, plot_specifications)
            for plot_spec, ax in zip(plot_specifications, self.axes):
                self.countries_in_legend = set()
                self._make_line_plots(ax, plot_spec)
            self.save_plot(plot_id=category)

    def _initialize_axes(self, category: str, plot_specifications: List[visualization_data.PlotSpecification]):
        num_plots = len(plot_specifications)
        cols = int(math.sqrt(num_plots))
        rows = int(math.ceil(num_plots / cols))
        figsize = (16 * cols, 9 * rows)
        self.fig, self.axes = plt.subplots(rows, cols, figsize=figsize, squeeze=False)
        self.axes = self.axes.flatten()

        player_countries = ", ".join(sorted(pd.player_country for pd in self.plot_data.values()))
        min_date = min(
            pd.dates[0] for pd in self.plot_data.values()
        )
        max_date = max(
            pd.dates[-1] for pd in self.plot_data.values()
        )

        title_lines = [
            f"Game comparison {player_countries}",
            f"{category}",
            f"{models.days_to_date(360 * min_date)} - {models.days_to_date(360 * max_date)}"
        ]
        self.fig.suptitle("\n".join(title_lines))
        for ax in self.axes:
            ax.set_xlim((min_date, max_date))
            ax.set_xlabel(f"Time (Years)")

    def _make_line_plots(self, ax, plot_spec: visualization_data.PlotSpecification):
        ax.set_title(plot_spec.title)
        game_handles = []
        for game_index, (game_name, style) in enumerate(zip(self.plot_data.keys(), itertools.cycle(MatplotLibComparativeVisualization.LINE_STYLES))):
            pd = self.plot_data[game_name]
            for i, (key, x, y) in enumerate(pd.iterate_data(plot_spec)):
                if y:
                    plot_kwargs = self._get_country_plot_kwargs(
                        linestyle=style,
                        country_name=key,
                        game_name=game_name,
                    )
                    ax.plot(x, y, **plot_kwargs)
            game_handles.append(matplotlib.lines.Line2D([], [], linestyle=style, color='grey', label=game_name))
        ax.legend(loc=2, prop={'size': 6})
        self.fig.legend(handles=game_handles, loc=8)

    def _get_country_plot_kwargs(
            self, linestyle: str,
            country_name: str,
            game_name: str,
    ):
        linewidth = 0.75
        alpha = 1
        label = None
        c = visualization_data.get_color_vals(country_name, range_min=0, range_max=0.9)
        if country_name not in self.countries_in_legend:
            self.countries_in_legend.add(country_name)
            label = f"{country_name}"
        if game_name not in self.games_in_legend:
            self.games_in_legend.add(game_name)
        return dict(label=label, c=c, linewidth=linewidth, alpha=alpha, linestyle=linestyle)

    def save_plot(self, plot_id):
        plot_filename = self._get_path(plot_id)
        if not plot_filename.parent.exists():
            plot_filename.parent.mkdir()
        logger.info(f"Saving graph to {plot_filename}")
        plt.savefig(str(plot_filename), dpi=250)
        plt.close("all")

    def _get_path(self, plot_id: str) -> pathlib.Path:
        return config.CONFIG.base_output_path / f"./output/comparison_{self.comparison_id}/comp_{self.comparison_id}_{plot_id}.png"
