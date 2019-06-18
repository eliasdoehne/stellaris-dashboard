# Stellaris Dashboard

The Stellaris Dashboard reads your Stellaris save files while you play the game and shows detailed information and statistics about your playthrough. 

Some screenshots are available on the Steam workshop page: https://steamcommunity.com/sharedfiles/filedetails/?id=1466534202 

**Important**: This program only works for **singleplayer** games. Ironman mode should work. Loading old save files is not supported and may or may not work as expected.

# Features

There are two components: A graph-based **timeline dashboard** which shows up to 60 graphs of game statistics and a historical map of the galaxy, and a text-based **event ledger** listing the events that define your game's history.

Subscribing to [the mod](https://steamcommunity.com/sharedfiles/filedetails/?id=1466534202) on the Steam workshop makes the dashboard directly available in-game through the integrated browser, which can be accessed by the help icon in the bottom right of the Stellaris UI, or with the keyboard shortcut `ALT-B`.

For game balance and immersion, only some information about AI empires is shown by default. If you want, you can also configure the program to show everything in the settings menu.


# Installation

If you experience any problems, please start [a new discussion topic in the steam workshop](https://steamcommunity.com/sharedfiles/filedetails/discussions/1466534202).

Before following the instructions below, subscribe to the mod [in the Steam Workshop](https://steamcommunity.com/sharedfiles/filedetails/?id=1466534202). Remember to activate the mod [in the Stellaris Launcher](https://imgur.com/g7XeZIz). To access the dashboard, you can open the in-game internet browser by clicking on the help icon in the lower right, or pressing shortcut `ALT-B`. If the help button is not accessible, click the "More" button to reveal it:

 <img src="https://steamuserimages-a.akamaihd.net/ugc/940589883945878302/66874C499AB7088E309D95FFB5A720F80E229BE0/" height="120">

The dashboard can then be accessed with the buttons in the top right corner of the browser window:


 <img src="https://steamuserimages-a.akamaihd.net/ugc/940589883947546239/330A856DDDEFB565C299CC45D6B2C3CE2B33A9A5/" height="60">

Alternatively, you can access the dashboard by opening [http://localhost:28053](http://localhost:28053) in any web browser (e.g. if you want to use it one a second monitor).

### Windows

  1. Download the latest build of the dashboard from the [releases page](https://github.com/eliasdoehne/stellaris-dashboard/releases).
  2. Extract the zip archive to a location of your choice.
  3. Run `stellarisdashboard.exe`.

### Linux
 
  1. [Get Python 3.6 or later](https://www.python.org/).
  2. Download the latest release [from github](https://github.com/eliasdoehne/stellaris-dashboard/releases) ("Download Source Code") and extract to a location of your choice.
  3. Open a terminal in the extracted folder and run `python -m venv env` to create a virtual environment. Depending on what Python versions are installed in your system, you may need to replace `python` in this step with the more explicit `python3.7` or similar. Once you activate the virtualenvironment in the next step, this should not matter anymore. 
  4. Run `source env/bin/activate` to activate the virtual environment.
  5. Run `python -m pip install -r requirements.txt` to install all dependencies.
  6. To start the program, run `python -m stellarisdashboard` from the main directory (remember to first activate your virtual environment!).

These instructions should also work on Mac OS X, although I cannot test this. Mac users should also follow the Cython instructions in the next section to improve the performance of the program.

### Cython
This section is only important if you see an error message mentioning Cython.

The dashboard uses a Cython extension to accelerate the processing of save files. Binaries are included for some common platforms, but you can also build it yourself:

  1. Install a C compiler and install cython following the instructions at http://docs.cython.org/en/latest/src/quickstart/install.html
  2. Install the dashboard using the instructions above.

The program should still run even with the Cython error, but it will be much slower.

# Other information

## Hardware Requirements

The Hardware requirements depend on several factors including galaxy size, your preferred game speed and autosave frequency.

If you have a quad-core CPU or better, I suggest allowing 1 or 2 threads in the settings menu. You can also change the "Only read every n-th save" setting in the settings menu. For example, if you set it to 3, the dashboard will only read every third save, allowing you to keep monthly autosaves, while the dashboard will only read one of them per quarter.

For disk space, the database itself should require a few megabytes per in-game decade. The data for each game is stored in a separate database in your output folder (for example `output/db/unitednationsofearth6_1643184243.db`), which is named by the game ID, so you can delete them individually. To reduce the database size, you can again change the  "Only read every n-th save" setting described above.

## Update notifications
Since the dashboard is still a work in progress, I release updates fairly regularly. If a new version of the dashboard program is released I also release an update in the Steam workshop, and you will then see a notification in the dashboard UI.

This notification is shown because the workshop mod has a version ID, allowing the dashboard to compare its own version to this ID. The dashboard program itself does not send any data. It only runs locally on your computer. You can disable the update notifications in the settings menu.

## Why a separate program?
The dashboard is quite complex and to my knowledge, making a mod with these features by editing the game files would be impossible, or at least much more difficult. This is why you have to run the external program to use the dashboard.

## Mod compatibility

The dashboard may or may not work with other mods, it is developed with the vanilla game in mind. If you experience a problem with a modded game, you can still let me know: If the mod is quite popular and the problem is easily fixed, I will take a look.

## Known Limitations

  1. Loading save files out of order (with respect to in-game time) is not supported, and will probably screw up the database.
  2. Renaming things in-game after they have been added to the database may or may not work as expected.
  3. The dashboard has so far only been tested for the early- and mid-game (first 100 years or so).
  
If an error occurs, please try restarting the dashboard program. If you run into problems that don't go away after restarting, please start a new discussion topic [in the Steam workshop page](https://steamcommunity.com/sharedfiles/filedetails/discussions/1466534202) or open an issue on github.
   

# Acknowledgements

First of all, thanks to everyone who directly or indirectly contributed to this project!

Thanks to reddit and Steam user 8igualdos0s for initially maintaining a copy of the browser mod [in the Steam Workshop](http://steamcommunity.com/sharedfiles/filedetails/?id=1341242772).

The approach of modding the in-game browser was inspired by [this project](https://github.com/omiddavoodi/StellarisInGameLedger) by reddit user Ariestinak.


Development of this project is supported by [JetBrains](http://jetbrains.com/?from=stellarisdashboard) with an open source license:

[<img src="img/jetbrains.png" height="80">](http://jetbrains.com/?from=stellarisdashboard)

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
