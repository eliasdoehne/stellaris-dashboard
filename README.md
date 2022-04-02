# Stellaris Dashboard

The Stellaris Dashboard is a program that reads your Stellaris save files while you play the game and shows detailed information and statistics about your playthrough. 

The dashboard has two main components: 
1. A graph-based **graph dashboard** and a historical map of the galaxy.
2. A text-based **event ledger** listing the events that define your game's history.

You can find some screenshots on the Steam workshop page: https://steamcommunity.com/sharedfiles/filedetails/?id=1466534202 

The dashboard works with Ironman and the mod for in-game access is (currently) **achievement compatible**.

# Installation

If you experience any problems, please start [a new discussion topic in the steam workshop](https://steamcommunity.com/sharedfiles/filedetails/discussions/1466534202) or open a issue on github.

## Mod for in-game access

Before following the instructions below, subscribe to the mod [in the Steam Workshop](https://steamcommunity.com/sharedfiles/filedetails/?id=1466534202). Remember to activate the mod [in the Stellaris Launcher](https://imgur.com/g7XeZIz). To access the dashboard, you can open the in-game internet browser by clicking on the help icon in the lower right, or pressing shortcut `ALT-B`. If the help button is not accessible, click the "More" button to reveal it:

 <img src="https://steamuserimages-a.akamaihd.net/ugc/940589883945878302/66874C499AB7088E309D95FFB5A720F80E229BE0/" height="120" alt="Screenshot of overflow menu that contains the help icon">

The dashboard can then be accessed with the buttons in the top right corner of the browser window:

 <img src="https://steamuserimages-a.akamaihd.net/ugc/940589883947546239/330A856DDDEFB565C299CC45D6B2C3CE2B33A9A5/" height="60" alt="Screenshot of navigation buttons added to the browser by the mod">

You can also access the dashboard by opening [http://localhost:28053](http://localhost:28053) in any web browser (maybe you want to use it on a second monitor).

## Dashboard Application

1. Download the latest release of the dashboard from the [releases page](https://github.com/eliasdoehne/stellaris-dashboard/releases) for your OS.
2. Extract the zip archive to a location of your choice.
3. Run the `parse_saves` application to read any existing save files. This will attempt to load any existing save files into the dashboard's database.
4. Run the `stellarisdashboard` application. 

On Linux (and mac?) you should run the applications from a terminal otherwise there won't be any output and it can be difficult to tell if it is working. Looking into improving this in the future.

I unfortunately don't have access to a mac and can't check every release on all platforms as thoroughly as I'd like... If there are problems, please make discussion thread on Steam or open a new issue on github.

### Build it yourself
- Get Python 3.7 (should also work with 3.8)
- (Recommended) create & activate a virtual environment 
- `pip install .`
- `stellarisdashboard`

# Other information

## Save File Location

If the dashboard is not showing any data, first make sure that you play the game until at least one autosave is triggered while the dashboard is running in the background. If the dashboard does not react to the new save file, the most likely explanation is that it cannot find it. 

Check that the save file location is correctly configured in the dashboard settings page, when you go to that path you should see one folder for each of your Stellaris games. By default, the dashboard assumes the values described [in the Stellaris wiki](https://stellaris.paradoxwikis.com/Save-game_editing#Location_.28Steam_Version.29). If you have enabled Steam cloud sync for your save files, you may need to change this location.

## Visibility Settings

For balance and immersion, only some information about other empires is shown by default. You can enable some settings to show everything:
- `Show information for all countries`: This will reveal information about countries you have never met, essentially a cheat. In multiplayer, other player empires will not be revealed.
- `Show all country types`: This will include "pseudo-countries" such as enclaves, pirates,  
- `Store data of all countries`: This will read detailed budgets and pop statistics for non-player countries. It will increase the processing time and database file size, but will allow you to inspect other countries by selecting them from the dropdown menu at the top. (Basic economy information is always read, this setting only controls the very detailed budgets)
- `Filter history ledger by event type`: By default, the event ledger does not show everything on the main page. For example, more detailed events like leader level-ups are only shown on that specific leader's ledger entry. This setting allows you to change this behavior and see all events on the main page.

## How to improve performance

If you find that the dashboard is too slow to browse, you can try some of these things:

- The dashboard graphs can be rearranged into tabs with some manual editing of the configuration file. [See instructions here.](configure_layout.md) This means you can improve the performance by removing some of the graphs that you may not care about. 
- Increase the autosave interval to generate less data and/or increase the "Skip saves" parameter in the dashboard settings. Both of these mean that less data is generated that has to be handled by the dashboard.
- Decrease the `Initial graph resolution` setting. This loads less data into memory when the dashboard starts up and makes the graphs more responsive. Any newly generated data is still fully loaded.
- If you find that the dashboard uses too much memory or CPU, you can reduce the number of CPU threads used for reading new save files in the dashboard settings.

## Game Modes

### Multiplayer

Support for Multiplayer is **experimental**. The dashboard will avoid showing information about other player controlled empires, even if the "Show all empires checkbox" is ticked in the settings. To use the dashboard in multiplayer, you must first configure your multiplayer username in the dashboard settings menu.

### Observer Mode

When using the dashboard in observer mode, you should let the game generate at least one save file as a regular country. After that you can switch to observer mode. You can select the country for the 

You should make sure to enable the `Store data of all countries` setting and you may also want to enable the `Show information for all countries` setting.

## Update notifications
If a new version of the dashboard program is released I also release an update in the Steam workshop, and you will then see a notification in the dashboard UI:

<img src=https://i.imgur.com/x2voRoz.png height=100></img>

This notification is shown because the workshop mod has a version ID, allowing the dashboard to compare its own version to this ID. The dashboard program itself does not send any data. It only runs on your computer.

## Why a separate program?
The dashboard is quite complex and to my knowledge, making a mod with these features by editing the game files would be impossible, or at least much more difficult. This is why you have to run the external program to use the dashboard.

## Mod compatibility

The dashboard may or may not work with other mods, it is developed with the vanilla game in mind. If you experience a problem with a modded game, you can still let me know: If the mod is quite popular and the problem is easily fixed, I will take a look.

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
