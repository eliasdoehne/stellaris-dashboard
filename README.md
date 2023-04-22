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
4. Run the `stellarisdashboard` application. (recommended to run from a terminal in Linux and macOs to see the dashboard output)

### Build it yourself
- Get Python 3.10 (it may work on other versions too)
- (Recommended) create & activate a virtual environment 
- `pip install .`
- `pip install maturin` and `maturin develop -r` in `stellarisdashboard/parsing/rust_parser`
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

## Timelapse Export

Using the controls under the "Galaxy Map" tab to export a gif timelapse of the galaxy map in your game. The timelapse shows the area controlled by each country on the given date. An example can be found here: https://www.youtube.com/watch?v=OoiRCEWs00I 

You can either export the timelapse as a single gif file (which can be large and requires more memory), export the individual frames as png images, or both. The export may take several minutes, check the stellaris dashboard console for progress, and for the exact output location.

The form offers some additional parameters to customize the speed of the animation:

- Start date: The in-game date where the animation starts.
- End date: This will be where the animation ends. It is always included as the last frame.
- Step size (days): How many days of game time pass between two frames. Increasing this reduces the total number of frames.
- Frame time (ms): How long each frame is shown in the animation (only applies to the gif)

## Market Price Graphs

The dashboard includes graphs of market prices for each resource in the game. To get the correct values, you will need to manually configure some things in the file `config.yml`, as these settings are not available in the built-in settings page. 

If `config.yml` does not exist, go to the settings page linked at the top (or `localhost:28053/settings/`) and hit "Apply Settings". This will create the file with all of your current settings.

These configurations are applied only when preparing the graph, so you can adjust them at any time in the configuration without reprocessing any data.

### Market fees

Currently, there is no easy way to get the market fee information from the save files. To still get the correct numbers in the graph, you can add the fee manually in the configuration by creating additional entries in the `market_fee` section. By default, a constant fee of 30% is assumed.

For example, to configure a game where the market_fee changed to 20% in 2240 and 5% in 2300, you could change the market_fee section like this:
```
market_fee:
- {date: 2200.01.01, fee: 0.3}
- {date: 2240.01.01, fee: 0.2}
- {date: 2300.01.01, fee: 0.05}
```

### Resources

The default resource configuration should be correct for the current vanilla Stellaris game.

When using mods that change resources in the game (or if Stellaris is updated in the future), you might need to manually adjust the resource configuration in `config.yml` to have the data collected for the additional resources.
  
These values must be configured in the correct order, for vanilla Stellaris, this is the same order in which they are defined in the game file `common/strategic_resources/00_strategic_resources.txt`.

### Names and Localizations

Since Stellaris 3.4, the game no longer stores names (of countries, systems, ships, ...) in the save files as they appear in-game, but instead uses a templating system where names are stored in a more abstract representation. To make names show up in the same way as they do in-game, the dashboard program will try to load the information required from your game files. 

If this system is not configured correctly, you will see some long and cryptic names like 
```
{"key": "format.gen_olig.1", "variables": [
{"key": "generic_oli_desc", "value": {"key": "Sovereign"}}, 
{"key": "generic_states", "value": {"key": "Realms"}}, 
{"key": "This.GetSpeciesName", "value": {"key": "SPEC_Klenn"}}]}
```
when using the dashboard.

If you are not using any mods and installed Stellaris in the default location at (`C:/Program Files (x86)/Steam/steamapps/common/Stellaris/` on windows), there is a good chance that everything will work without any additional actions. 

However, if you use mods that add new names, if you play the game in another language, or if your Stellaris game files (or steam library) are in a different location, you will need to take some manual actions. While the dashboard application itself does not have any kind of localization (yet), you can also change the localization_file_dir to have names rendered in your preferred language.

In this case, you can fix it like this:
- Make sure there is a single folder containing all required localization files ending in `.yml`, including the files from your stellaris game and from all mods that introduce new names. You might need to collect these files yourself and copy them to a convenient location. The file names you use does not matter, the dashboard will take them all and build one big name mapping.
- Update the `Stellaris localization folder` in the dashboard settings (or edit it in config.yml). Restart the dashboard program.
- Look in the dashboard program for some output like `Loaded 162 localization files from <path-to-your-stellaris>/localisation/english` or similar that matches the number of files you prepared.

## Colors

Currently, all country colors shown in the dashboard are randomized and completely unrelated to the colors in-game. Unfortunately, it is not that easy to get the country color from the save files, but it could be possible to add this in a future release.

## How to improve performance

If you find that the dashboard is too slow to browse, you can try some of these things:

- The dashboard graphs can be rearranged into tabs with some manual editing of the configuration file. [See instructions here.](configure_layout.md) This means you can improve the performance by removing some of the graphs that you may not care about. 
- Increase the autosave interval to generate less data and/or increase the "Skip saves" parameter in the dashboard settings. Both of these mean that less data is generated that has to be handled by the dashboard.
- Decrease the `Initial graph resolution` setting. This loads less data into memory when the dashboard starts up and makes the graphs more responsive. Any newly generated data is still fully loaded.
- If you find that the dashboard uses too much memory or CPU, you can reduce the number of CPU threads used for reading new save files in the dashboard settings.

## Game Modes

### Multiplayer

Support for Multiplayer is **experimental**. The dashboard will avoid showing information about other player controlled empires, even if the "Show all empires" checkbox is ticked in the settings. To use the dashboard in multiplayer, you must first configure your multiplayer username in the dashboard settings menu.

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
