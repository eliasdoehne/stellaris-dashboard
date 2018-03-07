import logging
import pathlib
import threading
import traceback

import click

from stellarisdashboard import save_parser, timeline, visualization_data, visualization_mpl, models

logger = logging.getLogger(__name__)

STELLARIS_SAVE_DIR = pathlib.Path.home() / ".local/share/Paradox Interactive/Stellaris/save games/"


@click.group()
def cli():
    pass


@cli.command()
@click.argument('game_name', type=click.STRING)
def visualize(game_name):
    f_visualize_mpl(game_name)


def f_visualize_mpl(game_name: str, show_everything=False):
    plot_data = visualization_data.EmpireProgressionPlotData(game_name, show_everything=show_everything)
    session = models.SessionFactory()
    try:
        plot_data.initialize()
        plot_data.update_with_new_gamestate()
    except Exception as e:
        raise e
    finally:
        session.close()
    plot = visualization_mpl.MatplotLibVisualization(plot_data)
    plot.make_plots()


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
