"""Galaxy-wide crises and events (end-game crises, Great Khan, Voidworm Plague, Fallen Empire
awakenings), read from the global flags and the country types of a gamestate."""
import dataclasses
from typing import Any, Iterator, Optional, Tuple

from stellarisdashboard import datamodel

# flag dates count hours from a fixed epoch; the game_started flag of a 2200.01.01 start is 62808000
_FLAG_EPOCH_DAYS = 2617000

EXTRADIMENSIONAL_TYPES = ("extradimensional", "extradimensional_2", "extradimensional_3")
SYNTH_QUEEN_TYPES = ("synth_queen_storm", "synth_queen", "awakened_synth_queen")
# country types of the end-game crises, in the order a generic stage event is attached to them
END_GAME_CRISIS_TYPES = EXTRADIMENSIONAL_TYPES + ("swarm", "ai_empire") + SYNTH_QUEEN_TYPES


@dataclasses.dataclass(frozen=True)
class GalaxyEventSignal:
    key: str  # stored as the event description
    flag: str  # the global flag marking the event
    country_types: Tuple[str, ...]  # the event is attached to a country of these types
    all_countries: bool = False  # attach to every matching country instead of the first
    # attach to the countries carrying this country flag (or UPSTART_AWAKENER) when there are any, else by type
    target: Optional[str] = None


# pseudo target: the countries that woke as an upstart, see _classify_awakening
UPSTART_AWAKENER = "upstart_awakener"
FIRST_SLEEPER = "sleepers_awake_first_sleeper"  # country flag of every empire that woke first (upstart or Guardian)
ANCIENT_RIVAL = "sleepers_awake_ancient_rival"  # country flag of the Fallen Empire chosen to be the awakener's rival
FORMED_LEAGUE = "formed_nonaligned_league"  # country flag of the founder of the League of Non-Aligned Powers
FALLEN_EMPIRE_TYPES = ("awakened_fallen_empire", "fallen_empire")

# Global flags set once by the game's event chains; none of these is removed again.
SIGNALS = (
    # extradimensional invaders, events/crisis_events_2.txt
    GalaxyEventSignal("unbidden_arrived", "extradimensional_invasion_happened", ("extradimensional",)),
    GalaxyEventSignal("aberrant_arrived", "extradimensional_second_portal", ("extradimensional_2",)),
    GalaxyEventSignal("vehement_arrived", "extradimensional_third_portal", ("extradimensional_3",)),
    GalaxyEventSignal("unbidden_portal_destroyed", "extradimensional_first_portal_destroyed", ("extradimensional",)),
    GalaxyEventSignal("aberrant_portal_destroyed", "extradimensional_second_portal_destroyed", ("extradimensional_2",)),
    GalaxyEventSignal("vehement_portal_destroyed", "extradimensional_third_portal_destroyed", ("extradimensional_3",)),
    GalaxyEventSignal("extradimensional_defeated", "extradimensional_invasion_defeated", EXTRADIMENSIONAL_TYPES),
    # Prethoryn Scourge, events/crisis_events_1.txt
    GalaxyEventSignal("prethoryn_arrived", "prethoryn_arrival", ("swarm",)),
    GalaxyEventSignal("prethoryn_defeated", "prethoryn_invasion_defeated", ("swarm",)),
    # Contingency, events/crisis_events_3.txt (ai_invasion_happened marks the hidden ghost signal years earlier;
    # ai_invasion_ongoing is set on activation and removed on defeat)
    GalaxyEventSignal("contingency_activated", "ai_invasion_ongoing", ("ai_empire",)),
    GalaxyEventSignal("contingency_defeated", "ai_invasion_defeated", ("ai_empire",)),
    # Cetana / Synth Queen, events/machine_age_crisis_events.txt
    GalaxyEventSignal("synth_queen_arrived", "synth_queen_happened", SYNTH_QUEEN_TYPES),
    GalaxyEventSignal("synth_queen_war", "synth_queen_war_started", SYNTH_QUEEN_TYPES),
    GalaxyEventSignal("synth_queen_victorious", "synth_queen_victorious", SYNTH_QUEEN_TYPES),
    GalaxyEventSignal("synth_queen_defeated", "synth_queen_defeated", SYNTH_QUEEN_TYPES),
    # Gray Tempest, events/gray_goo_events.txt (gray_goo_crisis_active is removed on defeat)
    GalaxyEventSignal("gray_tempest_arrived", "gray_goo_crisis_active", ("gray_goo",)),
    GalaxyEventSignal("gray_tempest_defeated", "graygoo_defeated", ("gray_goo",)),
    # Voidworm Plague, events/grand_archive_events.txt; the country is voidworms_crisis while it lasts
    GalaxyEventSignal("voidworm_plague_started", "voidworms_plague_crisis_started", ("voidworms_crisis", "voidworms")),
    GalaxyEventSignal("voidworm_plague_ended", "voidworms_plague_crisis_ended", ("voidworms", "voidworms_crisis")),
    # Great Khan, events/marauder_events.txt; the horde country is destroyed some months after the Khan dies
    GalaxyEventSignal("great_khan_rose", "marauder_crisis_started", ("awakened_marauders",)),
    GalaxyEventSignal("great_khan_defeated", "great_khan_defeated_once", ("awakened_marauders",)),
    GalaxyEventSignal("great_khan_died", "great_khan_dead", ("awakened_marauders",)),
    # War in Heaven, events/war_in_heaven_events.txt
    GalaxyEventSignal("war_in_heaven_started", "war_in_heaven_started", ("awakened_fallen_empire",), all_countries=True),
    GalaxyEventSignal("war_in_heaven_ended", "war_in_heaven_ended", ("awakened_fallen_empire",), all_countries=True),
    # the rival roll of an upstart awakening, events/fallen_empire_awakening_events.txt and war_in_heaven_events.txt
    GalaxyEventSignal("war_in_heaven_ruled_out", "no_war_in_heaven", FALLEN_EMPIRE_TYPES, target=UPSTART_AWAKENER, all_countries=True),
    GalaxyEventSignal("ancient_rival_woke", "sleepers_awake_rival_waking", ("awakened_fallen_empire",), target=ANCIENT_RIVAL, all_countries=True),
    # the League of Non-Aligned Powers is an existing federation that the game renames (see FederationProcessor)
    GalaxyEventSignal("nonaligned_league_formed", "war_in_heaven_nonaligned_league", (), target=FORMED_LEAGUE, all_countries=True),
    GalaxyEventSignal("nonaligned_league_war", "war_in_heaven_nonaligned_league_war", (), target=FORMED_LEAGUE, all_countries=True),
    # events/fallen_empire_events.txt; the first Fallen Empire to lose a war
    GalaxyEventSignal("fallen_empire_lost_war", "fallen_empire_lost_war", FALLEN_EMPIRE_TYPES, target="fallen_empire_lost_war", all_countries=True),
)

# crisis_stage_N is a generic global flag of the end-game crises (stage 1 is their arrival);
# it moves up and down with the crisis' share of the galaxy, so only the first time a stage is reached is kept
CRISIS_STAGES = (2, 3, 4)
# awakened empires: the upstart that woke first, a Guardian that woke against an end-game crisis, or one of unknown kind
FALLEN_EMPIRE_AWAKENED = "fallen_empire_awakened"
FALLEN_EMPIRE_AWAKENED_UPSTART = "fallen_empire_awakened_upstart"
FALLEN_EMPIRE_AWAKENED_GUARDIAN = "fallen_empire_awakened_guardian"
TYPED_AWAKENINGS = (FALLEN_EMPIRE_AWAKENED_UPSTART, FALLEN_EMPIRE_AWAKENED_GUARDIAN)
# country flags that mark an event on the country, dated from the flag itself
COUNTRY_FLAG_EVENTS = (("war_in_heaven_rival_chosen", ANCIENT_RIVAL),)
# country flag of an empire that became the crisis (crisis level 5) or was declared one by the Galactic Community;
# the empire stays a default country, and the flag is removed if the declaration is repealed
DECLARED_CRISIS = "declared_crisis"


@dataclasses.dataclass(frozen=True)
class GalaxyEvent:
    key: str
    date_days: int
    country_types: Tuple[str, ...]
    country_ids: Tuple[int, ...]  # matching countries in the gamestate, in country_types order
    all_countries: bool = False


def flag_date_days(value: Any) -> Optional[int]:
    """Days since 2200.01.01 of a flag's date, or None if it has none."""
    if isinstance(value, dict):
        value = value.get("flag_date")
    if not isinstance(value, int) or isinstance(value, bool):
        return None
    return value // 24 - _FLAG_EPOCH_DAYS


def _event_date(date_days: Optional[int], current_days: int) -> int:
    if date_days is None or not 0 <= date_days <= current_days:
        return current_days
    return date_days


def _countries_by_type(countries: Any) -> dict:
    by_type = {}
    if not isinstance(countries, dict):
        return by_type
    for country_id, country in sorted(countries.items()):
        if isinstance(country, dict):
            by_type.setdefault(country.get("type"), []).append(country_id)
    return by_type


def collect_galaxy_events(flags: Any, countries: Any, current_days: int) -> Iterator[GalaxyEvent]:
    """All galaxy events that the gamestate shows to have happened, dated from their flags."""
    flags = flags if isinstance(flags, dict) else {}
    by_type = _countries_by_type(countries)

    def matching(types):
        return tuple(cid for t in types for cid in by_type.get(t, []))

    def targeted(target):
        if target == UPSTART_AWAKENER:
            return _upstart_awakeners(by_type, countries, flags)
        return tuple(
            cid
            for cid, country in sorted(countries.items() if isinstance(countries, dict) else ())
            if isinstance(country, dict)
            and isinstance(country.get("flags"), dict)
            and target in country["flags"]
        )

    for signal in SIGNALS:
        if signal.flag in flags:
            ids = targeted(signal.target) if signal.target else ()
            if signal.target and not ids:
                ids = matching(signal.country_types)[:1]  # no carrier of the flag: the first matching empire
            yield GalaxyEvent(
                key=signal.key,
                date_days=_event_date(flag_date_days(flags[signal.flag]), current_days),
                country_types=signal.country_types,
                country_ids=ids or matching(signal.country_types),
                all_countries=signal.all_countries,
            )
    for stage in CRISIS_STAGES:
        flag = f"crisis_stage_{stage}"
        if flag in flags:
            yield GalaxyEvent(
                key=flag,
                date_days=_event_date(flag_date_days(flags[flag]), current_days),
                country_types=END_GAME_CRISIS_TYPES,
                country_ids=matching(END_GAME_CRISIS_TYPES),
            )
    for country_id, country in sorted(countries.items()) if isinstance(countries, dict) else ():
        country_flags = country.get("flags") if isinstance(country, dict) else None
        if not isinstance(country_flags, dict):
            continue
        for key, country_flag in ((DECLARED_CRISIS, DECLARED_CRISIS),) + COUNTRY_FLAG_EVENTS:
            if country_flag in country_flags:
                yield GalaxyEvent(
                    key=key,
                    date_days=_event_date(flag_date_days(country_flags[country_flag]), current_days),
                    country_types=(country.get("type"),),
                    country_ids=(country_id,),
                    all_countries=True,
                )
    for country_id in by_type.get("awakened_fallen_empire", []):
        key, date_days = _classify_awakening(countries[country_id], flags)
        if key is None:
            continue
        yield GalaxyEvent(
            key=key,
            date_days=_event_date(date_days, current_days),
            country_types=("awakened_fallen_empire",),
            country_ids=(country_id,),
            all_countries=True,
        )


def _classify_awakening(country: dict, flags: dict) -> Tuple[Optional[str], Optional[int]]:
    """The event key and date of an awakened empire's awakening; None if the rival's waking event covers it."""
    country_flags = country.get("flags") if isinstance(country.get("flags"), dict) else {}
    if FIRST_SLEEPER in country_flags:
        own = flag_date_days(country_flags[FIRST_SLEEPER])
        upstart = flag_date_days(flags.get("sleepers_awake_happened"))
        guardian = flag_date_days(flags.get("guardians_of_the_galaxy_happened"))
        # both can happen in one game; the country's flag was set together with its own global flag
        candidates = [
            (key, date) for key, date in ((FALLEN_EMPIRE_AWAKENED_UPSTART, upstart), (FALLEN_EMPIRE_AWAKENED_GUARDIAN, guardian))
            if date is not None
        ]
        if candidates and own is not None:
            return min(candidates, key=lambda c: abs(c[1] - own))[0], own
        if candidates:
            return candidates[0][0], candidates[0][1]
        return FALLEN_EMPIRE_AWAKENED, own
    if ANCIENT_RIVAL in country_flags and "sleepers_awake_rival_waking" in flags:
        return None, None
    # an awakened empire's type changed last when it awoke
    try:
        date_days = datamodel.date_to_days(country.get("last_changed_country_type"))
    except (AttributeError, ValueError):
        date_days = None
    return FALLEN_EMPIRE_AWAKENED, date_days


def _upstart_awakeners(by_type: dict, countries: dict, flags: dict) -> Tuple[int, ...]:
    return tuple(
        cid
        for cid in by_type.get("awakened_fallen_empire", [])
        if _classify_awakening(countries[cid], flags)[0] == FALLEN_EMPIRE_AWAKENED_UPSTART
    )
