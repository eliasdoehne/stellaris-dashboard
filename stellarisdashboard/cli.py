"""
This file defines the command line interface for the Stellaris dashboard, and contains the functions
for the top-level functionality of each dashboard feature.

The CLI functions which are annotated with click decorators are only thin wrappers that call the
corresponding function defined immediately below. This allows reusing the code in __main__.py.
"""

import collections
import concurrent.futures
import logging
import multiprocessing as mp
import pathlib
import threading
from typing import Deque, Tuple

import click

from stellarisdashboard import bulk_import, config, import_worker
from stellarisdashboard.parsing import save_copies, save_parser

logger = logging.getLogger(__name__)

# These messages are shown by the CLI
save_path_help_string = (
    "The path where the Stellaris save files are stored. "
    "This should be the path to the folder containing the save folders for each game."
)
game_name_help_string = (
    "An identifier of the game that you want to visualize. It matches prefixes, such that "
    '"--game-name uni" matches the game id "unitednationsofearth_-15512622", '
    'but not "lokkenmechanists_1256936305"'
)
showeverything_help_string = (
    "Use this flag if you want to include all empires regardless of visibility."
)

# seconds between checks for new save files
POLLING_INTERVAL = 0.5
# import_tag of the log records of the saves imported by the save monitor
MONITOR_LOG_TAG = "monitor"

threads_help_string = (
    "The number of threads that run in parallel when reading save games."
)


@click.group()
def cli():
    """Use COMMAND --help for more info on each command."""
    pass


@cli.command()
@click.option("--save-path", type=click.Path(exists=True, file_okay=False))
def monitor_saves(save_path):
    """
    Watch for new or modified files and import them into DB.
    """
    f_monitor_saves(save_path=save_path)


def f_monitor_saves(save_path=None, stop_event: threading.Event = None):
    """
    Monitor the save path for new files, and have the import worker process import them.

    :param save_path: Override for the path defined in the config.CONFIG object.
    :param stop_event: Signals that the program is shutting down.
    :return:
    """
    if save_path is None:
        save_path = config.CONFIG.save_file_path
    if stop_event is None:
        stop_event = threading.Event()
    save_reader = save_parser.ContinuousSavePathMonitor(save_path)
    save_reader.mark_all_existing_saves_processed()
    worker = import_worker.get_import_worker()
    worker.start()

    # submitted saves, oldest first: results are handled in the order of the saves
    pending: Deque[Tuple[pathlib.Path, concurrent.futures.Future]] = collections.deque()
    show_wait_message = True
    try:
        while not stop_event.is_set():
            for save_file in save_reader.get_new_saves():
                game_name = save_file.parent.stem
                if bulk_import.defer_to_running_import(game_name):
                    continue
                show_wait_message = True
                pending.append(
                    (save_file, worker.submit(save_file, game_name, log_tag=MONITOR_LOG_TAG))
                )
                if len(pending) > 1:
                    logger.info(f"{len(pending)} saves waiting to be imported")
            while pending and pending[0][1].done():
                _handle_monitor_result(*pending.popleft())
            if pending:
                # wake up when the oldest save is done, or to look for new saves
                concurrent.futures.wait([pending[0][1]], timeout=POLLING_INTERVAL)
            else:
                if show_wait_message:
                    show_wait_message = False
                    logger.info(f"Waiting for new saves in {config.CONFIG.save_file_path}")
                stop_event.wait(POLLING_INTERVAL)
    finally:
        import_worker.shutdown_import_worker(timeout=5)


def _handle_monitor_result(save_file: pathlib.Path, future: concurrent.futures.Future) -> None:
    try:
        result = future.result()
    except import_worker.ImportWorkerError as e:
        logger.warning(str(e))
        return
    import_worker.after_import(result)
    timings = ", ".join(f"{stage} {seconds:.1f} s" for stage, seconds in result.timings.items())
    if result.error:
        logger.error(f"Import of {save_file} failed: {result.error}")
    elif result.imported:
        logger.info(f"Imported {save_file.name} of {result.game_id} ({result.game_date}): {timings}")
    elif result.parsed and not result.failures:
        logger.info(f"Did not import {save_file.name} of {result.game_id} ({result.game_date}), it is already imported")


@cli.command()
@click.option("--threads", type=click.INT, help=threads_help_string)
@click.option(
    "--save-path",
    type=click.Path(exists=True, file_okay=False),
    help=save_path_help_string,
)
@click.option("--game-name", type=click.STRING, help=game_name_help_string, default="")
def parse_saves(threads, save_path, game_name):
    """
    Batch parser to import all saved game files to DB.
    """
    f_parse_saves(threads, save_path, game_name_prefix=game_name)


def f_parse_saves(
    threads=None,
    save_path=None,
    game_name_prefix="",
    replace_existing=False,
    save_files=None,
    use_worker_processes=False,
    stop_event: threading.Event = None,
    on_save_processed=None,
) -> None:
    """
    Import saves into the database: all saves of the games matching game_name_prefix, or only save_files.

    :param replace_existing: Replace gamestates of the same date instead of skipping the save.
    :param save_files: Import exactly these saves, in this order.
    :param use_worker_processes: Parse saves in worker processes even with a single thread.
    :param stop_event: Stop importing once this is set.
    :param on_save_processed: Called with (game_name, gamestate_dict) after each save; gamestate_dict is None
        if the save could not be parsed.
    """
    if threads is not None:
        # since this is usually used when the game is not running, let the user override the thread count
        config.CONFIG.threads = threads
    if save_path is None:
        save_path = config.CONFIG.save_file_path
    save_reader = save_parser.BatchSavePathMonitor(
        save_path,
        game_name_prefix=game_name_prefix,
        save_files=save_files,
        use_worker_processes=use_worker_processes,
    )
    for (
        game_name,
        gamestate_dict,
        save_file,
    ) in save_reader.get_gamestates_and_check_for_new_files():
        if stop_event is not None and stop_event.is_set():
            break
        save_copies.back_up_save(save_file, game_name)
        if gamestate_dict is not None:
            f_import_gamestate(
                game_name, gamestate_dict, save_file, replace_existing=replace_existing
            )
        if on_save_processed is not None:
            on_save_processed(game_name, gamestate_dict)
        del gamestate_dict


# the import itself lives in the module the worker process imports; see import_worker.import_gamestate
f_import_gamestate = import_worker.import_gamestate


@cli.command()
@click.option("--threads", type=click.INT, help=threads_help_string)
@click.option("--game-name", type=click.STRING, help=game_name_help_string, default="")
def reprocess_failed_saves(threads, game_name):
    """
    Import the kept copies of failed saves again, e.g. after updating the dashboard.
    """
    f_reprocess_failed_saves(threads, game_name_prefix=game_name)


def f_reprocess_failed_saves(threads=None, game_name_prefix="") -> None:
    """
    Import the kept copies of failed saves again. Gamestates of the same date are replaced,
    copies that import cleanly are deleted, and the records of the others are updated.
    """
    failed_dir = save_copies.failed_saves_path()
    if not failed_dir.is_dir():
        logger.info(f"No failed saves to reprocess in {failed_dir}")
        return
    # every kept copy should be reprocessed, regardless of the skip_saves setting
    skip_saves = config.CONFIG.skip_saves
    config.CONFIG.skip_saves = 0
    try:
        f_parse_saves(
            threads,
            save_path=failed_dir,
            game_name_prefix=game_name_prefix,
            replace_existing=True,
        )
    finally:
        config.CONFIG.skip_saves = skip_saves


if __name__ == "__main__":
    mp.freeze_support()
    cli()
