"""
This file defines the command line interface for the Stellaris dashboard, and contains the functions
for the top-level functionality of each dashboard feature.

The CLI functions which are annotated with click decorators are only thin wrappers that call the
corresponding function defined immediately below. This allows reusing the code in __main__.py.
"""

import concurrent.futures
import logging
import multiprocessing as mp
import pathlib
import threading

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

threads_help_string = (
    "The number of worker processes that import saves of different games at the same time. "
    "The saves of one game are always imported one after the other."
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
    save_reader = save_parser.SavePathMonitor(save_path)
    save_reader.mark_all_existing_saves_processed()
    worker = import_worker.get_import_worker()
    worker.start()
    waiting = False
    try:
        while not stop_event.is_set():
            for save_file in save_reader.get_new_savefiles():
                game_name = save_file.parent.stem
                if bulk_import.defer_to_running_import(game_name):
                    continue
                worker.submit(save_file, game_name).add_done_callback(_handle_monitor_result)
                waiting = False
                if worker.num_waiting > 1:
                    logger.info(f"{worker.num_waiting} saves waiting to be imported")
            if not waiting and not worker.num_waiting:
                waiting = True
                logger.info(f"Waiting for new saves in {config.CONFIG.save_file_path}")
            stop_event.wait(POLLING_INTERVAL)
    finally:
        import_worker.shutdown_import_worker(timeout=5)


def _handle_monitor_result(future: concurrent.futures.Future) -> None:
    # runs in a thread of the import worker pool
    try:
        result = future.result()
    except import_worker.ImportWorkerError as e:
        logger.warning(str(e))
        return
    import_worker.after_import(result)
    timings = ", ".join(f"{stage} {seconds:.1f} s" for stage, seconds in result.timings.items())
    if result.imported:
        logger.info(f"Imported {result.save_file.name} of {result.game_id} ({result.game_date}): {timings}")
    elif result.parsed and not result.failures and not result.error:
        logger.info(f"Did not import {result.save_file.name} of {result.game_id} ({result.game_date}), it is already imported")


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
    stop_event: threading.Event = None,
    on_save_processed=None,
    worker=None,
) -> None:
    """
    Import saves into the database: all saves of the games matching game_name_prefix, or only save_files.

    The import worker processes parse and import the saves: the saves of one game one after the
    other, in order, and different games in parallel on up to `threads` workers.

    :param threads: Overrides the threads setting: the number of worker processes (if no `worker`).
    :param replace_existing: Replace gamestates of the same date instead of skipping the save.
    :param save_files: Import exactly these saves, in this order.
    :param stop_event: Once this is set, saves that did not start are cancelled; running ones finish.
    :param on_save_processed: Called with the import_worker.ImportResult of each save, in order per game.
    :param worker: The import workers to use (import_worker.ImportWorkerPool); by default a pool of
        `threads` workers that lives as long as this call.
    """
    if threads is not None:
        # since this is usually used when the game is not running, let the user override the thread count
        config.CONFIG.threads = threads
    if save_path is None:
        save_path = config.CONFIG.save_file_path
    save_reader = save_parser.BatchSavePathMonitor(
        save_path, game_name_prefix=game_name_prefix, save_files=save_files
    )
    new_files = save_reader.get_new_savefiles()
    if not new_files:
        return
    own_pool = worker is None
    if own_pool:
        worker = import_worker.ImportWorkerPool(config.CONFIG.threads)
    try:
        futures = {
            worker.submit(save_file, save_file.parent.stem, replace_existing): index
            for index, save_file in enumerate(new_files)
        }
        pending = set(futures)
        while pending:
            if stop_event is not None and stop_event.is_set():
                pending = {future for future in pending if not future.cancel()}  # cancels those not started
            done, pending = concurrent.futures.wait(
                pending, timeout=POLLING_INTERVAL, return_when=concurrent.futures.FIRST_COMPLETED
            )
            for future in sorted(done, key=futures.get):
                if future.cancelled():
                    continue
                result = future.result()
                import_worker.after_import(result)
                if on_save_processed is not None:
                    on_save_processed(result)
    finally:
        if own_pool:
            worker.shutdown(timeout=30)


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
