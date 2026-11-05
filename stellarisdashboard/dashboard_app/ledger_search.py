"""Find a game's empires, leaders, wars, systems and planets by name.

Names are stored as localisation JSON, so they can't be matched in SQL: each
game gets an in-memory index of rendered names, built on the first search and
rebuilt once a new save was processed (or a setting that changes names or
visibility was edited). The topbar search box (assets/search.js) fetches the
results as an HTML fragment while the user types.
"""
import dataclasses
import logging
import threading
import time
import unicodedata
from typing import Dict, List, Optional, Tuple

import sqlalchemy
from flask import render_template, request, url_for
from sqlalchemy.orm import load_only, selectinload

from stellarisdashboard import config, datamodel, game_info
from stellarisdashboard.dashboard_app import flask_app

logger = logging.getLogger(__name__)

# (kind, group label), in display order for groups that match equally well
KINDS = [
    ("country", "Empires"),
    ("leader", "Leaders"),
    ("war", "Wars"),
    ("system", "Systems"),
    ("planet", "Planets"),
]
_KIND_ORDER = {kind: i for i, (kind, _) in enumerate(KINDS)}
PER_KIND = 5  # hits per group in the mixed view
PER_KIND_EXPANDED = 50  # hits when one group is expanded
MAX_QUERY_LENGTH = 80

_STAR_CLASSES = {"pc_black_hole", "pc_pulsar"}


@dataclasses.dataclass(frozen=True)
class Entry:
    kind: str
    entity_id: int
    name: str
    norm: str  # normalize(name)
    meta: str
    weight: tuple  # ranks equally good matches, lower first


@dataclasses.dataclass
class Hit:
    entry: Entry
    rank: int


def normalize(text: str) -> str:
    """Case- and accent-insensitive form of a name, same length as the input."""
    return "".join(_fold(c) for c in text)


def _fold(char: str) -> str:
    folded = "".join(c for c in unicodedata.normalize("NFKD", char) if not unicodedata.combining(c)).casefold()
    # keep one character per input character, so match positions map back to the name
    return folded[:1] if folded else char


def _word_start(norm: str, query: str) -> int:
    """Where query starts a word of norm other than the first, or -1."""
    start = norm.find(query, 1)
    while start != -1:
        if not norm[start - 1].isalnum():
            return start
        start = norm.find(query, start + 1)
    return -1


def _word_starts(norm: str, query: str) -> bool:
    return _word_start(norm, query) != -1


def match_rank(norm: str, query: str, tokens: List[str]) -> Optional[int]:
    """How well a normalized name matches a normalized query, lower is better.

    0 exact, 1 name prefix, 2 word prefix, 3 substring, 4 every query word
    starts some word of the name (in any order); None if it doesn't match."""
    if query in norm:
        if norm == query:
            return 0
        if norm.startswith(query):
            return 1
        return 2 if _word_starts(norm, query) else 3
    if len(tokens) > 1 and all(norm.startswith(t) or _word_starts(norm, t) for t in tokens):
        return 4
    return None


def search(entries: List[Entry], query: str, kind: Optional[str] = None) -> List[Tuple[str, List[Hit], int]]:
    """Matching entries as (kind, best hits, number of matches) groups, best group first."""
    query = normalize(" ".join(query.split()))[:MAX_QUERY_LENGTH]
    if not query:
        return []
    tokens = query.split(" ")
    by_kind: Dict[str, List[Hit]] = {}
    for entry in entries:
        if kind is not None and entry.kind != kind:
            continue
        # cheap pre-check: a match always contains the first query word
        if tokens[0] not in entry.norm:
            continue
        rank = match_rank(entry.norm, query, tokens)
        if rank is not None:
            by_kind.setdefault(entry.kind, []).append(Hit(entry, rank))
    limit = PER_KIND if kind is None else PER_KIND_EXPANDED
    groups = []
    for k, hits in by_kind.items():
        hits.sort(key=lambda h: (h.rank, h.entry.weight, len(h.entry.name), h.entry.norm))
        groups.append((k, hits[:limit], len(hits)))
    groups.sort(key=lambda g: (g[1][0].rank, _KIND_ORDER[g[0]]))
    return groups


def highlight(name: str, query: str) -> Tuple[str, str, str]:
    """name split around the query, or else its first word, preferring word starts: (before, match, after)."""
    query = normalize(" ".join(query.split()))
    norm = normalize(name)
    for part in (query, query.split(" ")[0]):
        if not part:
            continue
        start = 0 if norm.startswith(part) else _word_start(norm, part)
        start = start if start != -1 else norm.find(part)
        if start != -1:
            return name[:start], name[start:start + len(part)], name[start + len(part):]
    return name, "", ""


# ---- Index ------------------------------------------------------------------

_INDEXES: Dict[str, Tuple[tuple, List[Entry]]] = {}
_INDEX_LOCKS: Dict[str, threading.Lock] = {}
_LOCKS_LOCK = threading.Lock()


def _index_key(session) -> tuple:
    """Changes whenever the index would: a new save, or a setting that alters names or visibility."""
    count, latest = session.query(sqlalchemy.func.count(datamodel.GameState.gamestate_id),
                                  sqlalchemy.func.max(datamodel.GameState.date)).one()
    c = config.CONFIG
    return (count, latest, c.show_everything, c.hide_other_players, c.show_all_country_types,
            c.include_id_in_names, c.stellaris_language)


def get_index(game_id: str) -> List[Entry]:
    with datamodel.get_db_session(game_id) as session:
        key = _index_key(session)
        cached = _INDEXES.get(game_id)
        if cached and cached[0] == key:
            return cached[1]
        with _LOCKS_LOCK:
            lock = _INDEX_LOCKS.setdefault(game_id, threading.Lock())
        with lock:
            cached = _INDEXES.get(game_id)
            if cached and cached[0] == key:  # built by a concurrent request
                return cached[1]
            start = time.perf_counter()
            entries = _build_index(session)
            _INDEXES[game_id] = (key, entries)
            logger.info(f"Built the search index for {game_id}: {len(entries)} names in {time.perf_counter() - start:.2f}s")
            return entries


def _country_label(country: datamodel.Country) -> str:
    if country.is_player:
        return "Your empire"
    return country.rendered_type


def _build_index(session) -> List[Entry]:
    entries = []
    countries = session.query(datamodel.Country).all()
    hidden = {c.country_id for c in countries if c.is_hidden_country()}
    names = {c.country_id: c.rendered_name for c in countries}
    player = {c.country_id for c in countries if c.is_player}

    # scripted leaders come from nameless "faction" countries of the game's scripts
    unnamed = hidden | {c.country_id for c in countries if c.country_type == "faction"}

    def owner_name(country_id) -> Optional[str]:
        return names.get(country_id) if country_id is not None and country_id not in unnamed else None

    def add(kind, entity_id, name, meta, weight):
        if name:
            entries.append(Entry(kind, entity_id, name, normalize(name), meta, weight))

    # empires: the Codex's rules
    for c in countries:
        if c.country_id in hidden or (not config.CONFIG.show_all_country_types and not c.is_real_country()):
            continue
        add("country", c.country_id, names[c.country_id], _country_label(c), (not c.is_player, not c.is_real_country()))

    leaders = session.query(datamodel.Leader).options(load_only(
        datamodel.Leader.leader_id, datamodel.Leader.first_name, datamodel.Leader.second_name,
        datamodel.Leader.leader_class, datamodel.Leader.country_id, datamodel.Leader.creator_id,
        datamodel.Leader.is_active,
    ))
    for leader in leaders:
        owner = leader.country_id if leader.country_id is not None else leader.creator_id
        if owner in hidden:
            continue
        meta = [game_info.convert_id_to_name(leader.leader_class or "leader")]
        if owner_name(owner):
            meta.append(owner_name(owner))
        if not leader.is_active:
            meta.append("dead or retired")
        add("leader", leader.leader_id, leader.rendered_name, " · ".join(meta),
            (owner not in player, not leader.is_active))

    wars = session.query(datamodel.War).options(
        selectinload(datamodel.War.participants).selectinload(datamodel.WarParticipant.country)
    )
    for war in wars:
        sides = {p.country_id for p in war.participants}
        if sides and sides <= hidden:
            continue
        start = war.start_date or "?"
        meta = f"{start} – {war.end_date}" if war.end_date else f"since {start}"
        add("war", war.war_id, war.rendered_name, meta, (not sides & player, -(war.start_date_days or 0)))

    systems = session.query(datamodel.System.system_id, datamodel.System.name, datamodel.System.country_id)
    system_names = {}
    for system_id, name, country_id in systems:
        rendered = game_info.render_name(name)
        system_names[system_id] = rendered
        owner = owner_name(country_id)
        meta = owner or ("Unowned" if country_id is None else "System")
        add("system", system_id, rendered, meta, (country_id not in player, owner is None))

    planets = session.query(datamodel.Planet.planet_id, datamodel.Planet.planet_name, datamodel.Planet.planet_class,
                            datamodel.Planet.system_id, datamodel.Planet.colonized_date)
    for planet_id, name, planet_class, system_id, colonized in planets:
        if planet_class and (planet_class.endswith("_star") or planet_class in _STAR_CLASSES):
            continue  # stars share their system's name and have no events of their own
        kind = game_info.lookup_id_name(planet_class, remove_prefix="pc") if planet_class else "Destroyed"
        meta = f"{kind} · {system_names[system_id]}" if system_id in system_names else kind
        add("planet", planet_id, game_info.render_name(name), meta, (colonized is None,))
    return entries


# ---- Route ------------------------------------------------------------------

@flask_app.route("/history/<game_id>/search")
def ledger_search(game_id):
    query = request.args.get("q", "")[:MAX_QUERY_LENGTH]
    kind = request.args.get("kind")
    kind = kind if kind in _KIND_ORDER else None
    groups = []
    known = game_id in datamodel.get_known_games(game_id)
    if known and query.strip():
        groups = search(get_index(game_id), query, kind)
    elif known and "warm" in request.args:
        get_index(game_id)  # sent on focus, so the first keystroke finds the index built
    labels = dict(KINDS)
    return render_template(
        "_search_results.html",
        query=query,
        expanded=kind,
        groups=[
            dict(
                kind=k,
                label=labels[k],
                total=total,
                hits=[
                    dict(
                        url=url_for("history_page", game_id=game_id, **{k: h.entry.entity_id}),
                        parts=highlight(h.entry.name, query),
                        meta=h.entry.meta,
                    )
                    for h in hits
                ],
            )
            for k, hits, total in groups
        ],
    )
