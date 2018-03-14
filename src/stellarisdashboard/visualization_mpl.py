import logging
import math
from typing import List

from matplotlib import pyplot as plt

from stellarisdashboard import models, visualization_data, config

logger = logging.getLogger(__name__)


class MatplotLibVisualization:
    """ Make a static visualization using matplotlib. """
    COLOR_MAP = plt.get_cmap(name=config.CONFIG.colormap)

    def __init__(self, plot_data, plot_filename_base=None):
        self.fig = None
        self.axes = None
        self.axes_iter = None
        self.plot_data: visualization_data.EmpireProgressionPlotData = plot_data

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
        for i, (key, x, y) in enumerate(self.plot_data.iterate_data_sorted(plot_spec)):
            if y:
                plot_kwargs = self._get_country_plot_kwargs(key, i, len(plot_spec.plot_data_function(self.plot_data)))
                ax.plot(x, y, **plot_kwargs)
        ax.legend()

    def _stacked_plot(self, ax, plot_spec: visualization_data.PlotSpecification):
        ax.set_title(plot_spec.title)
        stacked = []
        labels = []
        colors = []
        data = list(self.plot_data.iterate_data_sorted(plot_spec))
        for i, (key, x, y) in enumerate(data):
            stacked.append(y)
            labels.append(key)
            colors.append(MatplotLibVisualization.COLOR_MAP(i / (len(data) - 1)))
        if stacked:
            ax.stackplot(self.plot_data.dates, stacked, labels=labels, colors=colors, alpha=0.75)
        ax.legend(loc='upper left')

    def _budget_plot(self, ax, plot_spec: visualization_data.PlotSpecification):
        ax.set_title(plot_spec.title)
        stacked_pos = []
        labels_pos = []
        stacked_neg = []
        labels_neg = []
        data = sorted(self.plot_data.iterate_data_sorted(plot_spec), key=lambda tup: tup[-1][-1], reverse=True)
        data = [(key, x_values, y_values) for (key, x_values, y_values) in data if not all(y == 0 for y in y_values)]
        net = [0 for _ in self.plot_data.dates]
        for i, (key, x_values, y_values) in enumerate(data):
            if y_values[-1] > 0:
                stacked_pos.append(y_values)
                labels_pos.append(key)
            else:
                stacked_neg.append(y_values)
                labels_neg.append(key)
            for j, y in enumerate(y_values):
                net[j] += y

        num_pos = len(stacked_pos)
        colors_pos = [MatplotLibVisualization.COLOR_MAP(0.9 - 0.5 * val / num_pos) for val in reversed(range(num_pos))]
        num_neg = len(stacked_neg)
        colors_neg = [MatplotLibVisualization.COLOR_MAP(0.0 + 0.5 * val / num_neg) for val in range(num_neg)]
        ax.stackplot(self.plot_data.dates, stacked_neg, labels=labels_neg, colors=colors_neg, alpha=0.75, )
        ax.stackplot(self.plot_data.dates, list(reversed(stacked_pos)), labels=labels_pos, colors=colors_pos, alpha=0.75, )
        ax.plot(self.plot_data.dates, net, label="Net income", color="k")
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

    def _get_country_plot_kwargs(self, country_name: str, i: int, num_lines: int):
        linewidth = 1
        c = MatplotLibVisualization.COLOR_MAP(i / (num_lines - 1))
        label = f"{country_name}"
        if country_name == self.plot_data.player_country:
            linewidth = 2
            c = "r"
            label += " (player)"
        return {"label": label, "c": c, "linewidth": linewidth}

    def save_plot(self, plot_id):
        plot_filename = self._get_path(plot_id)
        logger.info(f"Saving graph to {plot_filename}")
        plt.savefig(str(plot_filename), dpi=250)
        plt.close("all")

    def _get_path(self, plot_id: str):
        return config.CONFIG.base_output_path / f"./output/{self.plot_data.game_name}_{plot_id}.png"
