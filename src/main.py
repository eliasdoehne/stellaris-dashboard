import pathlib
import time

import save_parser

BASE_SAVE_DIR = pathlib.Path("../saves/lokkenmechanists_1256936305/")


def main():
    for save_file in BASE_SAVE_DIR.iterdir():
        print(f"Parsing save {save_file}")
        start_time = time.time()
        parser = save_parser.SaveFileParser(save_file)
        parser.parse_save()
        end_time = time.time()
        print(f"Parsed example save in {end_time - start_time} seconds.")


if __name__ == "__main__":
    main()
