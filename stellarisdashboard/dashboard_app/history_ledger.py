"""This file contains the code for the flask server hosting the visualizations and the event ledger."""
import collections
import logging
from typing import Dict

from flask import render_template, request

from stellarisdashboard import config, datamodel, game_info
from stellarisdashboard.dashboard_app import flask_app, utils, event_registry, event_grouping
from stellarisdashboard.datamodel import join_and, join_comma
from stellarisdashboard.i18n import gettext as _, pgettext

logger = logging.getLogger(__name__)

# A merged envoy entry names this many envoys and counts the rest.
MAX_NAMED_ENVOYS = 3

# Descriptor-driven event renderer (replaces the old ~50-branch if/elif chain in
# history_page.html). Exposed to the template as render_event(event, links).
flask_app.jinja_env.globals["render_event"] = event_registry.render_event


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
        # In-game date of the most recent save, for the shell topbar stardate.
        current_date = datamodel.days_to_date(utils.get_most_recent_date(session))
    return render_template(
        "history_page.html",
        game_name=game_id,
        country=country,
        current_date=current_date,
        wars=wars,
        events=events,
        details=details,
        links=links,
        title=title,
        is_filtered_page=not event_filter.is_empty_filter,
        show_old_version_notice=show_old_version_notice,
        version=utils.VERSION,
        update_version_id=version,
    )


def get_event_filter() -> "EventFilter":
    country_id = request.args.get("country", None)
    leader_id = request.args.get("leader", None)
    system_id = request.args.get("system", None)
    war_id = request.args.get("war", None)
    planet_id = request.args.get("planet", None)
    # tolerate a missing or non-numeric min_date query param instead of 500ing
    min_date_raw = request.args.get("min_date")
    try:
        min_date = float(min_date_raw) if min_date_raw is not None else float("-inf")
    except ValueError:
        min_date = float("-inf")
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
                datamodel.Leader.leader_id_in_game.asc(),
            )
        elif self.system_filter is not None:
            return (
                datamodel.System,
                "system",
                dict(system_id=self.system_filter),
                datamodel.System.system_id_in_game.asc(),
            )
        elif self.planet_filter is not None:
            return (
                datamodel.Planet,
                "planet",
                dict(planet_id=self.planet_filter),
                datamodel.Planet.planet_id_in_game.asc(),
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
                filter_dict = dict(country_id=self.country_filter)
            return (
                datamodel.Country,
                "country",
                filter_dict,
                datamodel.Country.country_id_in_game.asc(),
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


def _name_and_count(name: str, count) -> str:
    # Translators: something and how many there are, e.g. "Mining Station: 3"
    return _("%(name)s: %(count)s", name=name, count=count)


def diplo_relation_label(key: str) -> str:
    """Label of a kind of diplomatic relation in the detail box of a country."""
    labels = {
        "rivalries": pgettext("diplomatic relation", "Rivalries"),
        "defensive_pacts": pgettext("diplomatic relation", "Defensive Pacts"),
        "federations": pgettext("diplomatic relation", "Federations"),
        "non_aggression_pacts": pgettext("diplomatic relation", "Non Aggression Pacts"),
        "closed_borders": pgettext("diplomatic relation", "Closed Borders"),
        "communations": pgettext("diplomatic relation", "Communications"),
        "migration_treaties": pgettext("diplomatic relation", "Migration Treaties"),
        "commercial_pacts": pgettext("diplomatic relation", "Commercial Pacts"),
        "neighbors": pgettext("diplomatic relation", "Neighbors"),
        "research_agreements": pgettext("diplomatic relation", "Research Agreements"),
        "embassies": pgettext("diplomatic relation", "Embassies"),
    }
    return labels.get(key) or game_info.convert_id_to_name(key)


def war_call_type_label(call_type: str) -> str:
    """How a country entered a war (the participant's call type), for the detail box of a war."""
    labels = {
        "primary": pgettext("war call type", "primary"),
        "overlord": pgettext("war call type", "overlord"),
        "offensive": pgettext("war call type", "offensive"),
        "defensive": pgettext("war call type", "defensive"),
        "alliance": pgettext("war call type", "alliance"),
    }
    return labels.get(call_type) or str(call_type)


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
        shown_events = []
        storyline_items = {}  # storyline_id -> item dict, per section
        for event in event_list:
            if not self.event_filter.include_event(event):
                continue
            if not config.CONFIG.show_everything and not event.event_is_known_to_player:
                continue
            if (
                event.country
                and any(c.is_hidden_country() for c in event.involved_countries())
                and not event.event_is_known_to_player
            ):
                continue
            country_type = (
                event.country.country_type if event.country is not None else None
            )
            if (
                not config.CONFIG.show_all_country_types
                and country_type
                not in [
                    "default",
                    "fallen_empire",
                    "awakened_fallen_empire",
                ]
                and event.country is not None
                # primitives' technology ages are shown even though their other events are not
                and event.event_type != datamodel.HistoricalEventType.pre_ftl_age_change
                # crises are special countries, but their events belong in the ledger
                and not event.country.is_crisis_country()
                and event.event_type != datamodel.HistoricalEventType.galactic_crisis
            ):
                continue
            shown_events.append(event)

        for group in event_grouping.group_envoy_assignments(shown_events):
            event = group.events[0]
            start = datamodel.days_to_date(event.start_date_days)
            end_date = None
            is_active = True
            if group.end_date_days is not None:
                end_date = datamodel.days_to_date(group.end_date_days)
                is_active = group.end_date_days >= self._most_recent_date
            description = event.description
            if type(description) is str:
                if description.startswith("{"):
                    description = game_info.render_name(description)
                else:
                    description = game_info.lookup_key(description)
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
                description=description,
                fleet=event.fleet,
            )
            event_dict = {k: v for (k, v) in event_dict.items() if v is not None}
            # the untranslated key of the description and its parts, for sentences that depend on them
            event_dict["description_key"] = event.db_description.text if event.db_description else ""
            event_dict.update(event.description_fields)

            if "planet" in event_dict and "system" not in event_dict:
                # planets carried by ships (nomad arkships) have no system
                if event_dict["planet"].system is not None:
                    event_dict["system"] = event_dict["planet"].system
            if "faction" in event_dict:
                event_dict["faction_type"] = event.faction.rendered_type
            if event.combat:
                event_dict.update(self._combat_dict(event.combat))
            if event.federation is not None:
                event_dict.update(self._federation_dict(event))
            if len(group.events) > 1:
                leaders = list(
                    dict.fromkeys(e.leader for e in group.events if e.leader is not None)
                )
                if leaders:
                    event_dict["leaders"] = leaders[:MAX_NAMED_ENVOYS]
                    event_dict["other_envoy_count"] = len(leaders) - MAX_NAMED_ENVOYS
                event_dict["assignment_count"] = len(group.events)

            if event.storyline_id is not None:
                # members fold into one card per storyline, placed at its first visible member
                item = storyline_items.get(event.storyline_id)
                if item is None:
                    item = storyline_items[event.storyline_id] = self._storyline_dict(
                        event.storyline
                    )
                    self._events[key_object].append(item)
                    self._preformat_storyline_urls(event.storyline)
                if event.stage_index is not None:
                    sort_key = (event.start_date_days, event.stage_index)
                    item["stages"].append((sort_key, self._stage_dict(event, start)))
                self._preformat_urls(event)
                continue
            self._events[key_object].append(event_dict)
            for grouped_event in group.events:
                self._preformat_urls(grouped_event)
        for item in storyline_items.values():
            item["stages"] = [s for _, s in sorted(item["stages"], key=lambda s: s[0])]
            # the furthest stage reached, which counts stages hidden from the player too
            item["stage_count"] = max((s["index"] for s in item["stages"]), default=0)
        if not self._events[key_object] and self.event_filter.is_empty_filter:
            del self._events[key_object]

    def get_war_list(self):
        if not self.event_filter.is_empty_filter:
            return []
        return sorted(
            [key for key in self._formatted_urls if isinstance(key, datamodel.War)],
            key=lambda w: w.start_date_days,
        )

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

    def _preformat_storyline_urls(self, storyline):
        for entity in (
            storyline.country,
            storyline.target_country,
            storyline.leader,
            storyline.system,
            storyline.planet,
        ):
            if entity is not None:
                self._formatted_urls[entity] = self._get_url_for(entity)

    def _storyline_dict(self, storyline: datamodel.Storyline) -> dict:
        end_date = None
        if storyline.end_date_days is not None:
            end_date = datamodel.days_to_date(storyline.end_date_days)
        item = dict(
            event_type="storyline",
            storyline_type=str(storyline.storyline_type),
            state=str(storyline.state),
            title=storyline.rendered_title,
            start_date=datamodel.days_to_date(storyline.start_date_days),
            end_date=end_date,
            is_active=storyline.end_date_days is None,
            country=storyline.country,
            target_country=storyline.target_country,
            leader=storyline.leader,
            system=storyline.system,
            planet=storyline.planet,
        )
        item = {k: v for (k, v) in item.items() if v is not None}
        item["stages"] = []
        return item

    @staticmethod
    def _stage_dict(event: datamodel.HistoricalEvent, start_date: str) -> dict:
        """A compact stage line: country and leader only where they differ from the storyline's."""
        storyline = event.storyline
        stage = dict(
            start_date=start_date,
            index=event.stage_index,
            detail=event.stage_detail,
            country=event.country if event.country is not storyline.country else None,
            leader=event.leader if event.leader is not storyline.leader else None,
        )
        return {k: v for (k, v) in stage.items() if v is not None}

    def _get_title(self, key) -> str:
        if isinstance(key, datamodel.Country):
            return self._get_url_for(key, a_class="titlelink")
        elif isinstance(key, datamodel.System):
            # Translators: title of the page of a star system; %(system)s is its name
            return _("%(system)s System", system=key.rendered_name)
        elif isinstance(key, datamodel.Leader):
            return key.rendered_name
        elif isinstance(key, datamodel.War):
            return key.rendered_name
        elif isinstance(key, datamodel.Planet):
            # Translators: title of the page of a planet; %(planet)s is its name
            return _("Planet %(planet)s", planet=key.rendered_name)
        else:
            return ""

    def _get_url_for(self, key, a_class="textlink"):
        if isinstance(key, datamodel.Country):
            return utils.preformat_history_url(
                key.rendered_name,
                country=key.country_id,
                a_class=a_class,
                game_id=self.game_id,
            )
        elif isinstance(key, datamodel.System):
            return utils.preformat_history_url(
                key.rendered_name,
                system=key.system_id,
                a_class=a_class,
                game_id=self.game_id,
            )
        elif isinstance(key, datamodel.Leader):
            return utils.preformat_history_url(
                key.rendered_name,
                leader=key.leader_id,
                a_class=a_class,
                game_id=self.game_id,
            )
        elif isinstance(key, datamodel.Planet):
            return utils.preformat_history_url(
                key.rendered_name,
                planet=key.planet_id,
                a_class=a_class,
                game_id=self.game_id,
            )
        elif isinstance(key, datamodel.War):
            return utils.preformat_history_url(
                key.rendered_name,
                war=key.war_id,
                a_class=a_class,
                game_id=self.game_id,
            )
        else:
            return str(key)

    def system_details(self, system_model: datamodel.System) -> Dict[str, str]:
        star_class = game_info.convert_id_to_name(
            system_model.star_class, remove_prefix="sc"
        )
        details = {
            pgettext("ledger detail", "Star Class"): star_class,
        }
        hyperlane_targets = sorted(
            system_model.neighbors, key=lambda s: s.rendered_name
        )
        details[pgettext("ledger detail", "Hyperlanes")] = join_comma(
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
                details[bp.name] = join_comma(
                    self._get_url_for(t.system) for t in targets if t != bp
                )
            else:
                bypasses.append(bp.name)
        if bypasses:
            details[pgettext("ledger detail", "Unknown Bypasses")] = join_comma(bypasses)

        if system_model.country is not None and not system_model.country.is_hidden_country():
            details[pgettext("ledger detail", "Owner")] = self._get_url_for(system_model.country)

        details[pgettext("ledger detail", "Planets")] = join_comma(
            self._get_url_for(p) for p in system_model.planets
        )

        deposits = collections.Counter()
        for p in system_model.planets:
            for d in p.deposits:
                if d.is_resource_deposit:
                    deposits[d.name] += d.count

        details[pgettext("ledger detail", "Resource Deposits")] = join_comma(
            _name_and_count(key, val) for key, val in sorted(deposits.items())
        )
        return details

    def leader_details(self, leader_model: datamodel.Leader) -> Dict[str, str]:
        country_url = self._get_url_for(leader_model.country)
        # the stored subclass may be unknown to the game's localisation
        subclass = game_info.lookup_key(leader_model.subclass)
        # Translators: class of a leader and the empire they serve, e.g. "Scientist in the United Nations of Earth".
        # %(leader_class)s is the leader class, %(country)s is the name of the empire (a link)
        class_and_country = _(
            "%(leader_class)s in the %(country)s",
            leader_class=event_registry.leader_class_label(leader_model.leader_class),
            country=country_url,
        )
        details = {
            pgettext("ledger detail", "Leader Name"): leader_model.rendered_name,
            pgettext("ledger detail", "Class"): class_and_country,
            pgettext("ledger detail", "Subclass"): subclass or _("None"),
            pgettext("ledger detail", "Traits"): join_comma(leader_model.rendered_traits),
            pgettext("ledger detail", "Species"): leader_model.species.rendered_name,
            pgettext("ledger detail", "Gender"): game_info.convert_id_to_name(leader_model.gender),
            pgettext("ledger detail", "Ethic"): game_info.lookup_key(leader_model.ethic),
            pgettext("ledger detail", "Previous Position"): game_info.lookup_key(f"job_{leader_model.job}")
            if leader_model.job is not None else _("None"),
            pgettext("ledger detail", "Home Planet"): self._get_url_for(leader_model.planet),
            pgettext("ledger detail", "Country of Origin"): self._get_url_for(leader_model.creator),
            pgettext("ledger detail", "Born"): datamodel.days_to_date(leader_model.date_born),
            pgettext("ledger detail", "Hired"): datamodel.days_to_date(leader_model.date_hired),
            pgettext("ledger detail", "Last active"): datamodel.days_to_date(
                utils.get_most_recent_date(self._session)
                if leader_model.is_active
                else leader_model.last_date
            ),
            pgettext("ledger detail", "Status"): _("Active") if leader_model.is_active else _("Dead or retired"),
        }
        return details

    def country_details(self, country_model: datamodel.Country) -> Dict[str, str]:
        details = {
            pgettext("ledger detail", "Country Type"): game_info.convert_id_to_name(country_model.country_type),
        }
        gov = country_model.get_current_government()
        if gov is not None:
            details.update(
                {
                    pgettext("ledger detail", "Personality"): game_info.convert_id_to_name(gov.personality),
                    pgettext("ledger detail", "Government Type"): game_info.lookup_key(gov.gov_type),
                    pgettext("ledger detail", "Authority"): game_info.lookup_key(gov.authority),
                    pgettext("ledger detail", "Ethics"): join_comma(
                        [game_info.lookup_key(e) for e in sorted(gov.ethics)]
                    ),
                    pgettext("ledger detail", "Civics"): join_comma(
                        [game_info.lookup_key(c) for c in sorted(gov.civics)]
                    ),
                    pgettext("ledger detail", "Origin"): game_info.lookup_key(country_model.origin or "UNKNOWN"),
                    pgettext("ledger detail", "Ruler"): self._get_url_for(country_model.ruler),
                }
            )
        active_councilor_events = (
            self._session.query(datamodel.HistoricalEvent)
            .filter_by(country=country_model, event_type=datamodel.HistoricalEventType.councilor, end_date_days=None)
        )
        details[pgettext("ledger detail", "Council")] = join_comma(
            # if leader name is the same as council position, only show the link
            # this is the case in hive minds, which have eg leader "Growth Node" serving as council position "Growth Node"
            self._get_url_for(event.leader)
            if event.description == event.leader.rendered_name
            # Translators: a council position and the leader holding it, e.g. "Chancellor <leader name>".
            # %(position)s is the position, %(leader)s is the name of the leader (a link)
            else _(
                "%(position)s %(leader)s",
                position=event.description,
                leader=self._get_url_for(event.leader),
            )
            for event in active_councilor_events
        )
        for key, countries in country_model.diplo_relation_details().items():
            relation_type = diplo_relation_label(key)
            relations_to_display = [
                self._get_url_for(c)
                for c in sorted(countries, key=lambda c: c.country_name)
                if not c.is_hidden_country()
            ]
            if relations_to_display:
                details[relation_type] = join_comma(relations_to_display)

        return details

    def planet_details(self, planet_model: datamodel.Planet):
        details = {}

        if planet_model.planet_class is None:
            details[pgettext("ledger detail", "Planet Class")] = _("Destroyed (no trace remains)")
        else:
            details[pgettext("ledger detail", "Planet Class")] = game_info.convert_id_to_name(
                planet_model.planet_class, remove_prefix="pc"
            )
        modifiers = []
        for modifier in planet_model.modifiers:
            if modifier.expiry_date is not None:
                # Translators: a planet modifier that runs out; %(modifier)s is its name, %(date)s the date it expires
                m = _(
                    "%(modifier)s (expires %(date)s)",
                    modifier=modifier.name,
                    date=datamodel.days_to_date(modifier.expiry_date),
                )
            else:
                m = modifier.name
            modifiers.append(m)
        if modifiers:
            details[pgettext("ledger detail", "Modifiers")] = join_comma(sorted(modifiers))

        resource_deposits = []
        other_deposits = []
        for d in planet_model.deposits:
            if d.is_resource_deposit:
                resource_deposits.append(d.name)
            else:
                other_deposits.append(d.name)
        if resource_deposits:
            details[pgettext("ledger detail", "Resource Deposits")] = join_comma(sorted(resource_deposits))
        if other_deposits:
            details[pgettext("ledger detail", "Blockers and Features")] = join_comma(sorted(other_deposits))

        districts = collections.Counter()
        for district in planet_model.districts:
            districts[district.name] += district.count
        if districts:
            details[pgettext("ledger detail", "Districts")] = join_comma(
                _name_and_count(k, v) for k, v in sorted(districts.items())
            )

        buildings = collections.Counter()
        for building in planet_model.buildings:
            buildings[building.name] += building.count
        if buildings:
            details[pgettext("ledger detail", "Buildings")] = join_comma(
                _name_and_count(k, v) for k, v in sorted(buildings.items()) if v > 0
            )

        return details

    def war_details(self, war):
        start = datamodel.days_to_date(war.start_date_days)

        attacker = next(war.attackers, None)
        defender = next(war.defenders, None)
        details = {
            pgettext("ledger detail", "Start date"): start,
            pgettext("ledger detail", "End date"): "-",
            pgettext("ledger detail", "Attackers"): join_comma(
                self._participant_with_call_type(wp) for wp in war.attackers
            ),
            pgettext("ledger detail", "Attacker War Goal"): attacker.get_war_goal() if attacker is not None else _("Unknown"),
            pgettext("ledger detail", "Defenders"): join_comma(
                self._participant_with_call_type(wp) for wp in war.defenders
            ),
            pgettext("ledger detail", "Defender War Goal"): defender.get_war_goal() if defender is not None else _("Unknown"),
            pgettext("ledger detail", "Outcome"): war.outcome,
        }
        if war.attacker_war_exhaustion or war.defender_war_exhaustion:
            details[pgettext("ledger detail", "Attacker exhaustion")] = war.attacker_war_exhaustion
            details[pgettext("ledger detail", "Defender exhaustion")] = war.defender_war_exhaustion
        if war.end_date_days:
            details[pgettext("ledger detail", "End date")] = datamodel.days_to_date(war.end_date_days)

        return details

    def _participant_with_call_type(self, participant: datamodel.WarParticipant) -> str:
        # Translators: an empire in a war and how it joined, e.g. "United Nations of Earth (primary)".
        # %(country)s is the name of the empire (a link), %(call_type)s how it entered the war
        return _(
            "%(country)s (%(call_type)s)",
            country=self._get_url_for(participant.country),
            call_type=war_call_type_label(participant.call_type),
        )

    def _combat_dict(self, combat: datamodel.Combat):
        attackers = ", ".join(
            sorted(self._get_url_for(cp.country) for cp in combat.attackers)
        )
        defenders = ", ".join(
            sorted(self._get_url_for(cp.country) for cp in combat.defenders)
        )
        return dict(
            combat_type=combat.combat_type,
            attackers=attackers,
            defenders=defenders,
            attacker_victory=combat.attacker_victory,
            attacker_exhaustion=f"{100 * combat.attacker_war_exhaustion:.1f}%",
            defender_exhaustion=f"{100 * combat.defender_war_exhaustion:.1f}%",
        )

    def _federation_dict(self, event: datamodel.HistoricalEvent):
        federation = event.federation
        result = dict(federation_name=federation.rendered_name, federation_title=federation.rendered_title)
        if event.event_type == datamodel.HistoricalEventType.federation_renamed:
            result["old_federation_name"] = event.description
        if event.event_type == datamodel.HistoricalEventType.founded_federation:
            # co-founders are the other full members since the founding date
            partners = [
                m.country
                for m in federation.memberships
                if m.start_date_days == federation.start_date_days
                and not m.is_associate
                and m.country_id != event.country_id
            ]
            links = []
            for country in sorted(partners, key=lambda c: c.rendered_name):
                self._formatted_urls[country] = self._get_url_for(country)
                links.append(self._formatted_urls[country])
            if links:
                result["federation_partners"] = join_and(links)
        elif event.event_type == datamodel.HistoricalEventType.joined_federation:
            membership = next(
                (
                    m
                    for m in federation.memberships
                    if m.country_id == event.country_id
                    and m.start_date_days == event.start_date_days
                ),
                None,
            )
            result["is_associate"] = membership is not None and membership.is_associate
            # a membership ending the day before means the country switched between full and associate
            result["changed_role"] = any(
                m.country_id == event.country_id
                and m.end_date_days == event.start_date_days - 1
                for m in federation.memberships
            )
            result["federation_member_count"] = len(
                federation.members_at(event.start_date_days)
            )
        return result
