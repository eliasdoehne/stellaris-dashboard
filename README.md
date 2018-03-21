# Stellaris Dashboard

Stellaris currently does not have a timeline feature that shows some statistics and historical info, so I decided to build one. 

Here is a one minute animation of the interactive dashboard:
https://gfycat.com/DifficultForkedGentoopenguin

You can also export static images:
https://imgur.com/a/4ArVU

So far, it includes information about your:
  - economy: categorized energy, mineral and food budgets (categories such as trade, production, sector income, ship maintenance and many more)
  - science: number of techs, research output or the number of surveyed objects
  - demographics: number of pops, distribution of species
  - factions: size, support and happiness of each faction
  - military: fleet strength
The program aggregates even more data than this, so in the future I hope to include other cool info, such as leader biographies on a timeline or detailed war logs.

# Installation and Use

The Stellaris dashboard requires Python 3.6. It is mainly tested in Linux, but it *should* also work in Windows.

  1. Download and extract the repository.
  2. (Optional) create and activate a virtualenvironment using `virtualenv`.
  3. (Optional) Open a terminal or command line in the downloaded directory and run `pip install .`
  4. Run the program:
     1. If you did 3., either run `stellarisdashboard` in your console to launch the program in default configuration or `stellarisdashboardcli` to 
        execute specific commands
     2. Or change to the `src/` folder and run `python -m stellarisdashboard` for the default program and `python -m stellarisdashboard.cli` for the CLI
        if you did not install the program in step 3.
  5. To use the dashboard in the in-game browser, copy the contents of the `mod/` folder into your Stellaris mod folder and activate the "Stellaris Dashboard 
  Integration" mod in the Stellaris launcher.

# Instructions

## Default Execution
The command `stellarisdashboard` runs the code to read the save files in the background and starts a local web server for 
the interactive graphs. 

As the save files are processed in the background, the data is added to a database in your output folder. You can specify this location in the
`config.ini` file. By default, every file generated by the dashboard ends up in a subfolder of your user directory, e.g. `$USER/stellarisdashboard/`.

While the program is running, you can access the visualizations by either using a browser to go to `http://127.0.0.1:8050/`,
or using the included mod for the in-game browser. 

Note: The current method of reading the save files is a bit slow, so the graphs may lag behind the game quite a bit. For a 1000 star galaxy, reading a 
single save file can take about 30 seconds...

## Command Line Interface

The command line interface allows you to:

  - Only run the save monitoring without any interactivity. This only builds the database, which you can later visualize (`stellarisdashboardcli monitor_saves`)
  - Produce the static visualizations (`stellarisdashboardcli visualize`)
  - Reparse all existing files. Running `stellarisdashboard` or `stellarisdashboardcli monitor_saves` will ignore any existing save files,
  so sometimes it may be necessary to manually re-parse them. (`stellarisdashboardcli parse_saves`)

Any parameters provided to these commands overwrite the values set in the `config.ini` file.

## Configuration

The following parameters can be specified in a config.ini file placed in the same folder as the stellarisdashboard/config.py file:

  - `save_file_path`: The path where the save files are.
  - `base_output_path`: The path where any files generated by the program end up. This includes the database and any images you generate.
  - `threads`: The number of concurrent processes that are used for reading save files. 
  - `colormap`: The colormap used when producing static images.
  - `port`:  The port where the stellarisdashboard webapp is served. If you change this, you should adapt the URL in the 
  `mod/Timeline/interface/browser.gui` file accordingly.


# Hardware Requirements

The Hardware requirements depend on how fast you play, the galaxy size, how frequently you generate autosaves, and how often you pause the game. 
If the dashboard cannot keep up, it will likely skip some saves as they are overwritten by the game and it will be missing some data points.

Most of the testing has been done in Ubuntu, but it should run in Windows as well. Some optimizations 
are not (yet) supported on Windows systems, so it takes longer to read the save files (about 1.5 - 3 times longer). 
So for Windows users, using 3 threads for monthly autosaves is recommended. If you are on Linux, 2 threads should be OK. 
Since Stellaris mostly uses a single CPU core, the program should run decently on any modern quad-core CPU.

As for disk space, each game's data is stored in a separate database in your output folder, so you can delete them 
individually if you wish. A 60 year testing playthrough with monthly autosave requires about 3.5 Megabytes for the database.

The game deletes the autosaves in a "rolling" manner, so only the most recent saves are kept. If you plan a longer playthrough 
and want to be able to re-generate the database later, you need to preserve the save files. This can be done by
running a script that automatically backing them up to another folder. On linux, you can do this quite simply by running a script like

    while true
    do
           sleep 5;
           rsync -r -u -v "save games" "save_backup";
    done
in the background while you play. Something similar should be possible in Windows, too.

This uses a lot of disk space (in my experience about 1 GB every 50 years for a huge galaxy), but allows you to rebuild the full database at any point using 
`stellarisdashboardcli parse_saves --save_backup`. 

# Bugs

Some known bugs:
  - the economic budget numbers might not exactly match with what is shown in-game. This is because I had to reproduce some of the math that the
  game does behind the scenes and almost definitely missed many modifiers. However, most numbers should be accurate enough to get a decent idea.
  - Sometimes the dashboard glitches out and adds a line that crosses through most of the graphs. This probably happens because different parts of the program 
  talk to the database at the same time. When this happens, simply kill the program and start it again.
  - 























