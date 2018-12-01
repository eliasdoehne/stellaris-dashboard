# Stellaris Dashboard

The Stellaris Dashboard reads your Stellaris save files while you play the game and shows you detailed information and stats about your playthrough. 

**Important**: This program only works for **singleplayer** games. Ironman mode should work. Loading old save files is not explicitly supported and may break the program.

# Features

There are two main components: A **timeline dashboard** which shows graphs of many game statistics, and an **event ledger** listing the events that define your game's history.

By using [the mod](https://steamcommunity.com/sharedfiles/filedetails/?id=1466534202) available on the Steam workshop, the dashboard is available directly in-game through the integrated browser (by clicking the help icon).

 For game balance and immersion, only some information about AI empires is shown by default. If you want, you can also configure the program to show everything in the settings menu.

### Timeline Dashboard

The timeline dashboard contains graphs which show information about economy, science, population, political factions and military. There is also a historical map of the galaxy showing which country owned which system at any time in the past.

Screenshots are available in the Steam workshop page: https://steamcommunity.com/sharedfiles/filedetails/?id=1466534202 

And here is a (quite old) animated demo: https://gfycat.com/RealAnguishedAustralianfreshwatercrocodile

### Event Ledger

You can browse through the event ledger by clicking the links, allowing you to view the history of a specific country, leader, system, or war. For each war, a combat log of major fleet battles and planet invasions is shown.

Here are some screenshots of the event ledger: https://imgur.com/a/1Zwkss5 

And here is an animated demo: https://gfycat.com/ClumsyOddballAmphibian

# Installation

If you experience any problems, please start [a new discussion topic in the steam workshop](https://steamcommunity.com/sharedfiles/filedetails/discussions/1466534202) or open a new github issue.

Before following the OS-specific instructions below, subscribe to the browser mod [in the Steam Workshop](https://steamcommunity.com/sharedfiles/filedetails/?id=1466534202). Remember to activate the mod [in the Stellaris Launcher](https://imgur.com/g7XeZIz). You can open the in-game internet browser by clicking on the help icon in the lower right. The dashboard can then be accessed with the buttons in the top right corner of the browser window. 


### Windows
You can follow my [video tutorial](https://youtu.be/gXpkyL_7jNE?t=379) for the installation in Windows.

  1. [Get Python 3](https://www.python.org/) and install it. The Dashboard requires version 3.6 or later.
  ***IMPORTANT: Check the "Add Python to PATH" option in the first step of the installer.***
  2. Download the latest release of the dashboard [from github](https://github.com/eliasdoehne/stellaris-dashboard/releases) (Click on "Download Source Code (zip)")
  3. Extract the archive to a location of your choice.
  4. Run the `install.bat` file in the extracted folder by double-clicking it. At this point, Windows may show you a security warning [like this](https://imgur.com/6PIZCmA). If so, click on "More Information" to enable the "Run anyway" button.
  5. To start the program, run `stellarisdashboard.bat`. A similar Windows security warning as before may appear.

### Linux
 
  1. [Get Python 3](https://www.python.org/). The Dashboard requires 3.6 or later.
  2. Download the latest release of the dashboard [from github](https://github.com/eliasdoehne/stellaris-dashboard/releases) ("Download Source Code (zip)") and extract the archive to a location of your choice.
  3. Open a terminal in that folder and run `python -m venv env` to create a virtual environment. Depending on what Python versions are installed in your system, you may have to replace `python` with the more explicit `python3.7` or similar. Once you activate the virtualenvironment in the next step, this should not matter anymore. 
  4. Run `source env/bin/activate` to activate the virtual environment.
  5. Run `python -m pip install -r requirements.txt` to install all dependencies.
  6. To start the program, run `python -m stellarisdashboard` from the main directory (remember to first activate your virtual environment!).

These instructions should also work on Mac OS X, although I cannot test this. Mac users can also follow the cython instructions in the next section to improve the performance of the program.

### Cython
This section is only important if you see an error message mentioning Cython.

The dashboard uses a Cython extension to accelerate the processing of save files. Binaries are included for some common platforms, but you can also build it yourself:

  1. Install a C compiler and install cython following the instructions at http://docs.cython.org/en/latest/src/quickstart/install.html
  2. Install the dashboard using the instructions above.

The program should still run even with the Cython error, but it will be much slower.

# Other information

## Hardware Requirements

The Hardware requirements depend on several factors galaxy size, your game speed and the autosave frequency.

If you have a quad-core CPU or better, I suggest allowing 2 threads in the settings menu. You can also change the "Only read every n-th save" option in the settings menu. For example, if you set it to 3, the dashboard will only read every third save, allowing you to keep monthly autosaves, while the dashboard will only read one of them per quarter.

For disk space, the database itself should only require a few megabytes per century of gametime. The data for each game is stored in a separate database in your output folder named by the game ID, so you can delete them individually.

Stellaris always deletes the oldest autosave so only the most recent files are kept. If you plan a long game and want to be able to re-generate the database later (in case I release an update or if something goes wrong), you need to continually backup the save files yourself. This requires more disk space for all the save files, but allows you to rebuild the full database at any point using the command `stellarisdashboardcli parse-saves --save-path *path to your save_backup*`. If you do this, I recommend only backing up auto-save files, as the saves are processed in alphabetical order, and the dashboard expects to process them in order of increasing in-game time.


## Update notifications
Since the dashboard is still a work in progress, I occasionally release updates. If a new version of the dashboard program is released I can update the mod in the Steam workshop, and you will see a notification in the dashboard UI. 

This notification can be shown because the workshop mod contains a version ID, allowing the dashboard to compare its own version to this ID. The dashboard program itself does not send any data. It only runs locally on your computer. You can disable update notifications in the settings menu.

## Why a separate program?
The dashboard is quite complex and to my knowledge, making a mod with these features by editing the game files would be impossible, or at least much more difficult. This is why you have to run the external program to use the dashboard.

## Mod compatibility

The dashboard may or may not work with other mods, it is developed with the vanilla game in mind. If you experience a problem with a modded game, you can still let me know: If the mod is quite popular and the problem is easily fixed, I will take a look.

## Known Limitations

  1. Budget numbers may not always be very precise (but they should usually be close).
  2. Loading save files out of order (with respect to in-game time) is not supported, and this will screw up the data.
  3. Renaming things in-game after they have been added to the database might cause issues.
   
If an error occurs, please try restarting the dashboard program. If you run into problems that don't go away after restarting, please start a new discussion topic [on the Steam workshop](https://steamcommunity.com/sharedfiles/filedetails/discussions/1466534202).
   

# Acknowledgements

First of all, thanks to everyone who directly or indirectly contributed to this project!

Thanks to reddit and Steam user 8igualdos0s for initially maintaining a copy of the browser mod [in the Steam Workshop](http://steamcommunity.com/sharedfiles/filedetails/?id=1341242772).

The approach of modding the in-game browser was inspired by [this project](https://github.com/omiddavoodi/StellarisInGameLedger) by reddit user Ariestinak.

---

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


---

Stellaris Copyright © 2018 Paradox Interactive AB. www.paradoxplaza.com
