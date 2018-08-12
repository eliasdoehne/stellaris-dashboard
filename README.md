# Stellaris Dashboard

The stellaris dashboard is a program that reads your Stellaris save files while you play the game and shows you more detailed information and stats about your playthrough. 

# Features

There are two main components: A **timeline dashboard** which shows you graphs of many statistics about the empires in your game, and a **text ledger** listing the events that define the history of your game (work in progress).

For game balance, only some information is shown by default: you can only see the fleet power of empires who are friendly, or who have given you active sensor links or defensive pacts. But if you want, you can also configure the program to show data of every empire.

By using [the game mod](https://steamcommunity.com/sharedfiles/filedetails/?id=1466534202) available on the steam workshop, the entire functionality of the dashboard is available in-game through the integrated browser.

**Important**: This program currently does not work with ironman, or multiplayer games. Ironman support will probably be added in a future release. There are no plans for multiplayer support, because the dashboard gives a competitive advantage.

## Timeline Dashboard

Here is a animation of the dashboard in action:

https://gfycat.com/RealAnguishedAustralianfreshwatercrocodile
(the animation is quite old, but it shows the concept)

The timeline dashboard shows information about:

  - Economy: detailed categorized energy, mineral and food budgets (categories include production, sector income, trade, ship and pop maintenance, enclave trade deals...)
  - Science: number of techs, research output, exploration (number of surveyed objects)
  - Population: number of pops, species demographics
  - Factions: size, support and happiness of each faction
  - Military: fleet strength
  - Galaxy Map: shows which country owned which system in the past


## Event Ledger
The second main feature is the event ledger. Here are some screenshots:

https://imgur.com/a/t6858co

Currently, this is a list of wars with detailed combat logs, and for each of your leaders, a biography is generated that shows their lifetime achievements, including:

  - Government reforms
  - Ruler mandates and political activities (faction leadership)
  - Passed edicts, researched technologies, embraced traditions and ascension perks
  - Planet colonizations



# Installation

If you experience any issues, please open a github issue, or start [a discussion topic in the steam workshop](https://steamcommunity.com/sharedfiles/filedetails/discussions/1466534202).

### Windows
You can follow my [video tutorial](https://youtu.be/gXpkyL_7jNE?t=379) for the installation in Windows.

  1. Subscribe to the browser mod [in the Steam Workshop](https://steamcommunity.com/sharedfiles/filedetails/?id=1466534202)
  2. Download and install Python 3.6 from https://www.python.org/downloads/release/python-366/ 
  ***IMPORTANT: Make sure to check the "Add Python 3.6 to PATH" option in the first step of the python installer!***
  3. Download the latest release at the [from github](https://github.com/eliasdoehne/stellaris-dashboard/releases)  (Click on "Download Source Code (zip)")
  4. Extract the archive in a location of your choice.
  5. Run the `install.bat` file in the extracted folder by double-clicking. At this point, Windows may show you a security warning [like this](https://imgur.com/6PIZCmA). If so, click on "More Information" to enable the "Run anyway" button.
  6. To start the program, run the `stellarisdashboard.bat` file. A similar Windows security warning as before may appear.
  7. Activate the mod [in the Stellaris Launcher](https://imgur.com/g7XeZIz)
  8. Play the game!
      
### Linux
Ensure that you are using Python 3.6 or later when running the commands below. You probably have other, possibly incompatible versions of Python installed on your system already!
 
  1. Subscribe to the browser mod [in the Steam Workshop](https://steamcommunity.com/sharedfiles/filedetails/?id=1466534202)
  2. Download and install Python 3.6 from https://www.python.org/downloads/release/python-366/
  3. Download and extract the dashboard program where you want it
  4. Open a terminal in that folder and run `python3.6 -m venv env` to create a virtual environment
  5. Run `source env/bin/activate` to activate the virtual environment (important!)
  6. Run `python3.6 -m pip install -e .` to install the dashboard.
  8. Activate the mod [in the Stellaris Launcher](https://imgur.com/g7XeZIz)
  9. To start the program, run `stellarisdashboard` (Remember to always first activate your virtual environment with `source env/bin/activate`)
  10. Play the game!

These instructions should also work on Mac OS X, although I cannot test this or help with any problems.

# Other information

## Hardware Requirements

The Hardware requirements depend on several factors:

  - Galaxy size
  - How fast you play / how much you pause the game. 
  - Autosave frequency

### CPU
If the dashboard cannot keep up, it may miss some data or certain events in the event ledger may be recorded incorrectly. If you have a quad-core CPU or better, I suggest allowing 2 threads. If the program cannot keep up, you can also reduce the autosave frequency.

### Disk Space
The database itself should only require a few megabytes. The data for each game is stored in a separate database in your output folder named by the game ID, so you can delete them individually if you wish.

Stellaris always deletes the oldest autosave so only the most recent files are kept. If you plan a long game and want to be able to re-generate the database later (in case updates are released or if something goes wrong), you need to continually backup the save files yourself. This uses much more disk space for all the save files, but allows you to rebuild the full database at any point using the command `stellarisdashboardcli parse_saves --save-path path/to/your/save_backup`. 


## Update notifications
Since the dashboard is still a work in progress, I occasionally release updates. If a new version of the dashboard program is released I can update the mod in the Steam workshop, and you will see a notification in the dashboard UI. 

This notification can be shown because the workshop mod contains a version ID, allowing the dashboard to compare its own version to this ID. The dashboard program itself does not send any data. It only runs locally on your computer.

## Why a separate program?
The dashboard is quite complex and to my (limited) knowledge, making a mod with these features by editing the game files would be impossible (at least for me). This is why you have to run the external program to use the dashboard.

# Known Issues

  1. Budget numbers (especially for food, and if sectors are involved) do not always match up exactly (but should be reasonably close)
  2. Star systems that are added to the map mid-game (e.g. precursor homeworlds) are currently not added to the database.
  3. Loading old save files may mess up the data base.
  4. Renaming leaders, planets and system might lead to duplicate or missing entries or similar issues.
 
If an error occurs, please try restarting the dashboard. If you run into problems that don't go away after restarting, please open a github issue, or start a discussion topic [on the Steam workshop](https://steamcommunity.com/sharedfiles/filedetails/discussions/1466534202).
   

# Acknowledgements

First of all, thanks to everyone who contributed to this github project!


Thanks to reddit and Steam user 8igualdos0s for initially maintaining a copy of the browser mod [in the Steam Workshop](http://steamcommunity.com/sharedfiles/filedetails/?id=1341242772).


The approach of modding the in-game browser was inspired by [this project](https://github.com/omiddavoodi/StellarisInGameLedger) by reddit user Ariestinak.

The Python code is released under the MIT license:

MIT License

Copyright (c) 2018 Elias Doehne

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
