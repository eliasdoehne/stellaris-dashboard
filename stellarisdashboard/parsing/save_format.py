"""
Helpers that hide differences between Stellaris save format versions.

Stellaris 4.x moved several pieces of data to new places in the gamestate (pre-4.0 layouts
are not supported):

- 4.0: districts became entries in a top-level ``districts`` table (with a ``level``),
  buildings moved into the top-level ``zones`` attached to districts, and ships reference
  their design through ``ship_design_implementation`` (designs have ``growth_stages``).
- 4.4: a top-level ``colony`` table was split off from planets. Pops, jobs, districts,
  stability etc. live on the colony, and several fields that used to hold planet IDs
  (``country.owned_planets``, ``country.capital``, ``sector.local_capital``,
  ``pop_group.planet``) now hold colony IDs.
- 4.5: pop groups are no longer split by ethic and faction. Each group stores how many
  of its pops hold each ethic instead of a single ``key.ethos.ethic``, and likewise per
  faction instead of a single ``key.pop_faction``.
- 4.5 (nomads): a colony can be carried by a ship (an arkship) instead of a planet. These
  colonies have no entry in ``planets.planet``, so they are exposed as pseudo-planets with a
  negative planet ID (see ``arkship_planet_id``).

The functions in this module accept the parsed gamestate dictionary and work for
the 4.0-4.3, 4.4 and 4.5 layouts, so the timeline processors can stay version-agnostic.
"""
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

NO_ETHOS = "ethic_no_ethos"

# Planet class of the pseudo-planets that represent colonies carried by ships (nomad arkships);
# the game's own class for arkships, localised as "Arkship"
ARKSHIP_PLANET_CLASS = "pc_ark"

# Strategic resources that the game prefixes with "sr_" in budgets and stockpiles
_STRATEGIC_RESOURCE_PREFIX = "sr_"


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _as_id(value) -> Optional[int]:
    # newer saves wrap some references as {"type": ..., "reference": <id>}
    if isinstance(value, dict):
        value = value.get("reference")
    if isinstance(value, (int, float)):
        return int(value)
    return None


def arkship_planet_id(colony_id: int) -> int:
    """
    Planet ID of the pseudo-planet for a ship-carried colony. Real planet IDs are never
    negative, so the ID cannot collide with them, and it stays stable as long as the colony does.
    """
    return -1 - colony_id


def is_arkship_planet_id(planet_id) -> bool:
    return isinstance(planet_id, int) and planet_id < 0


def _get_ship_carried_colony(gamestate: Dict[str, Any], colony_id) -> Optional[Dict[str, Any]]:
    colonies = gamestate.get("colony")
    if not isinstance(colonies, dict):
        return None
    colony = colonies.get(_as_id(colony_id))
    if not isinstance(colony, dict):
        return None
    carrier = colony.get("carrier")
    if isinstance(carrier, dict) and carrier.get("type") == "ship":
        return colony
    return None


def _get_carrying_ship(gamestate: Dict[str, Any], colony: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    ships = gamestate.get("ships")
    if not isinstance(ships, dict):
        return None
    ship = ships.get(_as_id(colony["carrier"].get("reference")))
    return ship if isinstance(ship, dict) else None


def get_arkship_system_id(gamestate: Dict[str, Any], planet_id) -> Optional[int]:
    """
    Return the ID of the system that the ship carrying an arkship colony is in, or None if the
    ship is in transit (its coordinate origin is not a system) or cannot be found.
    """
    colony = _get_ship_carried_colony(gamestate, -1 - planet_id)
    ship = _get_carrying_ship(gamestate, colony) if colony is not None else None
    coordinate = ship.get("coordinate") if ship is not None else None
    system_id = _as_id(coordinate.get("origin")) if isinstance(coordinate, dict) else None
    systems = gamestate.get("galactic_object")
    if isinstance(systems, dict) and system_id in systems:
        return system_id
    return None


def resolve_colony_to_planet_id(gamestate: Dict[str, Any], colony_id) -> Optional[int]:
    """
    Map an ID taken from a field that holds colony IDs since 4.4 (``country.owned_planets``,
    ``country.capital``, ``sector.local_capital``, ``pop_group.planet``) to a planet ID.

    Without a ``colony`` table (4.0-4.3) these fields hold planet IDs, which are returned unchanged. If the colony is
    carried by a ship (a nomad arkship), the ID of its pseudo-planet is returned, see
    ``arkship_planet_id``. Returns None for unknown colonies.
    """
    colonies = gamestate.get("colony")
    if not isinstance(colonies, dict):
        return colony_id
    colony = colonies.get(_as_id(colony_id))
    if not isinstance(colony, dict):
        return None
    carrier = colony.get("carrier")
    if isinstance(carrier, dict):
        if carrier.get("type") == "planet":
            return _as_id(carrier.get("reference"))
        if carrier.get("type") == "ship":
            return arkship_planet_id(_as_id(colony_id))
    return None


def get_arkship_planet_ids(gamestate: Dict[str, Any]) -> List[int]:
    """Return the pseudo-planet IDs of all ship-carried colonies that a country owns."""
    result = set()
    for country in (gamestate.get("country") or {}).values():
        if not isinstance(country, dict):
            continue
        for colony_id in _as_list(country.get("owned_planets")):
            if _get_ship_carried_colony(gamestate, colony_id) is not None:
                result.add(arkship_planet_id(_as_id(colony_id)))
    return sorted(result)


def get_planet(gamestate: Dict[str, Any], planet_id) -> Optional[Dict[str, Any]]:
    """
    Return the planet's dictionary. Since 4.4, the data of a colonized planet is split between
    the planet (physical data, modifiers) and its colony (pops, districts, governor, stability, ...).
    In that case, a merged view is returned, so callers can read either kind of field.

    For the pseudo-planet of a ship-carried colony, the colony's data is returned, with the
    planet class ARKSHIP_PLANET_CLASS and the name of the carrying ship's fleet.
    """
    if is_arkship_planet_id(planet_id):
        colony_id = -1 - planet_id
        colony = _get_ship_carried_colony(gamestate, colony_id)
        if colony is None:
            return None
        result = {**colony, "planet_class": ARKSHIP_PLANET_CLASS}
        ship = _get_carrying_ship(gamestate, colony)
        # the colony has no colonize_date of its own, the carrying ship has
        if ship is not None and ship.get("colonize_date"):
            result["colonize_date"] = ship["colonize_date"]
        fleet = None
        if ship is not None:
            fleet = (gamestate.get("fleet") or {}).get(_as_id(ship.get("fleet")))
        if isinstance(fleet, dict) and "name" in fleet:
            result["name"] = fleet["name"]
        return result

    planet = gamestate.get("planets", {}).get("planet", {}).get(planet_id)
    if not isinstance(planet, dict):
        return planet
    colonies = gamestate.get("colony")
    colony_id = _as_id(planet.get("colony"))
    if not isinstance(colonies, dict) or colony_id is None:
        return planet
    colony = colonies.get(colony_id)
    if not isinstance(colony, dict):
        return planet
    return {**colony, **planet}


def get_district_types(gamestate: Dict[str, Any], planet_dict: Dict[str, Any]) -> List[str]:
    """
    Return one entry per district on the planet, e.g. ["district_city", "district_city", "district_mining"].

    ``districts`` lists IDs into the top-level ``districts`` table, and each entry's ``level`` is its count.
    """
    districts_table = gamestate.get("districts", {})
    result = []
    for district_id in _as_list(planet_dict.get("districts")):
        district = districts_table.get(district_id)
        if not isinstance(district, dict) or not isinstance(district.get("type"), str):
            continue
        level = district.get("level", 1)
        if not isinstance(level, int):
            level = 1
        result.extend([district["type"]] * max(level, 0))
    return result


def get_building_types(gamestate: Dict[str, Any], planet_dict: Dict[str, Any]) -> List[str]:
    """
    Return the type of each building on the planet.

    In 4.0-4.3, buildings belong to zones, which belong to the planet's districts. Since 4.4,
    the colony also caches the building IDs in ``buildings_cache``.
    """
    if "buildings_cache" in planet_dict:
        building_ids = _as_list(planet_dict["buildings_cache"])
    else:
        building_ids = _get_building_ids_from_zones(gamestate, planet_dict)

    buildings_table = gamestate.get("buildings", {})
    result = []
    for building_id in building_ids:
        building = buildings_table.get(building_id)
        if not isinstance(building, dict):
            continue
        result.append(building.get("type", "Unknown type"))
    return result


def _get_building_ids_from_zones(gamestate: Dict[str, Any], planet_dict: Dict[str, Any]) -> list:
    districts_table = gamestate.get("districts", {})
    zones_table = gamestate.get("zones", {})
    building_ids = []
    for district_id in _as_list(planet_dict.get("districts")):
        district = districts_table.get(district_id)
        if not isinstance(district, dict):
            continue
        for zone_id in _as_list(district.get("zones")):
            zone = zones_table.get(zone_id)
            if isinstance(zone, dict):
                building_ids.extend(_as_list(zone.get("buildings")))
    return building_ids


def get_ship_size(gamestate: Dict[str, Any], ship_dict: Dict[str, Any]) -> Optional[str]:
    """
    Return the ship size (e.g. "corvette", "science") of a ship.

    The ship references its design as ``ship_design_implementation = {design, growth_stage}`` and the
    design lists one ``ship_size`` per growth stage (bioships can grow into larger sizes).
    """
    implementation = ship_dict.get("ship_design_implementation")
    if not isinstance(implementation, dict):
        return None
    design = gamestate.get("ship_design", {}).get(_as_id(implementation.get("design")))
    if not isinstance(design, dict):
        return None
    stages = [s for s in _as_list(design.get("growth_stages")) if isinstance(s, dict)]
    if not stages:
        return design.get("ship_size")
    stage_index = implementation.get("growth_stage", 0)
    if not isinstance(stage_index, int):
        stage_index = 0
    return stages[min(max(stage_index, 0), len(stages) - 1)].get("ship_size")


def get_budget_value(values: Dict[str, Any], resource: str, default: float = 0.0):
    """
    Read a resource from a budget entry. Strategic resources such as living metal, zro and
    dark matter are stored with an "sr_" prefix (e.g. "sr_living_metal").
    """
    prefixed = _STRATEGIC_RESOURCE_PREFIX + resource
    if prefixed in values:
        return values[prefixed]
    return values.get(resource, default)


def get_pop_group_ethics(pop_group_dict: Dict[str, Any]) -> List[Tuple[str, int]]:
    """
    Return (ethic, pop count) pairs for a pop group. The counts sum to the group size.

    Before 4.5, each pop group has a single ethic in ``key.ethos.ethic``. Since 4.5, the group
    stores ``ethos = {ethics: [...], pops: [...]}`` with the number of pops holding each ethic.
    Pops without an ethic (e.g. gestalt drones) are reported as NO_ETHOS.
    """
    size = pop_group_dict.get("size", 0)
    ethos = pop_group_dict.get("ethos")
    if isinstance(ethos, dict):
        result = [
            (ethic, pops)
            for ethic, pops in zip(_as_list(ethos.get("ethics")), _as_list(ethos.get("pops")))
            if isinstance(ethic, str) and isinstance(pops, (int, float)) and pops > 0
        ]
        return _with_remainder(result, size, NO_ETHOS)

    key = pop_group_dict.get("key")
    key_ethos = key.get("ethos") if isinstance(key, dict) else None
    ethic = key_ethos.get("ethic") if isinstance(key_ethos, dict) else None
    if not isinstance(ethic, str):
        ethic = NO_ETHOS
    return [(ethic, size)]


def get_pop_group_factions(pop_group_dict: Dict[str, Any]) -> List[Tuple[Optional[int], int]]:
    """
    Return (faction ID, pop count) pairs for a pop group. The counts sum to the group size, and
    pops that are not in any faction are reported with a faction ID of None.

    Before 4.5, each pop group has at most one faction in ``key.pop_faction``.

    Since 4.5, the group's pops can be spread over several factions:
    ``factions = {objects: [pop faction IDs], pops: [pop count per faction]}``. The counts sum to
    at most the group size, and the rest are unaffiliated. Implausible data counts as unaffiliated.
    """
    size = pop_group_dict.get("size", 0)
    key = pop_group_dict.get("key")
    if isinstance(key, dict) and "pop_faction" in key:
        return [(_as_id(key.get("pop_faction")), size)]

    factions = pop_group_dict.get("factions")
    pairs = []
    if isinstance(factions, dict):
        pairs = list(zip(_as_list(factions.get("objects")), _as_list(factions.get("pops"))))

    result = [
        (faction_id, pops)
        for faction_id, pops in pairs
        if isinstance(faction_id, int) and faction_id >= 0 and isinstance(pops, (int, float)) and pops > 0
    ]
    if sum(pops for _, pops in result) > size:
        logger.debug("Ignoring unexpected faction data in pop group: %s", factions)
        result = []
    return _with_remainder(result, size, None)


def _with_remainder(pairs: list, size, remainder_key) -> list:
    remainder = size - sum(count for _, count in pairs)
    if remainder > 0 or not pairs:
        pairs.append((remainder_key, max(remainder, 0)))
    return pairs
