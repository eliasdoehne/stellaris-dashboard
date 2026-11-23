import copy
import dataclasses
import itertools
import json
import logging
import multiprocessing
import os
import pathlib
import platform
import re
import shutil
import sys
import tempfile
from typing import Any, Callable, Dict, Iterable, Iterator, List, Mapping, Optional, Tuple

import yaml

from stellarisdashboard import i18n, legacy_config_defaults
from stellarisdashboard.i18n import N_

LOG_LEVELS = {
    "CRITICAL": logging.CRITICAL,
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
}

LOG_FORMAT = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

CONFIG: "Config" = None
# what loading config.yml at startup did, including the values it rejected
LOAD_RESULT: "ApplyResult" = None
logger: logging.Logger = logging.getLogger()


def initialize_logger():
    # Add a stream handler for stdout output
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    stdout_ch = logging.StreamHandler(sys.stdout)
    stdout_ch.setLevel(logging.INFO)
    stdout_ch.setFormatter(LOG_FORMAT)
    root_logger.addHandler(stdout_ch)


def _get_default_stellaris_user_data_path():
    # according to https://stellaris.paradoxwikis.com/Save-game_editing
    home = pathlib.Path.home()
    if platform.system() == "Windows":
        one_drive_path = home / "OneDrive/Documents/Paradox Interactive/Stellaris/"
        non_one_drive_path = home / "Documents/Paradox Interactive/Stellaris/"
        if one_drive_path.exists():
            return one_drive_path
        else:
            return non_one_drive_path
    elif platform.system() == "Linux":
        return home / ".local/share/Paradox Interactive/Stellaris/"
    else:
        return home / "Documents/Paradox Interactive/Stellaris/"


def _get_default_save_path():
    return _get_default_stellaris_user_data_path() / "save games"


def _get_default_stellaris_install_path():
    for p in [
        pathlib.Path("C:/Program Files (x86)/Steam/steamapps/common/Stellaris/"),
        (pathlib.Path.home() / ".steam/steamapps/common/Stellaris/").absolute(),
        (pathlib.Path.home() / ".steam/root/steamapps/common/Stellaris/").absolute(),
    ]:
        if p.exists():
            return p
    return pathlib.Path(__file__).parent


def _get_default_base_output_path():
    return pathlib.Path.cwd() / "output"


GALAXY_MAP_TAB = "Galaxy Map"
MARKET_TAB = "Markets"
DEFAULT_TAB_LAYOUT = {
    "Budget": [
        "energy_budget",
        "mineral_budget",
        "food_budget",
        "consumer_goods_budget",
        "alloys_budget",
        "trade_budget",
        "influence_budget",
        "unity_budget",
        "volatile_motes_budget",
        "exotic_gases_budget",
        "rare_crystals_budget",
        "living_metal_budget",
        "zro_budget",
        "dark_matter_budget",
        "nanites_budget",
        "minor_artifacts_budget",
        "astral_threads_budget",
        "biomass_budget",
    ],
    "Economy": [
        "net_energy_income_graph",
        "net_mineral_income_graph",
        "net_food_income_graph",
        "net_consumer_goods_income_graph",
        "net_alloys_income_graph",
        "net_trade_income_graph",
        "net_biomass_income_graph",
        "empire_size_graph",
    ],
    "Demographics": [
        "species_distribution_graph",
        "job_distribution_graph",
        "ethos_distribution_graph",
        "strata_distribution_graph",
        "faction_distribution_graph",
        "planet_pop_distribution_graph",
    ],
    "Pops": [
        "species_happiness_graph",
        "species_crime_graph",
        "species_power_graph",
        "job_happiness_graph",
        "job_crime_graph",
        "job_power_graph",
        "ethos_happiness_graph",
        "ethos_crime_graph",
        "ethos_power_graph",
        "faction_approval_graph",
        "faction_happiness_graph",
        "faction_support_graph",
        "faction_crime_graph",
        "faction_power_graph",
        "strata_happiness_graph",
        "strata_crime_graph",
        "strata_power_graph",
    ],
    "Planets": [
        "planet_count_graph",
        "planet_stability_graph",
        "planet_happiness_graph",
        "planet_amenities_graph",
        "planet_housing_graph",
        "planet_crime_graph",
        "planet_power_graph",
    ],
    "Science": [
        "technology_progress_graph",
        "research_output_graph",
        "research_output_by_category_graph",
    ],
    "Military": ["fleet_size_graph", "military_power_graph", "fleet_composition_graph"],
    "Victory": [
        "victory_rank_graph",
        "victory_score_graph",
        "victory_economy_score_graph",
    ],
    MARKET_TAB: [],  # filled dynamically based on resource config
}
DEFAULT_MARKET_RESOURCES = [
    # all available resources, in the order in which they are defined in the game files
    # common/strategic_resources/00_strategic_resources.txt
    # Put None as price of non-tradeable resources (These must still be added because ordering matters)
    {"name": "energy", "base_price": 1},
    {"name": "minerals", "base_price": 1},
    {"name": "food", "base_price": 1},
    {"name": "physics_research", "base_price": None},
    {"name": "society_research", "base_price": None},
    {"name": "engineering_research", "base_price": None},
    {"name": "influence", "base_price": None},
    {"name": "unity", "base_price": None},
    {"name": "trade", "base_price": None},
    {"name": "consumer_goods", "base_price": 2},
    {"name": "alloys", "base_price": 4},
    {"name": "volatile_motes", "base_price": 10},
    {"name": "exotic_gases", "base_price": 10},
    {"name": "rare_crystals", "base_price": 10},
    {"name": "sr_living_metal", "base_price": 20},
    {"name": "sr_zro", "base_price": 20},
    {"name": "sr_dark_matter", "base_price": 20},
    {"name": "nanites", "base_price": None},
    {"name": "minor_artifacts", "base_price": None},
    {"name": "astral_threads", "base_price": None},
    {"name": "biomass", "base_price": None},
    {"name": "menace", "base_price": None},
    {"name": "advanced_logic", "base_price": None},
    {"name": "feral_insight", "base_price": None},
]
DEFAULT_MARKET_FEE = [{"date": "2200.01.01", "fee": 0.3}]


STELLARIS_LANGUAGES = (
    # the languages the game ships, by their key in the localisation files
    "l_braz_por",
    "l_english",
    "l_french",
    "l_german",
    "l_japanese",
    "l_korean",
    "l_polish",
    "l_russian",
    "l_simp_chinese",
    "l_spanish",
)
# Mods can add languages; any key of this form is accepted.
_STELLARIS_LANGUAGE_PATTERN = re.compile(r"^l_\w+$")

# Settings that were removed. They are ignored (with a warning) when an old config.yml contains them.
REMOVED_SETTINGS = {
    "read_all_countries",
    "polling_interval",  # now a constant in cli.py
    "include_id_in_names",  # the ID is always included, it tells apart countries with the same name
    "plot_width",  # graphs fill the available width
    "save_name_filter",  # use skip_saves to import fewer saves
}


class InvalidSettingValue(ValueError):
    """Raised by a setting's parser when a value is rejected.

    ``reason`` is an English message, marked for translation with N_ and formatted
    with ``params``, so the settings page can show it in the dashboard's language.
    """

    def __init__(self, reason: str, **params):
        self.reason = reason
        self.params = params
        super().__init__(i18n.format_message(reason, params))


def _display_value(value) -> str:
    text = repr(value) if isinstance(value, str) else str(value)
    return text if len(text) <= 80 else text[:77] + "..."


# Kinds of settings, which decide how values are parsed and how the settings page shows them.
BOOL = "bool"
INT = "int"
FLOAT = "float"
STR = "str"
PATH = "path"
CHOICE = "choice"
STRUCTURED = "structured"  # a YAML list or mapping, only editable in config.yml

_TRUE_STRINGS = {"true", "on", "yes", "1"}
_FALSE_STRINGS = {"false", "off", "no", "0"}

# Returned by SettingSpec.parse for an empty value, which means "use the default".
USE_DEFAULT = object()


@dataclasses.dataclass(frozen=True)
class SettingSpec:
    """The declaration of a setting: its type, default, validation and how it is shown.

    Labels and descriptions are translated interface text, so they are kept in
    dashboard_app/settings.py (keyed by setting key); this module does not depend on
    the interface language.
    """

    key: str
    kind: str
    default: Any = None
    # for defaults that depend on the system (detected paths); called once at import
    default_factory: Optional[Callable[[], Any]] = None
    # stable id of the settings page section, see CATEGORIES
    category: str = "interface"
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    # CHOICE settings: returns {value: display name}. Names that need translation are
    # translated by the settings page.
    choices: Optional[Callable[[], Dict[str, str]]] = None
    # Called with the value after it was converted to the setting's kind. Returns the
    # final value (e.g. normalized), raises InvalidSettingValue to reject it.
    validate: Optional[Callable[[Any], Any]] = None
    requires_restart: bool = False
    # shown in a collapsed section of the settings page
    advanced: bool = False
    # False for settings that can only be changed in config.yml
    in_ui: bool = True
    # False for runtime-only state that is never written to config.yml
    persisted: bool = True
    # UI hint: the setting only matters if this other setting is set
    depends_on: Optional[str] = None
    # PATH settings: the folder must exist
    must_exist: bool = False
    # Defaults of previous versions. A stored value equal to one of them is treated as
    # not customized, so that the current default applies.
    legacy_defaults: Tuple[Any, ...] = ()
    # brings a value into a form in which it can be compared to a default
    comparable: Optional[Callable[[Any], Any]] = None

    def make_default(self):
        if self.default_factory is not None:
            return self.default_factory()
        return copy.deepcopy(self.default)

    def comparable_value(self, value):
        return self.comparable(value) if self.comparable is not None else value

    def get_choices(self) -> Dict[str, str]:
        return dict(self.choices()) if self.choices is not None else {}

    def parse(self, raw):
        """Convert a value from config.yml or the settings form to this setting's type.

        Returns USE_DEFAULT for an empty value (None or blank text), raises
        InvalidSettingValue if the value is rejected."""
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            return USE_DEFAULT
        value = _KIND_PARSERS[self.kind](self, raw)
        if self.validate is not None:
            value = self.validate(value)
        return value


def _parse_bool(spec: SettingSpec, raw):
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        if raw.strip().lower() in _TRUE_STRINGS:
            return True
        if raw.strip().lower() in _FALSE_STRINGS:
            return False
    raise InvalidSettingValue(N_("Expected true or false, got %(value)s."), value=_display_value(raw))


def _check_bounds(spec: SettingSpec, value):
    too_small = spec.min_value is not None and value < spec.min_value
    too_large = spec.max_value is not None and value > spec.max_value
    if not (too_small or too_large):
        return value
    if spec.min_value is not None and spec.max_value is not None:
        raise InvalidSettingValue(
            N_("Must be between %(min)s and %(max)s."), min=spec.min_value, max=spec.max_value
        )
    if too_small:
        raise InvalidSettingValue(N_("Must be at least %(min)s."), min=spec.min_value)
    raise InvalidSettingValue(N_("Must be at most %(max)s."), max=spec.max_value)


def _parse_int(spec: SettingSpec, raw):
    value = None
    if isinstance(raw, int) and not isinstance(raw, bool):
        value = raw
    elif isinstance(raw, float) and raw.is_integer():
        value = int(raw)
    elif isinstance(raw, str):
        try:
            value = int(raw.strip())
        except ValueError:
            pass
    if value is None:
        raise InvalidSettingValue(N_("%(value)s is not a whole number."), value=_display_value(raw))
    return _check_bounds(spec, value)


def _parse_float(spec: SettingSpec, raw):
    value = None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        value = float(raw)
    elif isinstance(raw, str):
        try:
            value = float(raw.strip())
        except ValueError:
            pass
    if value is None or value != value:  # NaN
        raise InvalidSettingValue(N_("%(value)s is not a number."), value=_display_value(raw))
    return _check_bounds(spec, value)


def _parse_str(spec: SettingSpec, raw):
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return str(raw)
    raise InvalidSettingValue(N_("Expected text, got %(value)s."), value=_display_value(raw))


def _parse_path(spec: SettingSpec, raw):
    if not isinstance(raw, (str, os.PathLike)):
        raise InvalidSettingValue(N_("Expected a folder path, got %(value)s."), value=_display_value(raw))
    path = pathlib.Path(raw).expanduser()
    if path == DEFAULT_SETTINGS.get(spec.key):
        # the detected default is always accepted, even if it does not exist (yet)
        return path
    if spec.must_exist and not path.is_dir():
        if path.exists():
            raise InvalidSettingValue(N_("%(value)s is not a folder."), value=str(path))
        raise InvalidSettingValue(N_("The folder %(value)s does not exist."), value=str(path))
    if path.exists() and not path.is_dir():
        raise InvalidSettingValue(N_("%(value)s is not a folder."), value=str(path))
    return path


def _parse_choice(spec: SettingSpec, raw):
    value = _parse_str(spec, raw)
    if spec.validate is None and value not in spec.get_choices():
        raise InvalidSettingValue(N_("%(value)s is not one of the available options."), value=_display_value(raw))
    return value


def _parse_structured(spec: SettingSpec, raw):
    # the shape is checked by the setting's validate function
    return copy.deepcopy(raw)


_KIND_PARSERS = {
    BOOL: _parse_bool,
    INT: _parse_int,
    FLOAT: _parse_float,
    STR: _parse_str,
    PATH: _parse_path,
    CHOICE: _parse_choice,
    STRUCTURED: _parse_structured,
}


def _not_one_of_the_options(value):
    return InvalidSettingValue(N_("%(value)s is not one of the available options."), value=_display_value(value))


def _validate_dashboard_language(value):
    locale = i18n.normalize_dashboard_language(value)
    if locale is None:
        raise _not_one_of_the_options(value)
    return locale


def _validate_stellaris_language(value):
    if value in STELLARIS_LANGUAGES or _STELLARIS_LANGUAGE_PATTERN.match(value):
        return value
    raise InvalidSettingValue(
        N_("%(value)s is not a Stellaris language key, such as l_english."), value=_display_value(value)
    )


def _validate_log_level(value):
    if value.upper() not in LOG_LEVELS:
        raise _not_one_of_the_options(value)
    return value.upper()


def _validate_host(value):
    if any(c.isspace() for c in value):
        raise InvalidSettingValue(N_("%(value)s is not a valid host name or address."), value=_display_value(value))
    return value


def _stellaris_language_choices():
    return {
        key: i18n.LANGUAGE_NAMES.get(i18n.STELLARIS_LANGUAGE_TO_LOCALE.get(key), key)
        for key in STELLARIS_LANGUAGES
    }


def _dashboard_language_choices():
    # the name of AUTO is translated by the settings page
    return {i18n.AUTO: i18n.AUTO, **i18n.LANGUAGE_NAMES}


def _log_level_choices():
    return {level: level for level in LOG_LEVELS}


def _comparable_tab_layout(value):
    """
    For tab layouts, the graph list of the Markets tab is blanked, because it is filled
    dynamically from the market resources and is ignored when applying the settings.
    The tab order and the order of the graphs in each tab are kept, because the
    dashboard displays them in that order (so a reordered layout is a customization).
    """
    if isinstance(value, dict):
        return [(tab, [] if tab == MARKET_TAB else graphs) for tab, graphs in value.items()]
    return value


def _validate_tab_layout(layout_dict):
    if not isinstance(layout_dict, dict):
        raise InvalidSettingValue(N_("Expected a mapping of tab names to lists of graph IDs."))
    processed = {}
    for tab, plot_list in layout_dict.items():
        if tab == GALAXY_MAP_TAB:
            logger.warning(f"Ignoring tab {tab}, it is reserved for the galaxy map")
            continue
        if tab == MARKET_TAB:
            if plot_list:
                logger.warning(f"Ignoring values for tab {tab}, it is filled dynamically")
            processed[tab] = []
            continue
        if not isinstance(plot_list, list):
            logger.warning(f"Ignoring invalid graph list for tab {tab}")
            continue
        graphs = processed.setdefault(tab, [])
        for g in plot_list:
            if not isinstance(g, str):
                logger.warning(f"Ignoring invalid graph ID {g}")
                continue
            graphs.append(g)
    return processed


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_market_resources(resources):
    if not isinstance(resources, list):
        raise InvalidSettingValue(N_("Expected a list of resources."))
    for entry in resources:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("name"), str)
            or "base_price" not in entry
            or not (entry["base_price"] is None or _is_number(entry["base_price"]))
        ):
            raise InvalidSettingValue(
                N_("Every resource needs a name and a base_price (a number, or null if it cannot be traded), got %(value)s."),
                value=_display_value(entry),
            )
    return resources


_GAME_DATE_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")


def _validate_market_fee(fees):
    if not isinstance(fees, list):
        raise InvalidSettingValue(N_("Expected a list of market fees."))
    for entry in fees:
        if (
            not isinstance(entry, dict)
            or not _GAME_DATE_PATTERN.match(str(entry.get("date", "")))
            or not _is_number(entry.get("fee"))
        ):
            raise InvalidSettingValue(
                N_("Every market fee needs a date (like 2200.01.01) and a fee (like 0.3), got %(value)s."),
                value=_display_value(entry),
            )
    return fees


# Sections of the settings page, in display order. The labels are in dashboard_app/settings.py.
CATEGORIES = ("game_files", "visibility", "multiplayer", "import", "interface", "server")


def _settings(*specs: SettingSpec) -> Dict[str, SettingSpec]:
    return {spec.key: spec for spec in specs}


# All settings, in the order in which the settings page shows them.
SETTINGS: Dict[str, SettingSpec] = _settings(
    # --- game files
    SettingSpec(
        "save_file_path", PATH, default_factory=_get_default_save_path,
        category="game_files", must_exist=True, requires_restart=True,
    ),
    SettingSpec(
        "stellaris_user_data_path", PATH, default_factory=_get_default_stellaris_user_data_path,
        category="game_files", must_exist=True, requires_restart=True,
    ),
    SettingSpec(
        "stellaris_install_path", PATH, default_factory=_get_default_stellaris_install_path,
        category="game_files", must_exist=True, requires_restart=True,
    ),
    SettingSpec(
        "stellaris_language", CHOICE, default="l_english", category="game_files",
        choices=_stellaris_language_choices, validate=_validate_stellaris_language,
        requires_restart=True,
    ),
    # --- visibility
    SettingSpec("show_everything", BOOL, default=False, category="visibility"),
    SettingSpec("show_all_country_types", BOOL, default=False, category="visibility"),
    SettingSpec("filter_events_by_type", BOOL, default=True, category="visibility"),
    # --- multiplayer
    SettingSpec("mp_username", STR, default="", category="multiplayer"),
    SettingSpec("hide_other_players", BOOL, default=True, category="multiplayer", depends_on="mp_username"),
    # --- importing saves
    SettingSpec("skip_saves", INT, default=0, category="import", min_value=0, max_value=100),
    SettingSpec("keep_failed_saves", BOOL, default=True, category="import"),
    SettingSpec(
        "failed_saves_max_size_mb", INT, default=1000, category="import", min_value=1,
        advanced=True, depends_on="keep_failed_saves",
    ),
    SettingSpec("backup_saves", BOOL, default=False, category="import"),
    SettingSpec(
        "threads", INT, default=1, category="import", min_value=1,
        requires_restart=True, advanced=True,
    ),
    # --- interface
    SettingSpec(
        "dashboard_language", CHOICE, default=i18n.AUTO, category="interface",
        choices=_dashboard_language_choices, validate=_validate_dashboard_language,
    ),
    SettingSpec("plot_height", INT, default=640, category="interface", min_value=300, max_value=2000),
    SettingSpec("plot_time_resolution", INT, default=500, category="interface", min_value=0, max_value=10000),
    SettingSpec("check_version", BOOL, default=True, category="interface"),
    # open the dashboard in the browser when it is started (not from the CLI)
    SettingSpec("open_browser", BOOL, default=False, category="interface"),
    SettingSpec(
        "tab_layout", STRUCTURED, default=DEFAULT_TAB_LAYOUT, category="interface", in_ui=False,
        validate=_validate_tab_layout, comparable=_comparable_tab_layout,
        legacy_defaults=legacy_config_defaults.LEGACY_TAB_LAYOUTS,
    ),
    SettingSpec(
        "market_resources", STRUCTURED, default=DEFAULT_MARKET_RESOURCES, category="interface",
        in_ui=False, validate=_validate_market_resources,
        legacy_defaults=legacy_config_defaults.LEGACY_MARKET_RESOURCES,
    ),
    SettingSpec(
        "market_fee", STRUCTURED, default=DEFAULT_MARKET_FEE, category="interface",
        in_ui=False, validate=_validate_market_fee,
    ),
    # --- server
    SettingSpec(
        "host", STR, default="127.0.0.1", category="server", validate=_validate_host,
        requires_restart=True, advanced=True,
    ),
    SettingSpec(
        "port", INT, default=28053, category="server", min_value=1024, max_value=65535,
        requires_restart=True, advanced=True,
    ),
    SettingSpec(
        "log_level", CHOICE, default="INFO", category="server", choices=_log_level_choices,
        validate=_validate_log_level, advanced=True,
    ),
    SettingSpec("log_to_file", BOOL, default=False, category="server", advanced=True),
    SettingSpec(
        "base_output_path", PATH, default_factory=_get_default_base_output_path,
        category="server", in_ui=False, requires_restart=True,
    ),
    SettingSpec("production", BOOL, default=False, category="server", in_ui=False),
)

# Runtime-only state, which is reset on every start and never written to config.yml.
RUNTIME_STATE_DEFAULTS = dict(
    debug_mode=False,
    # toggled from the graph pages
    normalize_stacked_plots=False,
)

# The default of each setting. Detected paths are resolved once, at import.
DEFAULT_SETTINGS: Dict[str, Any] = {key: spec.make_default() for key, spec in SETTINGS.items()}


def default_value(key: str):
    """A fresh copy of the default value of a setting."""
    return copy.deepcopy(DEFAULT_SETTINGS[key])


def _comparable_setting(key, value):
    """Bring a setting into a form in which it can be compared to a default value."""
    return SETTINGS[key].comparable_value(value)


def _is_legacy_default(key, value):
    """True if value was the default for this setting in some previous version."""
    value = _comparable_setting(key, value)
    return any(value == _comparable_setting(key, legacy) for legacy in SETTINGS[key].legacy_defaults)


def _is_current_default(key, value):
    return _comparable_setting(key, value) == _comparable_setting(key, DEFAULT_SETTINGS[key])


def _to_yaml_value(key, value):
    return str(value) if SETTINGS[key].kind == PATH else value


SETTINGS_FILE_HEADER = """\
# Settings of the Stellaris Dashboard. This file is managed by the dashboard: it is
# rewritten when you apply settings on the settings page, and comments are not kept.
# Only settings that differ from their default are listed. Settings that are not on
# the settings page (tab_layout, market_resources, market_fee, ...) are described in
# the README: https://github.com/eliasdoehne/stellaris-dashboard#readme
"""

# Where a settings dict comes from: config.yml, or the settings page.
SOURCE_FILE = "file"
SOURCE_FORM = "form"


@dataclasses.dataclass(frozen=True)
class RejectedSetting:
    """A setting value that was rejected. Unpacks as (key, raw_value, reason)."""

    key: str
    raw_value: Any
    # English, see InvalidSettingValue
    reason: str
    reason_template: str = ""
    reason_params: Dict[str, Any] = dataclasses.field(default_factory=dict)

    def __iter__(self) -> Iterator:
        return iter((self.key, self.raw_value, self.reason))

    def translated_reason(self) -> str:
        """The reason in the dashboard's language."""
        if not self.reason_template:
            return self.reason
        return i18n.gettext(self.reason_template, **self.reason_params)

    @classmethod
    def from_error(cls, key, raw_value, error: ValueError) -> "RejectedSetting":
        if isinstance(error, InvalidSettingValue):
            return cls(key, raw_value, str(error), error.reason, dict(error.params))
        return cls(key, raw_value, str(error))


@dataclasses.dataclass
class ApplyResult:
    """What applying a dict of settings did."""

    # keys whose value changed, in the order they were applied
    changed: List[str] = dataclasses.field(default_factory=list)
    # rejected values: the previous value (from the settings form) or the default (from config.yml) was kept
    rejected: List[RejectedSetting] = dataclasses.field(default_factory=list)
    # unknown or removed keys
    ignored: List[str] = dataclasses.field(default_factory=list)

    @property
    def restart_required(self) -> List[str]:
        """Changed settings that only take effect after restarting the dashboard."""
        return [key for key in self.changed if SETTINGS[key].requires_restart]

    @property
    def ok(self) -> bool:
        return not self.rejected


def settings_from_form(form: Mapping[str, Any]) -> Dict[str, Any]:
    """The settings submitted by the settings page form.

    Only settings shown in the settings page (in_ui) are taken from the form. The form
    must contain all of them: an unchecked checkbox is not submitted, so a missing BOOL
    setting means False."""
    settings = {}
    for key, spec in SETTINGS.items():
        if not spec.in_ui:
            continue
        if key in form:
            settings[key] = form[key]
        elif spec.kind == BOOL:
            settings[key] = False
    return settings


class Config:
    """Stores the settings for the dashboard.

    Every setting of SETTINGS is a plain attribute, initialized to its default. So is the
    runtime-only state of RUNTIME_STATE_DEFAULTS, which is never persisted.
    """

    def __init__(self, **settings):
        for key in SETTINGS:
            setattr(self, key, default_value(key))
        for key, value in RUNTIME_STATE_DEFAULTS.items():
            setattr(self, key, value)
        for key, value in settings.items():
            if key not in SETTINGS and key not in RUNTIME_STATE_DEFAULTS:
                raise TypeError(f"Unknown setting {key}")
            setattr(self, key, value)

    def __eq__(self, other):
        return isinstance(other, Config) and vars(self) == vars(other)

    def __repr__(self):
        return f"Config({', '.join(f'{k}={v!r}' for k, v in vars(self).items())})"

    def apply_dict(self, settings_dict: Mapping[str, Any], source: str = SOURCE_FORM) -> ApplyResult:
        """Validate and apply settings. Keys not in settings_dict are left unchanged.

        An invalid value is rejected and reported in the result. From config.yml
        (SOURCE_FILE), the default is used instead; from the settings page
        (SOURCE_FORM), the previous value is kept. Unknown and removed keys are ignored.
        """
        logger.info("Updating settings")
        result = ApplyResult()
        for key, raw in settings_dict.items():
            if key in REMOVED_SETTINGS:
                logger.warning(f"Ignoring setting {key}: it is no longer used and can be removed from the settings file.")
                result.ignored.append(key)
                continue
            spec = SETTINGS.get(key)
            if spec is None:
                logger.info(f"Ignoring unknown setting {key} with value {raw!r}.")
                result.ignored.append(key)
                continue
            old_val = getattr(self, key)
            try:
                val = spec.parse(raw)
            except ValueError as error:
                rejected = RejectedSetting.from_error(key, raw, error)
                result.rejected.append(rejected)
                if source == SOURCE_FILE:
                    val = default_value(key)
                    logger.warning(
                        f"Invalid value {_display_value(raw)} for setting {key}: {rejected.reason} "
                        f"Using the default {_display_value(_to_yaml_value(key, val))} instead."
                    )
                else:
                    logger.warning(
                        f"Invalid value {_display_value(raw)} for setting {key}: {rejected.reason} "
                        f"Keeping {_display_value(_to_yaml_value(key, old_val))}."
                    )
                    continue
            if val is USE_DEFAULT:
                val = default_value(key)
            setattr(self, key, val)
            if val != old_val:
                result.changed.append(key)
                logger.info(
                    f"Updated setting {key.ljust(28)} {str(old_val).rjust(8)} -> {str(val).ljust(8)}"
                )
        return result

    def reset_to_defaults(self, keys: Optional[Iterable[str]] = None) -> ApplyResult:
        """Reset the given settings (default: all) to their defaults."""
        result = ApplyResult()
        for key in SETTINGS if keys is None else keys:
            if key not in SETTINGS:
                result.ignored.append(key)
                continue
            val = default_value(key)
            if getattr(self, key) != val:
                result.changed.append(key)
            setattr(self, key, val)
        return result

    def is_customized(self, key: str) -> bool:
        """True if a setting differs from its current default (and from the defaults of earlier versions)."""
        value = getattr(self, key)
        return not (_is_current_default(key, value) or _is_legacy_default(key, value))

    def write_to_file(self, path: Optional[pathlib.Path] = None):
        """Save the customized settings to config.yml (or path), replacing the file atomically.

        Only settings that differ from their current default are written, so that a
        changed default reaches every install that did not customize the setting."""
        fname = path or settings_file_path()
        if fname.exists() and not fname.is_file():
            raise ValueError(f"Settings file {fname} exists and is not a file!")
        logger.info(f"Writing settings to {fname}")
        settings_dict = self.get_persisted_dict()
        content = SETTINGS_FILE_HEADER
        if settings_dict:
            content += yaml.dump(settings_dict, default_flow_style=False, sort_keys=False, allow_unicode=True)
        _keep_copy_of_unreadable_settings_file(fname)
        fname.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=fname.parent, prefix=f".{fname.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.chmod(tmp_name, fname.stat().st_mode if fname.exists() else 0o644)
            os.replace(tmp_name, fname)
        except BaseException:
            pathlib.Path(tmp_name).unlink(missing_ok=True)
            raise

    def get_persisted_dict(self) -> Dict[str, Any]:
        """The customized settings, as written to config.yml."""
        return {
            key: _to_yaml_value(key, getattr(self, key))
            for key, spec in SETTINGS.items()
            if spec.persisted and self.is_customized(key)
        }

    def get_dict(self) -> Dict[str, Any]:
        """All persisted settings, as written to config.yml."""
        return {
            key: _to_yaml_value(key, getattr(self, key))
            for key, spec in SETTINGS.items()
            if spec.persisted
        }

    def get_adjustable_settings_dict(self) -> Dict[str, Any]:
        """The current value of every setting."""
        return {key: getattr(self, key) for key in SETTINGS}

    def __str__(self):
        lines = [
            "Configuration:",
            f"  save_file_path: {repr(self.save_file_path)}",
            f"  base_output_path: {repr(self.base_output_path)}",
            f"  threads: {repr(self.threads)}",
            f"  show_everything: {repr(self.show_everything)}",
            f"  hide_other_players: {repr(self.hide_other_players)}",
            f"  show_all_country_types: {repr(self.show_all_country_types)}",
        ]
        return "\n".join(lines)

    @property
    def db_path(self) -> pathlib.Path:
        path = self.base_output_path / "db/"
        if not path.exists():
            # the dashboard and the import worker process can both create it
            path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def game_data_dirs(self):
        dlc_load_path = self.stellaris_user_data_path / "dlc_load.json"
        try:
            with open(dlc_load_path, "r") as f:
                dlc_load = json.load(f)
                mod_descriptor_paths = [
                    self.stellaris_user_data_path / relative_mod_descriptor_path
                    for relative_mod_descriptor_path in dlc_load.get("enabled_mods", [])
                ]
                mod_data_paths = [
                    _get_mod_data_dir(descriptor_path)
                    for descriptor_path in mod_descriptor_paths
                    if descriptor_path.exists()
                ]
                return [self.stellaris_install_path] + [
                    mod_data_path
                    for mod_data_path in mod_data_paths
                    if mod_data_path is not None
                ]
        except Exception as e:
            logger.warning(f"Failed to read enabled mods from {dlc_load_path}. Using only base game data.")
            logger.warning(e)
            return [self.stellaris_install_path]

    @property
    def localization_files(self):
        YAML_SUFFIXES = ("yaml", "yml")
        files = list(
            path
            for path in itertools.chain(
                *(
                    (data_dir / "localisation").glob(f"**/*.{yaml_suffix}")
                    for data_dir in self.game_data_dirs
                    for yaml_suffix in YAML_SUFFIXES
                )
            )
            if _localization_file_matches_language(path, self.stellaris_language)
        )
        logger.info(
            f"Loaded {len(files)} localization files from {self.game_data_dirs}"
        )
        return files
    
    
    @property
    def save_file_path_is_valid(self):
        return folder_looks_right("save_file_path", self.save_file_path)

    @property
    def stellaris_install_path_is_valid(self):
        return folder_looks_right("stellaris_install_path", self.stellaris_install_path)

    @property
    def stellaris_user_data_path_is_valid(self):
        return folder_looks_right("stellaris_user_data_path", self.stellaris_user_data_path)


def _contains_save_files(path: pathlib.Path) -> bool:
    # a folder per game, each with .sav files
    return next(path.glob("*/*.sav"), None) is not None


def _contains_game_files(path: pathlib.Path) -> bool:
    return (path / "localisation").is_dir() and (path / "flags/colors.txt").is_file()


def _contains_user_data(path: pathlib.Path) -> bool:
    return (path / "dlc_load.json").is_file()


# How to tell that a game folder setting points to the right folder (not just to any folder).
_FOLDER_CHECKS: Dict[str, Callable[[pathlib.Path], bool]] = {
    "save_file_path": _contains_save_files,
    "stellaris_install_path": _contains_game_files,
    "stellaris_user_data_path": _contains_user_data,
}


def has_folder_check(key: str) -> bool:
    return key in _FOLDER_CHECKS


def folder_looks_right(key: str, path: Optional[pathlib.Path]) -> bool:
    """True if path is a folder with the contents expected for the folder setting key.

    Works for any candidate path, not only the saved one, so that the settings page
    can check a path before it is saved."""
    if path is None:
        return False
    try:
        return path.is_dir() and _FOLDER_CHECKS[key](path)
    except (OSError, ValueError):  # e.g. no permission, or a null byte in the path
        return False


@dataclasses.dataclass(frozen=True)
class FolderCheck:
    """The result of check_folder. Says only whether the folder would be accepted and looks right."""

    # the folder that would be used (the default for a blank value); None if rejected
    path: Optional[pathlib.Path]
    # why the value would be rejected when saved, or None
    rejected: Optional[RejectedSetting]
    looks_right: bool


def check_folder(key: str, raw) -> FolderCheck:
    """Check a candidate value for a folder setting, without applying it.

    Uses the same validation as saving the setting, then the setting's folder check."""
    spec = SETTINGS[key]
    try:
        path = spec.parse(raw)
    except ValueError as error:
        return FolderCheck(None, RejectedSetting.from_error(key, raw, error), False)
    if path is USE_DEFAULT:
        path = default_value(key)
    return FolderCheck(path, None, folder_looks_right(key, path))


def _get_mod_data_dir(mod_descriptor_path: pathlib.Path):
    try:
        with open(mod_descriptor_path, "r") as f:
            for line in f:
                match = re.match(r'^\s*path\s*=\s*"(.*)"\s*$', line)
                if match:
                    return pathlib.Path(match.group(1))
    except Exception as e:
        logger.warning(f"Failed to read mod path from {mod_descriptor_path}")
        logger.warning(e)
    return None


def _localization_file_matches_language(file_path: pathlib.Path, language: str):
    with open(file_path, "r", encoding="utf-8") as f:
        first_line = f.readline()
        match = re.match(rf"^\ufeff\s*{language}\s*:\s*$", first_line)
        return match is not None


def _read_settings_file(settings_file: pathlib.Path) -> Dict[str, Any]:
    """The settings stored in config.yml; empty if it does not exist or cannot be read."""
    if not settings_file.is_file():
        return {}
    logger.info(f"Reading settings from {settings_file}...")
    try:
        with open(settings_file, "r", encoding="utf-8") as f:
            file_settings = yaml.load(f, Loader=yaml.SafeLoader)
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
        logger.error(f"Could not read the settings file {settings_file}, using the default settings: {error}")
        _UNREADABLE_SETTINGS_FILES.add(settings_file)
        return {}
    if file_settings is None:
        return {}
    if not isinstance(file_settings, dict):
        logger.error(f"The settings file {settings_file} does not contain a mapping of settings, using the default settings.")
        _UNREADABLE_SETTINGS_FILES.add(settings_file)
        return {}
    for key in list(file_settings):
        if key in SETTINGS and SETTINGS[key].legacy_defaults and _is_legacy_default(key, file_settings[key]):
            logger.info(
                f"Ignoring stored setting {key}: it is an outdated default, "
                f"using the current default instead."
            )
            del file_settings[key]
    return file_settings


# settings files that could not be read; they are kept as a copy before they are overwritten
_UNREADABLE_SETTINGS_FILES = set()


def _keep_copy_of_unreadable_settings_file(settings_file: pathlib.Path):
    if settings_file in _UNREADABLE_SETTINGS_FILES and settings_file.is_file():
        backup = settings_file.with_name(settings_file.name + ".unreadable")
        logger.warning(f"Keeping a copy of the unreadable settings file at {backup}")
        shutil.copy2(settings_file, backup)
    _UNREADABLE_SETTINGS_FILES.discard(settings_file)


def _apply_existing_settings(config: Config) -> ApplyResult:
    """Apply the settings stored in config.yml. Invalid values are replaced by their default."""
    return config.apply_dict(_read_settings_file(settings_file_path()), source=SOURCE_FILE)


# Environment variable with the location of the settings file, instead of ./config.yml
SETTINGS_FILE_ENV_VAR = "STELLARIS_DASHBOARD_CONFIG"


def settings_file_path() -> pathlib.Path:
    """The location of the settings file: $STELLARIS_DASHBOARD_CONFIG, or config.yml in the working directory."""
    from_env = os.environ.get(SETTINGS_FILE_ENV_VAR)
    if from_env:
        return pathlib.Path(from_env).expanduser().absolute()
    return pathlib.Path.cwd() / "config.yml"


def initialize():
    """Load the settings into CONFIG (once). Reads config.yml, but never writes files or creates folders."""
    global CONFIG, LOAD_RESULT
    if CONFIG is not None:
        return
    global logger
    initialize_logger()
    logger = logging.getLogger()
    # Spawned workers (Windows) re-import this module; keep them from repeating the startup logs.
    is_worker_process = multiprocessing.parent_process() is not None
    if is_worker_process:
        logger.setLevel(logging.WARNING)
    CONFIG = Config()
    LOAD_RESULT = _apply_existing_settings(CONFIG)
    configure_logger()


_log_file_handler: Optional[logging.Handler] = None


def configure_logger():
    """Apply the log_level and log_to_file settings. Can be called again after they changed."""
    global _log_file_handler
    level = LOG_LEVELS.get(CONFIG.log_level, logging.INFO)
    logger.setLevel(level)
    for h in logger.handlers:
        h.setLevel(level)
    if CONFIG.log_to_file and _log_file_handler is None:
        try:
            CONFIG.base_output_path.mkdir(parents=True, exist_ok=True)
            _log_file_handler = logging.FileHandler(CONFIG.base_output_path / "log.txt")
        except OSError as error:
            logger.warning(f"Could not open the log file in {CONFIG.base_output_path}: {error}")
            return
        _log_file_handler.setLevel(level)
        _log_file_handler.setFormatter(LOG_FORMAT)
        logger.addHandler(_log_file_handler)
    elif not CONFIG.log_to_file and _log_file_handler is not None:
        logger.removeHandler(_log_file_handler)
        _log_file_handler.close()
        _log_file_handler = None


initialize()
