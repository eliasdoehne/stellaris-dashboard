# Stellar Dashboard

The stellar dashboard provides in-depth statistics and historical information of your playthrough. Visualizations are available through a 
locally hosted web server, or can be generated using matplotlib through the command-line interface.

Examples for the output can be found here: 

<blockquote class="imgur-embed-pub" lang="en" data-id="a/4dhVd"><a href="//imgur.com/4dhVd">Stellaris Timeline</a></blockquote><script async src="//s.imgur.com/min/embed.js" charset="utf-8"></script>


There is also a video demonstrating integration with the in-game browser: https://gfycat.com/DifficultForkedGentoopenguin

# Installation

The Stellaris dashboard requires Python 3.6. It is only tested 

  1. Clone or download the repository.
  2. (Optionally) create and activate a virtualenvironment using `virtualenv`.
  3. (Optionally) Open a terminal or command line in the downloaded directory and run `pip install .`
  4. Either run `stellarisdashboard` in your console if you ran step 3. or run `python src/stellarisdashboard/main.py` otherwise.
  5. If you only want to run a specific part of the program (i.e. only monitor saves without hosting the visualization server) use 
     the command line interface with the `stellarisdashboardcli` command.

# Instructions

Installing the package as described should start the dash server and begin to monitor your stellaris save game path for new game files.
You can access the visualizations by either using any web browser to go to `http://127.0.0.1:8050/`, or using the Stellaris mod for 
the in-game browser.

Note that save data is only copied to the database while the `monitor_saves` function is running, i.e. while the commands `stellarisdashboard` 
or `stellarisdashboardcli monitor_saves` are running. If you run the game without these programs active, the save files will eventually be 
overwritten and the data will be lost, resulting in some larger gaps in the visualization.


