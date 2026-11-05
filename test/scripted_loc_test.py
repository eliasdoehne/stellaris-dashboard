import pytest

from stellarisdashboard import datamodel, game_info


SCRIPT = """
# a comment
defined_text = {
    name = GetChosenName
    text = {
        trigger = { exists = owner owner = { is_gestalt = yes } }
        localization_key = chosen_gestalt
    }
    default = chosen_default
}
"quoted" = "a value" # another comment
count > 2
"""

SCRIPTED_LOC = {
    "GetChosenName": ([([("owner", [("is_gestalt", "yes")])], "chosen_gestalt")], "chosen_default"),
    "GetNoDefault": ([([("is_ruler", "yes")], "only_variant")], None),
    "GetIndividualNameCap": ([([("owner", [("is_hive_empire", "yes")])], "drone_cap")], "individual_cap"),
}


@pytest.fixture
def renderer(monkeypatch):
    renderer = game_info.NameRenderer([])
    renderer.name_mapping = {
        "chosen_gestalt": "Chosen Vessel",
        "chosen_default": "Chosen One",
        "drone_cap": "Drone",
        "individual_cap": "Individual",
        "leader_trait_chosen": "[GetChosenName]",
        "leader_trait_consul_hive": "[owner.GetIndividualNameCap]-Consul",
        "leader_trait_ruler_hive_mind": "[owner.GetRulerTitle]",
        "NAME_Homeworld_Two": "[Space_Owner.GetHomeWorldName] II",
        "NAME_Moon": "[This.GetUnknownThing] Moon",
        "d_energy_2": "£energy£ +2",
        "d_dark_matter": "£sr_dark_matter£ +1",
        "coloured": "§RRepeal§! X",
        "concept_pop": "Pop",
        "with_concepts": "['concept_pop'] and ['concept_pop', Pops]",
    }
    monkeypatch.setattr(game_info, "global_renderer", renderer)
    monkeypatch.setattr(game_info, "_scripted_loc", lambda: SCRIPTED_LOC)
    monkeypatch.setattr(game_info, "ruler_title", lambda gov_type: "Hive Mind" if gov_type == "gov_hive_mind" else None)
    return renderer


def test_parse_script():
    assert game_info.parse_script(SCRIPT) == [
        (
            "defined_text",
            [
                ("name", "GetChosenName"),
                (
                    "text",
                    [
                        ("trigger", [("exists", "owner"), ("owner", [("is_gestalt", "yes")])]),
                        ("localization_key", "chosen_gestalt"),
                    ],
                ),
                ("default", "chosen_default"),
            ],
        ),
        ("quoted", "a value"),
        ("count", "2"),
    ]


@pytest.mark.parametrize(
    "trigger, expected",
    [
        ([("is_ruler", "yes")], True),
        ([("is_ruler", "no")], False),
        ([("is_heir", "yes")], None),  # not a known fact
        ([("is_ruler", "yes"), ("is_heir", "yes")], None),
        ([("is_ruler", "no"), ("is_heir", "yes")], False),
        ([("exists", "owner")], True),
        ([("exists", "planet")], False),
        ([("exists", "fleet")], None),
        ([("owner", [("is_gestalt", "yes")])], True),
        ([("planet", [("is_gestalt", "yes")])], None),
        ([("has_trait", "t1")], True),
        ([("has_trait", "t2")], False),
        ([("OR", [("is_ruler", "no"), ("is_heir", "yes")])], None),
        ([("OR", [("is_ruler", "yes"), ("is_heir", "yes")])], True),
        ([("NOT", [("is_ruler", "yes")])], False),
        ([("NOR", [("is_ruler", "no"), ("owner", [("is_gestalt", "no")])])], True),
        ([("NOT", [("is_heir", "yes")])], None),
        ([("AND", [("is_ruler", "yes"), ("owner", [("is_gestalt", "yes")])])], True),
    ],
)
def test_trigger_holds(trigger, expected):
    facts = {"is_ruler": True, "has_trait": {"t1"}, "owner": {"is_gestalt": True}, "planet": None}
    assert game_info.trigger_holds(trigger, facts) is expected


@pytest.mark.parametrize(
    "name, facts, expected",
    [
        ("GetChosenName", {"owner": {"is_gestalt": True}}, "chosen_gestalt"),
        ("GetChosenName", {"owner": {"is_gestalt": False}}, "chosen_default"),
        ("GetChosenName", None, "chosen_default"),
        ("GetNoDefault", None, "only_variant"),
        ("GetMissing", None, None),
    ],
)
def test_scripted_loc_key(renderer, name, facts, expected):
    assert game_info.scripted_loc_key(name, facts) == expected


@pytest.mark.parametrize(
    "name, expected",
    [
        ({"key": "leader_trait_chosen"}, "Chosen One"),  # the default without any facts
        # variables take precedence over scripted loc
        (
            {
                "key": "NAME_Homeworld_Two",
                "variables": [{"key": "Space_Owner.GetHomeWorldName", "value": {"key": "Marbell", "literal": "yes"}}],
            },
            "Marbell II",
        ),
        ({"key": "NAME_Moon"}, "Moon"),  # unresolvable scripted loc is dropped
        ({"key": "d_energy_2"}, "Energy +2"),
        ({"key": "d_dark_matter"}, "Dark Matter +1"),
        ({"key": "coloured"}, "Repeal X"),
        ({"key": "with_concepts"}, "Pop and Pops"),
        ({"key": "[not a script]", "literal": "yes"}, "[not a script]"),
    ],
)
def test_render_scripted_names(renderer, name, expected):
    assert renderer.render_from_dict(name) == expected


def test_render_loc_text_uses_facts_and_values(renderer):
    assert game_info.render_loc_text("[GetChosenName]", {"owner": {"is_gestalt": True}}) == "Chosen Vessel"
    assert game_info.render_loc_text("[owner.GetRulerTitle]", values={"owner.GetRulerTitle": "Queen"}) == "Queen"


def _leader(authority=None, gov_type="gov_test", ethic="ethic_militarist", traits="leader_trait_chosen"):
    country = None
    if authority:
        country = datamodel.Country(country_name="{}")
        country.governments = [
            datamodel.Government(authority=authority, gov_type=gov_type, ethics_1=ethic, start_date_days=0, end_date_days=0)
        ]
    return datamodel.Leader(leader_traits=traits, country=country, gender="male")


@pytest.mark.parametrize(
    "leader, trait, expected",
    [
        (_leader(), "leader_trait_chosen", "Chosen One"),
        (_leader("auth_democratic"), "leader_trait_chosen", "Chosen One"),
        (_leader("auth_hive_mind", ethic="ethic_gestalt_consciousness"), "leader_trait_chosen", "Chosen Vessel"),
        (_leader("auth_hive_mind"), "leader_trait_consul_hive_2", "Drone-Consul (2)"),
        (_leader("auth_hive_mind", gov_type="gov_hive_mind"), "leader_trait_ruler_hive_mind", "Hive Mind"),
        # nothing left of the scripted name: the trait's id
        (_leader(), "leader_trait_ruler_hive_mind", "Ruler Hive Mind"),
    ],
)
def test_render_scripted_trait(renderer, leader, trait, expected):
    assert leader.render_trait(trait) == expected
