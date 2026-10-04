"""
Helpers that hide differences between Stellaris save format versions.

Stellaris 4.x moved several pieces of data to new places in the gamestate:

- 4.0: districts became entries in a top-level ``districts`` table (with a ``level``),
  buildings moved into the top-level ``zones`` attached to districts, and ships reference
  their design through ``ship_design_implementation`` (designs have ``growth_stages``).
- 4.4: a top-level ``colony`` table was split off from planets. Pops, jobs, districts,
  stability etc. live on the colony, and several fields that used to hold planet IDs
  (``country.owned_planets``, ``country.capital``, ``sector.local_capital``,
  ``pop_group.planet``) now hold colony IDs.
- 4.5: pop groups are no longer split by ethic and faction. Each group stores how many
  of its pops hold each ethic instead of a single ``key.ethos.ethic``.

The functions in this module accept the parsed gamestate dictionary and work for
both the old and the new layouts, so the timeline processors can stay version-agnostic.
"""
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

NO_ETHOS = "ethic_no_ethos"

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


def resolve_colony_to_planet_id(gamestate: Dict[str, Any], colony_id) -> Optional[int]:
    """
    Map an ID taken from a field that holds colony IDs since 4.4 (``country.owned_planets``,
    ``country.capital``, ``sector.local_capital``, ``pop_group.planet``) to a planet ID.

    Before 4.4 these fields hold planet IDs, which are returned unchanged. Returns None if
    the colony is not carried by a planet (e.g. a nomad arkship, which is carried by a ship).
    """
    colonies = gamestate.get("colony")
    if not isinstance(colonies, dict):
        return colony_id
    colony = colonies.get(_as_id(colony_id))
    if not isinstance(colony, dict):
        return None
    carrier = colony.get("carrier")
    if isinstance(carrier, dict) and carrier.get("type") == "planet":
        return _as_id(carrier.get("reference"))
    return None


def get_planet(gamestate: Dict[str, Any], planet_id) -> Optional[Dict[str, Any]]:
    """
    Return the planet's dictionary. Since 4.4, the data of a colonized planet is split between
    the planet (physical data, modifiers) and its colony (pops, districts, governor, stability, ...).
    In that case, a merged view is returned, so callers can read either kind of field.
    """
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

    In 3.x, the planet repeats the ``district`` key once per district. Since 4.0, ``districts``
    lists IDs into the top-level ``districts`` table, and each entry's ``level`` is its count.
    """
    if "district" in planet_dict:
        return [d for d in _as_list(planet_dict["district"]) if isinstance(d, str)]

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

    In 3.x, the planet lists its building IDs in ``buildings``. In 4.0-4.3, buildings belong to
    zones, which belong to the planet's districts. Since 4.4, the colony also caches the building
    IDs in ``buildings_cache``.
    """
    if "buildings_cache" in planet_dict:
        building_ids = _as_list(planet_dict["buildings_cache"])
    elif "buildings" in planet_dict:
        building_ids = _as_list(planet_dict["buildings"])
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

    In 3.x, the ship references its design in ``ship_design`` and the design has a ``ship_size``.
    Since 4.0, the reference is ``ship_design_implementation = {design, growth_stage}`` and the
    design lists one ``ship_size`` per growth stage (bioships can grow into larger sizes).
    """
    designs = gamestate.get("ship_design", {})
    implementation = ship_dict.get("ship_design_implementation")
    if isinstance(implementation, dict):
        design = designs.get(_as_id(implementation.get("design")))
        if not isinstance(design, dict):
            return None
        stages = [s for s in _as_list(design.get("growth_stages")) if isinstance(s, dict)]
        if not stages:
            return design.get("ship_size")
        stage_index = implementation.get("growth_stage", 0)
        if not isinstance(stage_index, int):
            stage_index = 0
        return stages[min(max(stage_index, 0), len(stages) - 1)].get("ship_size")

    design = designs.get(ship_dict.get("ship_design"))
    if not isinstance(design, dict):
        return None
    return design.get("ship_size")


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

    Since 4.5, the group's pops can be spread over several factions. The layout of the 4.5
    ``factions`` field could not be confirmed yet (no 4.5 save with factions was available),
    so two plausible layouts are accepted, mirroring the 4.5 ``ethos`` field:
    ``{factions: [ids], pops: [counts]}`` and ``{<faction id>: <count>}``.
    Anything else counts as unaffiliated.
    """
    size = pop_group_dict.get("size", 0)
    key = pop_group_dict.get("key")
    if isinstance(key, dict) and "pop_faction" in key:
        return [(_as_id(key.get("pop_faction")), size)]

    factions = pop_group_dict.get("factions")
    pairs = []
    if isinstance(factions, dict):
        if "pops" in factions:
            pairs = list(zip(_as_list(factions.get("factions")), _as_list(factions.get("pops"))))
        else:
            pairs = list(factions.items())

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
