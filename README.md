# Stellaris Dashboard

The stellar dashboard provides in-depth statistics and historical information of your playthrough. Visualizations are available through a 
locally hosted web server, or can be generated using matplotlib through the command-line interface.

Examples for the output can be found here: 

https://imgur.com/a/4dhVd

Here is a video demonstrating integration with the in-game browser: 

https://gfycat.com/DifficultForkedGentoopenguin

# Installation and Use

The Stellaris dashboard requires Python 3.6. It is currently only tested under Linux.

  1. Clone or download and extract the repository.
  2. (Optional) create and activate a virtualenvironment using `virtualenv`.
  3. (Optional) Open a terminal or command line in the downloaded directory and run `pip install .`
  4. Run the program:
     1. Either run `stellarisdashboard` in your console to launch the program in default configuration or `stellarisdashboardcli` to 
        execute specific commands
     2. Or run `python src/stellarisdashboard/main.py` for the default program and `python src/stellarisdashboard/cli.py` for the CLI
        if you did not install the program in step 3.

# Instructions
By default, the program starts a local web server at `http://127.0.0.1:8050/` and runs the code to read the save files. The save files
are processed and the data is added to a database.

If you only want a specific functionality (i.e. only monitor saves without hosting the visualization server) use 
the command line interface with the `stellarisdashboardcli` command as described below.

The resource footprint of the program depends on galaxy size, autosave frequency and game speed. On my system, 2 cores have been enough for 
processing monthly autosaves at the fastest speed, but your mileage may vary depending on how often you pause the game. On the hard drive, 
the database of a 60 year game with monthly autosaves uses about 3 MB of space.

## Default Execution

Installing the package as described should start the dash server and begin to monitor your stellaris save game path for new game files.
You can access the visualizations by either using any web browser to go to `http://127.0.0.1:8050/`, or using the Stellaris mod for 
the in-game browser.

Note that save data is only copied to the database while the `monitor_saves` function is running, i.e. while the commands `stellarisdashboard` 
or `stellarisdashboardcli monitor_saves` are running. If you run the game without these programs active, the save files will eventually be 
overwritten and the data will be lost, resulting in some larger gaps in the visualization.

## Command Line Interface

The CLI allows you to do the following tasks:

  - Only run the save monitoring (`stellarisdashboardcli monitor_saves`)
  - Produce static visualizations using the Matplotlib backend (`stellarisdashboardcli visualize`)
  - Reparse all existing files. Running `stellarisdashboard` or `stellarisdashboardcli monitor_saves` will ignore any existing save files,
  so sometimes it may be necessary to manually re-parse them. (`stellarisdashboardcli parse_saves`)

Further, the CLI commands `monitor_saves` and `parse_saves` allow to explicitly specify the number of CPU cores that are used by the parser.