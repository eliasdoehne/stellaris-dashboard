import logging
import pathlib
import sys
import threading
import traceback

import click
import sqlalchemy

from stellarisdashboard import save_parser, timeline, visualization_data, visualization_mpl, models

logger = logging.getLogger(__name__)

# Add a stream handler for stdout output
root_logger = logging.getLogger()
root_logger.setLevel(logging.DEBUG)
ch = logging.StreamHandler(sys.stdout)
ch.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
ch.setFormatter(formatter)
root_logger.addHandler(ch)

STELLARIS_SAVE_DIR = pathlib.Path.home() / ".local/share/Paradox Interactive/Stellaris/save games/"


@click.group()
def cli():
    pass


@cli.command()
@click.option('--game_name_prefix', default="", type=click.STRING)
@click.option('--showeverything', is_flag=True)
def visualize(game_name_prefix, showeverything):
    f_visualize_mpl(game_name_prefix, show_everything=showeverything)


def f_visualize_mpl(game_name_prefix: str, show_everything=False):
    matching_games = list(models.get_game_names_matching(game_name_prefix))
    if not matching_games:
        logger.warning(f"No game matching {game_name_prefix} was found in the database!")
    logger.info(f"Found matching games {', '.join(matching_games)} for prefix \"{game_name_prefix}\"")
    for game_name in matching_games:
        try:
            plot_data = visualization_data.EmpireProgressionPlotData(game_name, show_everything=show_everything)
            plot_data.initialize()
            plot_data.update_with_new_gamestate()
            plot = visualization_mpl.MatplotLibVisualization(plot_data)
            plot.make_plots()
        except sqlalchemy.orm.exc.NoResultFound as e:
            logger.error(f'No game matching "{game_name}" was found in the database!')
        except Exception as e:
            logger.error(f"Error occurred while reading from database: {e}")


@cli.command()
@click.option('--threads', type=click.INT)
@click.option('--polling_interval', type=click.INT, default=1)
def monitor_saves(threads, polling_interval):
    f_monitor_saves(threads, polling_interval)


def f_monitor_saves(threads, polling_interval):
    save_reader = save_parser.SaveReader(STELLARIS_SAVE_DIR, threads=threads)
    stop_event = threading.Event()
    t = threading.Thread(target=_monitor_saves, daemon=False, args=(stop_event, save_reader, polling_interval))
    t.start()
    while True:
        exit_prompt = click.prompt("Press x and confirm with enter to exit the program")
        if exit_prompt == "x" or exit_prompt == "X":
            stop_event.set()
            break
    save_reader.teardown()
    t.join()


def _monitor_saves(stop_event: threading.Event, save_reader: save_parser.SaveReader, polling_interval: float):
    save_reader.mark_all_existing_saves_processed()
    wait_time = 0
    tle = timeline.TimelineExtractor()
    show_waiting_message = True
    while not stop_event.wait(wait_time):
        wait_time = polling_interval
        try:
            for game_name, gamestate_dict in save_reader.check_for_new_saves():
                show_waiting_message = True
                tle.process_gamestate(game_name, gamestate_dict)
                plot_data = visualization_data.get_current_execution_plot_data(game_name)
                plot_data.initialize()
                plot_data.update_with_new_gamestate()
            if show_waiting_message:
                show_waiting_message = False
                logger.info("Waiting for new saves...")
        except Exception as e:
            traceback.print_exc()
            logger.error(e)
            break
    save_reader.teardown()


@cli.command()
@click.option('--threads', type=click.INT)
def parse_saves(threads):
    f_parse_saves(threads)


def f_parse_saves(threads=None):
    save_reader = save_parser.SaveReader(STELLARIS_SAVE_DIR, threads=threads)
    tle = timeline.TimelineExtractor()
    for game_name, gamestate_dict in save_reader.check_for_new_saves():
        tle.process_gamestate(game_name, gamestate_dict)
