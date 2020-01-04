"""
This file defines the command line interface for the Stellaris dashboard, and contains the functions
for the top-level functionality of each dashboard feature.

The CLI functions which are annotated with click decorators are only thin wrappers that call the
corresponding function defined immediately below. This allows reusing the code in __main__.py.
"""

import logging
import multiprocessing as mp
import threading

import click

from stellarisdashboard import config, visualization_data
from stellarisdashboard.parsing import save_parser, timeline


logger = logging.getLogger(__name__)

# These messages are shown by the CLI
save_path_help_string = "The path where the Stellaris save files are stored. This should be the path to the folder containing the save folders for each game."
game_name_help_string = 'An identifier of the game that you want to visualize. It matches prefixes, such that "--game-name uni" matches the game id "unitednationsofearth_-15512622", but not "lokkenmechanists_1256936305"'
showeverything_help_string = (
    "Use this flag if you want to include all empires regardless of visibility."
)

threads_help_string = (
    "The number of threads that run in parallel when reading save games."
)


@click.group()
def cli():
    pass


@cli.command()
@click.option("--save-path", type=click.Path(exists=True, file_okay=False))
def monitor_saves(save_path):
    f_monitor_saves(save_path=save_path)


def f_monitor_saves(save_path=None, stop_event: threading.Event = None):
    """
    Monitor the save path for new files, and maintain the corresponding plot data.

    :param save_path: Override for the path defined in the config.CONFIG object.
    :param stop_event: Signals that the program is shutting down.
    :return:
    """
    if save_path is None:
        save_path = config.CONFIG.save_file_path
    if stop_event is None:
        stop_event = threading.Event()
    polling_interval = config.CONFIG.polling_interval
    save_reader = save_parser.ContinuousSavePathMonitor(save_path)
    save_reader.mark_all_existing_saves_processed()
    tle = timeline.TimelineExtractor()

    show_wait_message = True
    while not stop_event.is_set():
        nothing_new = True
        for (
            game_name,
            gamestate_dict,
        ) in save_reader.get_gamestates_and_check_for_new_files():
            if stop_event.is_set():
                save_reader.shutdown()
                break
            if gamestate_dict is None:
                continue
            show_wait_message = True
            nothing_new = False
            tle.process_gamestate(game_name, gamestate_dict)
            visualization_data.get_current_execution_plot_data(game_name)
            del gamestate_dict
        if nothing_new:
            if show_wait_message:
                show_wait_message = False
                logger.info(f"Waiting for new saves in {config.CONFIG.save_file_path}")
            stop_event.wait(polling_interval)


@cli.command()
@click.option("--threads", type=click.INT, help=threads_help_string)
@click.option(
    "--save-path",
    type=click.Path(exists=True, file_okay=False),
    help=save_path_help_string,
)
@click.option("--game-name", type=click.STRING, help=game_name_help_string, default="")
def parse_saves(threads, save_path, game_name):
    f_parse_saves(threads, save_path, game_name_prefix=game_name)


def f_parse_saves(threads=None, save_path=None, game_name_prefix="") -> None:
    if threads is not None:
        # since this is usually used when the game is not running, let the user override the thread count
        config.CONFIG.threads = threads
    if save_path is None:
        save_path = config.CONFIG.save_file_path
    save_reader = save_parser.BatchSavePathMonitor(
        save_path, game_name_prefix=game_name_prefix,
    )
    tle = timeline.TimelineExtractor()
    for (
        game_name,
        gamestate_dict,
    ) in save_reader.get_gamestates_and_check_for_new_files():
        if gamestate_dict is None:
            continue
        tle.process_gamestate(game_name, gamestate_dict)
        del gamestate_dict


if __name__ == "__main__":
    mp.freeze_support()
    cli()
