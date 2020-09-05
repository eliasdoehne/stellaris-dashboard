"""This file contains the code for the flask server hosting the visualizations and the event ledger."""
import collections
import functools
import logging
from typing import Dict

import flask
from flask import render_template, request

from stellarisdashboard import config, datamodel, game_info
from stellarisdashboard.dashboard_app import flask_app, utils

logger = logging.getLogger(__name__)


@flask_app.route("/history")
@flask_app.route("/history/<game_id>")
@flask_app.route("/checkversion/<version>/history")
@flask_app.route("/checkversion/<version>/history/<game_id>")
def history_page(game_id="", version=None):
    show_old_version_notice = version is not None and utils.is_old_version(version)

    matches = datamodel.get_known_games(game_id)
    if not matches:
        logger.warning(f"Could not find a game matching {game_id}")
        return render_template("404_page.html", game_not_found=True, game_name=game_id)
    game_id = matches[0]
    games_dict = datamodel.get_available_games_dict()
    country = games_dict[game_id]["country_name"]

    event_filter = get_event_filter()

    with datamodel.get_db_session(game_id) as session:
        dict_builder = EventTemplateDictBuilder(session, game_id, event_filter)
        events, title, details, links = dict_builder.get_event_and_link_dicts()
        wars = dict_builder.get_war_list()
    return render_template(
        "history_page.html",
        game_name=game_id,
        country=country,
        wars=wars,
        events=events,
        details=details,
        links=links,
        title=title,
        is_filtered_page=not event_filter.is_empty_filter,
        show_old_version_notice=show_old_version_notice,
        version=utils.VERSION_ID,
        update_version_id=version,
    )


def get_event_filter() -> "EventFilter":
    country_id = request.args.get("country", None)
    leader_id = request.args.get("leader", None)
    system_id = request.args.get("system", None)
    war_id = request.args.get("war", None)
    planet_id = request.args.get("planet", None)
    min_date = request.args.get("min_date", float("-inf"))
    event_filter = EventFilter(
        min_date=min_date,
        country_filter=country_id,
        war_filter=war_id,
        leader_filter=leader_id,
        system_filter=system_id,
        planet_filter=planet_id,
    )
    return event_filter


class EventFilter:
    def __init__(
        self,
        min_date=float("-inf"),
        max_date=float("inf"),
        war_filter=None,
        country_filter=None,
        leader_filter=None,
        system_filter=None,
        planet_filter=None,
        faction_filter=None,
    ):
        self.min_date = float(min_date)
        self.max_date = float(max_date)
        self.war_filter = int(war_filter) if war_filter is not None else None
        self.country_filter = (
            int(country_filter) if country_filter is not None else None
        )
        self.leader_filter = int(leader_filter) if leader_filter is not None else None
        self.system_filter = int(system_filter) if system_filter is not None else None
        self.planet_filter = int(planet_filter) if planet_filter is not None else None
        self.faction_filter = (
            int(faction_filter) if faction_filter is not None else None
        )
        self.scope_threshold = None
        self._initialize_scope()

    @property
    def query_args_info(self):
        """
        Return the model class that is expected to be the "main" object according to the filter.
        For now, assume that only one of the filter classes is active at a time.
        :return:
        """
        if self.leader_filter is not None:
            return (
                datamodel.Leader,
                "leader",
                dict(leader_id=self.leader_filter),
                datamodel.Leader.leader_id.asc(),
            )
        elif self.system_filter is not None:
            return (
                datamodel.System,
                "system",
                dict(system_id=self.system_filter),
                datamodel.System.system_id.asc(),
            )
        elif self.planet_filter is not None:
            return (
                datamodel.Planet,
                "planet",
                dict(planet_id=self.planet_filter),
                datamodel.Planet.planet_id.asc(),
            )
        elif self.war_filter is not None:
            return (
                datamodel.War,
                "war",
                dict(war_id=self.war_filter),
                datamodel.War.war_id.asc(),
            )
        else:
            filter_dict = {}
            if self.country_filter is not None:
                filter_dict = dict(country_id=(self.country_filter))
            return (
                datamodel.Country,
                "country",
                filter_dict,
                datamodel.Country.country_id.asc(),
            )

    @property
    def is_empty_filter(self):
        return (
            self.country_filter is None
            and self.war_filter is None
            and self.system_filter is None
            and self.planet_filter is None
            and self.leader_filter is None
            and self.faction_filter is None
        )

    def include_event(self, event: datamodel.HistoricalEvent) -> bool:
        result = all(
            [
                self.min_date <= event.start_date_days <= self.max_date,
                self.country_filter is None or self.country_filter == event.country_id,
                event.event_type.scope >= self.scope_threshold,
            ]
        )
        if self.country_filter is not None:
            result &= self.country_filter in {
                c.country_id for c in event.involved_countries()
            }
        if self.leader_filter is not None:
            result &= event.leader_id == self.leader_filter
        if self.system_filter is not None:
            result &= event.system_id == self.system_filter
        if self.planet_filter is not None:
            result &= event.planet_id == self.planet_filter
        if self.faction_filter is not None:
            result &= event.faction_id == self.faction_filter
        return result

    def _initialize_scope(self):
        if config.CONFIG.filter_events_by_type:
            country_scope = (
                datamodel.HistoricalEventScope.country
                if self.country_filter is not None
                else float("inf")
            )
            leader_scope = (
                datamodel.HistoricalEventScope.leader
                if self.leader_filter is not None
                else float("inf")
            )
            system_scope = (
                datamodel.HistoricalEventScope.system
                if self.system_filter is not None
                else float("inf")
            )
            planet_scope = (
                datamodel.HistoricalEventScope.all
                if self.planet_filter is not None
                else float("inf")
            )
            war_scope = (
                datamodel.HistoricalEventScope.all
                if self.war_filter is not None
                else float("inf")
            )
            self.scope_threshold = min(
                [
                    datamodel.HistoricalEventScope.galaxy,
                    country_scope,
                    leader_scope,
                    system_scope,
                    planet_scope,
                    war_scope,
                ]
            )
        else:
            self.scope_threshold = datamodel.HistoricalEventScope.all


class EventTemplateDictBuilder:
    def __init__(self, db_session, game_id, event_filter=None):
        if event_filter is None:
            event_filter = EventFilter()
        self.event_filter = event_filter
        self.game_id = game_id
        self._session = db_session
        self._formatted_urls = None
        self._most_recent_date = None
        self._events = None
        self._details = None
        self._titles = None

    def get_event_and_link_dicts(self):
        self._formatted_urls = {}

        # the kind of object varies depending on the filter.
        (
            key_object_class,
            event_query_kwargs,
            key_obj_filter_dict,
            key_object_order_column,
        ) = self.event_filter.query_args_info

        self._most_recent_date = utils.get_most_recent_date(self._session)
        key_objects = (
            self._session.query(key_object_class)
            .filter_by(**key_obj_filter_dict)
            .order_by(key_object_order_column)
        )

        self._events = {}
        self._details = {}
        self._titles = {}

        for key in key_objects:
            event_list = (
                self._session.query(datamodel.HistoricalEvent)
                .order_by(
                    datamodel.HistoricalEvent.start_date_days.asc(),
                    datamodel.HistoricalEvent.event_type.asc(),
                )
                .filter_by(**{event_query_kwargs: key})
                .all()
            )
            self.collect_event_dicts(event_list, key)
        return self._events, self._titles, self._details, self._formatted_urls

    def collect_event_dicts(self, event_list, key_object):
        self._events[key_object] = []
        self._details[key_object] = self._get_details(key_object)
        self._titles[key_object] = self._get_title(key_object)
        self._formatted_urls[key_object] = self._get_url_for(key_object)
        for event in event_list:
            if not self.event_filter.include_event(event):
                continue
            if not config.CONFIG.show_everything and not event.event_is_known_to_player:
                continue
            if (
                event.country
                and any(c.is_other_player for c in event.involved_countries())
                and not event.event_is_known_to_player
            ):
                continue
            country_type = (
                event.country.country_type if event.country is not None else None
            )
            if not config.CONFIG.show_all_country_types and country_type not in [
                "default",
                "fallen_empire",
                "awakened_fallen_empire",
            ]:
                continue

            start = datamodel.days_to_date(event.start_date_days)
            end_date = None
            is_active = True
            if event.end_date_days is not None:
                end_date = datamodel.days_to_date(event.end_date_days)
                is_active = event.end_date_days >= self._most_recent_date
            event_dict = dict(
                country=event.country,
                start_date=start,
                is_active=is_active,
                end_date=end_date,
                event_type=str(event.event_type),
                war=event.war,
                leader=event.leader,
                system=event.system,
                planet=event.planet,
                faction=event.faction,
                target_country=event.target_country,
                description=event.description,
                fleet=event.fleet,
            )
            event_dict = {k: v for (k, v) in event_dict.items() if v is not None}

            if "planet" in event_dict and event_dict["system"] is None:
                event_dict["system"] = event_dict["planet"].system
            if "faction" in event_dict:
                event_dict["faction_type"] = event.faction.type
            if event.combat:
                event_dict.update(self._combat_dict(event.combat))

            self._events[key_object].append(event_dict)
            self._preformat_urls(event)
        if not self._events[key_object] and self.event_filter.is_empty_filter:
            del self._events[key_object]

    def get_war_list(self):
        if not self.event_filter.is_empty_filter:
            return []
        return [key for key in self._formatted_urls if isinstance(key, datamodel.War)]

    def _get_details(self, key) -> Dict[str, str]:
        if isinstance(key, datamodel.Country):
            return self.country_details(key)
        elif isinstance(key, datamodel.System):
            return self.system_details(key)
        elif isinstance(key, datamodel.Leader):
            return self.leader_details(key)
        elif isinstance(key, datamodel.War):
            return self.war_details(key)
        elif isinstance(key, datamodel.Planet):
            return self.planet_details(key)
        else:
            return {}

    def _preformat_urls(self, event):
        for country in event.involved_countries():
            self._formatted_urls[country] = self._get_url_for(country)
        if event.planet:
            self._formatted_urls[event.planet] = self._get_url_for(event.planet)
        if event.leader:
            self._formatted_urls[event.leader] = self._get_url_for(event.leader)
        if event.system:
            self._formatted_urls[event.system] = self._get_url_for(event.system)
        if event.war:
            self._formatted_urls[event.war] = self._get_url_for(event.war)

    def _get_title(self, key) -> str:
        if isinstance(key, datamodel.Country):
            return self._get_url_for(key, a_class="titlelink")
        elif isinstance(key, datamodel.System):
            return f"{key.get_name()} System"
        elif isinstance(key, datamodel.Leader):
            return key.get_name()
        elif isinstance(key, datamodel.War):
            return key.name
        elif isinstance(key, datamodel.Planet):
            return f"Planet {key.name}"
        else:
            return ""

    def _get_url_for(self, key, a_class="textlink"):
        if isinstance(key, datamodel.Country):
            return self._preformat_history_url(
                key.country_name, country=key.country_id, a_class=a_class
            )
        elif isinstance(key, datamodel.System):
            return self._preformat_history_url(
                game_info.convert_id_to_name(key.name, remove_prefix="NAME"),
                system=key.system_id,
                a_class=a_class,
            )
        elif isinstance(key, datamodel.Leader):
            return self._preformat_history_url(
                key.leader_name, leader=key.leader_id, a_class=a_class
            )
        elif isinstance(key, datamodel.Planet):
            return self._preformat_history_url(
                game_info.convert_id_to_name(key.planet_name),
                planet=key.planet_id,
                a_class=a_class,
            )
        elif isinstance(key, datamodel.War):
            return self._preformat_history_url(
                key.name, war=key.war_id, a_class=a_class
            )
        else:
            return str(key)

    def system_details(self, system_model: datamodel.System) -> Dict[str, str]:
        star_class = game_info.convert_id_to_name(
            system_model.star_class, remove_prefix="sc"
        )
        details = {
            "Star Class": star_class,
        }
        hyperlane_targets = sorted(
            system_model.neighbors, key=datamodel.System.get_name
        )
        details["Hyperlanes"] = ", ".join(
            self._get_url_for(s) for s in hyperlane_targets
        )

        bypasses = []
        for bp in system_model.bypasses:
            if bp.db_description.text == "lgate":
                bypasses.append(bp.name)
            if bp.is_active:
                targets = (
                    self._session.query(datamodel.Bypass)
                    .filter_by(network_id=bp.network_id)
                    .all()
                )
                details[bp.name] = ", ".join(
                    self._get_url_for(t.system) for t in targets if t != bp
                )
            else:
                bypasses.append(bp.name)
        if bypasses:
            details["Unknown Bypasses"] = ", ".join(bypasses)

        if system_model.country is not None and (
            system_model.country.has_met_player()
            or (
                config.CONFIG.show_everything
                and not system_model.country.is_other_player
            )
        ):
            details["Owner"] = self._get_url_for(system_model.country)

        details["Planets"] = ", ".join(
            self._get_url_for(p) for p in system_model.planets
        )

        deposits = collections.Counter()
        for p in system_model.planets:
            for d in p.deposits:
                if d.is_resource_deposit:
                    deposits[d.name] += d.count

        details["Resource Deposits"] = ", ".join(
            f"{key}: {val}" for key, val in sorted(deposits.items())
        )
        return details

    def leader_details(self, leader_model: datamodel.Leader) -> Dict[str, str]:
        country_url = self._get_url_for(leader_model.country)
        details = {
            "Leader Name": leader_model.leader_name,
            "Gender": game_info.convert_id_to_name(leader_model.gender),
            "Species": leader_model.species.species_name,
            "Class": f"{game_info.convert_id_to_name(leader_model.leader_class)} in the {country_url}",
            "Born": datamodel.days_to_date(leader_model.date_born),
            "Hired": datamodel.days_to_date(leader_model.date_hired),
            "Last active": datamodel.days_to_date(
                utils.get_most_recent_date(self._session)
                if leader_model.is_active
                else leader_model.last_date
            ),
            "Status": "Active" if leader_model.is_active else "Dead or Dismissed",
        }
        return details

    def country_details(self, country_model: datamodel.Country) -> Dict[str, str]:
        details = {
            "Country Type": game_info.convert_id_to_name(country_model.country_type),
        }
        gov = country_model.get_current_government()
        if gov is not None:
            details.update(
                {
                    "Personality": game_info.convert_id_to_name(gov.personality),
                    "Government Type": game_info.convert_id_to_name(
                        gov.gov_type, remove_prefix="gov"
                    ),
                    "Authority": game_info.convert_id_to_name(
                        gov.authority, remove_prefix="auth"
                    ),
                    "Ethics": ", ".join(
                        [
                            game_info.convert_id_to_name(e, remove_prefix="ethic")
                            for e in sorted(gov.ethics)
                        ]
                    ),
                    "Civics": ", ".join(
                        [
                            game_info.convert_id_to_name(c, remove_prefix="civic")
                            for c in sorted(gov.civics)
                        ]
                    ),
                }
            )
        for key, countries in country_model.diplo_relation_details().items():
            relation_type = game_info.convert_id_to_name(key)
            relations_to_display = [
                self._get_url_for(c)
                for c in sorted(countries, key=lambda c: c.country_name)
                if c.has_met_player()
                or (config.CONFIG.show_everything and not c.is_other_player)
            ]
            if relations_to_display:
                details[relation_type] = ", ".join(relations_to_display)

        return details

    def planet_details(self, planet_model: datamodel.Planet):
        details = {}

        modifiers = []
        for modifier in planet_model.modifiers:
            if modifier.expiry_date is not None:
                m = f"{modifier.name} (expires {datamodel.days_to_date(modifier.expiry_date)})"
            else:
                m = modifier.name
            modifiers.append(m)
        if modifiers:
            details["Modifiers"] = ", ".join(sorted(modifiers))

        resource_deposits = []
        other_deposits = []
        for d in planet_model.deposits:
            if d.is_resource_deposit:
                resource_deposits.append(d.name)
            else:
                other_deposits.append(d.name)
        if resource_deposits:
            details["Resource Deposits"] = ", ".join(
                f"{d}" for d in sorted(resource_deposits)
            )
        if other_deposits:
            details["Blockers and Features"] = ", ".join(
                f"{d}" for d in sorted(other_deposits)
            )

        districts = collections.Counter()
        for district in planet_model.districts:
            districts[district.name] += district.count
        if districts:
            details["Districts"] = ", ".join(
                f"{k}: {v}" for k, v in sorted(districts.items())
            )

        buildings = collections.Counter()
        for building in planet_model.buildings:
            buildings[building.name] += building.count
        if buildings:
            details["Buildings"] = ", ".join(
                f"{k}: {v}" for k, v in sorted(buildings.items())
            )

        return details

    def war_details(self, war):
        start = datamodel.days_to_date(war.start_date_days)

        details = {
            "Start date": start,
            "End date": "-",
            "Attackers": ", ".join(
                self._get_url_for(wp.country)
                for wp in war.participants
                if wp.is_attacker
            ),
            "Defenders": ", ".join(
                self._get_url_for(wp.country)
                for wp in war.participants
                if not wp.is_attacker
            ),
            "Outcome": war.outcome,
        }
        if war.attacker_war_exhaustion or war.defender_war_exhaustion:
            details["Attacker exhaustion"] = war.attacker_war_exhaustion
            details["Defender exhaustion"] = war.defender_war_exhaustion
        if war.end_date_days:
            details["End date"] = datamodel.days_to_date(war.end_date_days)

        return details

    def _combat_dict(self, combat: datamodel.Combat):
        attackers = ", ".join(self._get_url_for(cp.country) for cp in combat.attackers)
        defenders = ", ".join(self._get_url_for(cp.country) for cp in combat.defenders)
        return dict(
            combat_type=combat.combat_type,
            attackers=attackers,
            defenders=defenders,
            attacker_victory=combat.attacker_victory,
            attacker_exhaustion=f"{100 * combat.attacker_war_exhaustion:.1f}%",
            defender_exhaustion=f"{100 * combat.defender_war_exhaustion:.1f}%",
        )

    @functools.lru_cache()
    def _preformat_history_url(self, text, a_class="textlink", **kwargs):
        return f'<a class="{a_class}" href={flask.url_for("history_page", game_id=self.game_id, **kwargs)}>{text}</a>'
