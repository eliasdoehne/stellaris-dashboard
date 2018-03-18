import logging
import sys
import time
import traceback

import click
import sqlalchemy

from stellarisdashboard import config, save_parser, timeline, visualization_data, visualization_mpl, models

logger = logging.getLogger(__name__)


@click.group()
def cli():
    pass


game_name_help_string = 'An identifier of the game that you want to visualize. It matches prefixes, such that "--game-name uni" matches the game id "unitednationsofearth_-15512622", but not "lokkenmechanists_1256936305"'
showeverything_help_string = 'Use this flag if you want to include all empires regardless of visibility.'


@cli.command()
@click.option('--game-name', default="", type=click.STRING, help=game_name_help_string)
@click.option('--showeverything', is_flag=True, help=game_name_help_string)
def visualize(game_name, showeverything):
    f_visualize_mpl(game_name, show_everything=showeverything)


def f_visualize_mpl(game_name_prefix: str, show_everything=False):
    matching_games = list(models.get_game_names_matching(game_name_prefix))
    if not matching_games:
        logger.warning(f"No game matching {game_name_prefix} was found in the database!")
    match_games_string = ', '.join(matching_games)
    logger.info(f"Found matching games {match_games_string} for prefix \"{game_name_prefix}\"")
    for game_name in matching_games:
        try:
            plot_data = visualization_data.EmpireProgressionPlotData(game_name, show_everything=show_everything)
            plot_data.initialize()
            plot_data.update_with_new_gamestate()
            plot = visualization_mpl.MatplotLibVisualization(plot_data)
            plot.make_plots()
        except sqlalchemy.orm.exc.NoResultFound as e:
            logger.error(f'No game matching "{game_name}" was found in the database!')


@cli.command()
@click.option('--threads', type=click.INT)
@click.option('--save-path', type=click.Path(exists=True, file_okay=False))
@click.option('--polling-interval', type=click.FLOAT, default=0.5)
def monitor_saves(threads, save_path, polling_interval):
    f_monitor_saves(threads, polling_interval, save_path=save_path)


def f_monitor_saves(threads=None, polling_interval=None, save_path=None):
    if save_path is None:
        save_path = config.CONFIG.save_file_path
    if polling_interval is None:
        polling_interval = 0.5  # TODO add to config
    if threads is None:
        threads = config.CONFIG.threads
    save_reader = save_parser.SavePathMonitor(save_path, threads=threads)
    save_reader.mark_all_existing_saves_processed()
    tle = timeline.TimelineExtractor()
    show_waiting_message = True

    try:
        while True:
            for game_name, gamestate_dict in save_reader.check_for_new_saves():
                show_waiting_message = True
                tle.process_gamestate(game_name, gamestate_dict)
                plot_data = visualization_data.get_current_execution_plot_data(game_name)
                plot_data.initialize()
                plot_data.update_with_new_gamestate()
                visualization_data.MOST_RECENTLY_UPDATED_GAME = game_name
            if show_waiting_message:
                show_waiting_message = False
                logger.info(f"Waiting for new saves in {config.CONFIG.save_file_path}")
            time.sleep(polling_interval)
    except Exception as e:
        traceback.print_exc()
        logger.error(e)
        raise e
    finally:
        save_reader.teardown()


@cli.command()
@click.option('--threads', type=click.INT)
@click.option('--save-path', type=click.Path(exists=True, file_okay=False))
@click.option('--game-name', type=click.STRING)
def parse_saves(threads, save_path, game_name):
    f_parse_saves(threads, save_path, game_name_prefix=game_name)


def f_parse_saves(threads=None, save_path=None, game_name_prefix=None):
    if threads is None:
        threads = config.CONFIG.threads
    if save_path is None:
        save_path = config.CONFIG.save_file_path
    save_reader = save_parser.SavePathMonitor(save_path, threads=threads)
    if game_name_prefix is not None:
        save_reader.apply_matching_prefix(game_name_prefix)
    tle = timeline.TimelineExtractor()
    for game_name, gamestate_dict in save_reader.check_for_new_saves():
        if not game_name.startswith(game_name_prefix):
            logger.info(f"Ignoring game {game_name}, as it does not match {game_name_prefix}.")
        tle.process_gamestate(game_name, gamestate_dict)


if __name__ == '__main__':
    cli()
