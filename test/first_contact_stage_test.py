import pytest

from stellarisdashboard import datamodel, game_info

ICON = "GFX_firstcontact_icon_"
STAGES = {
    "base_contact_set": [(ICON + "starter", "sum_encountered"), (ICON + "starter", "sum_other")],
    "enclave_stage_1": [(ICON + "progress", "sum_we_discovered")],
    "enclave_stage_2": [(ICON + "success", "sum_critter"), (ICON + "success", "sum_critter")],
    "default_stage_2": [(ICON + "question", "sum_delay"), (ICON + "progress", "sum_broadcast")],
    "default_hostile_ship_stage": [
        (ICON + "warship_success", "sum_ship_best"),
        (ICON + "fail", "sum_ship_lost"),
    ],
    "default_end_stage_cautious": [(ICON + "success", "sum_comms")],
    "default_end_stage_mixed": [(ICON + "fail", "sum_comms_bad"), (ICON + "success", "sum_comms_good")],
    "default_stage_system": [(ICON + "progress", "sum_in_system")],
    "no_summary_stage_proactive": [(ICON + "success", None)],
    "default_unknown_icon_stage": [(ICON + "saucer", None)],
}
LOC = {
    "sum_encountered": "We encountered unknown aliens.",
    "sum_other": "A colossal creature appeared.",
    "sum_we_discovered": "We discovered the §H[This.ContactCountry.GetName]§!.",
    "sum_critter": "$sum_critter_base$",
    "sum_critter_base": "The §H[This.ContactCountry.GetName]§! were identified as space whales.",
    "sum_delay": "We encountered delays.",
    "sum_broadcast": "We received a broadcast.",
    "sum_ship_best": "Our attempt to capture a §H[This.ContactCountry.GetName]§! ship §Gsucceeded§!.",
    "sum_ship_lost": "Our attempt to capture a §H[This.ContactCountry.GetName]§! ship failed.",
    "sum_comms": "Communications with the §H[This.ContactCountry.GetName]§! were established.",
    "sum_comms_bad": "Communications were established. They thought us cowardly.",
    "sum_comms_good": "Communications were established. They liked us.",
    "sum_in_system": "A signal came from the §H[This.System.GetName]§! system.",
}
NAMES = {"This.ContactCountry.GetName": "Zarg Empire"}


@pytest.fixture
def stages(monkeypatch):
    renderer = game_info.NameRenderer([])
    renderer.name_mapping = LOC
    monkeypatch.setattr(game_info, "global_renderer", renderer)
    monkeypatch.setattr(game_info, "_first_contact_stages", lambda: STAGES)


@pytest.mark.parametrize(
    "texts, expected",
    [
        (["Our attempt to capture a ship worked", "Our attempt to capture a ship failed"], "Our attempt to capture a ship"),
        (["They met. It went well", "They met. It went badly"], ""),  # shorter than three words
        (["Contact was made. It went well", "Contact was made. They left"], "Contact was made."),
        (["Unrelated one", "Something else"], ""),
    ],
)
def test_shared_clause(texts, expected):
    assert game_info.shared_clause(texts) == expected


@pytest.mark.parametrize(
    "stage, values, expected",
    [
        # the single summary, in the third person
        ("default_end_stage_cautious", NAMES, "communications with the Zarg Empire were established"),
        ("enclave_stage_2", NAMES, "the Zarg Empire were identified as space whales"),
        # names bring their own article
        (
            "default_end_stage_cautious",
            {"This.ContactCountry.GetName": "The Chosen"},
            "communications with the Chosen were established",
        ),
        # the outcome isn't known: what its possible summaries share
        ("default_hostile_ship_stage", NAMES, "attempt to capture a Zarg Empire ship"),
        ("default_end_stage_mixed", NAMES, "communications were established"),
        # nothing shared, first person, missing values, no summary
        ("default_stage_2", NAMES, None),
        ("enclave_stage_1", NAMES, None),
        ("default_stage_system", NAMES, None),
        ("no_summary_stage_proactive", NAMES, None),
        ("not_a_stage", NAMES, None),
    ],
)
def test_first_contact_stage_summary(stages, stage, values, expected):
    assert game_info.first_contact_stage_summary(stage, values) == expected


@pytest.mark.parametrize(
    "stage, expected",
    [
        ("base_contact_set", "starter"),
        ("default_hostile_ship_stage", "success"),  # warship_ icons have the regular roles
        ("default_stage_2", "progress"),  # several roles: the most neutral
        ("default_end_stage_mixed", "success"),
        ("default_unknown_icon_stage", None),
        ("not_a_stage", None),
    ],
)
def test_first_contact_stage_role(stages, stage, expected):
    assert game_info.first_contact_stage_role(stage) == expected


@pytest.mark.parametrize(
    "stage, expected",
    [
        ("default_hostile_ship_stage", "attempt to capture a Zarg Empire ship (hostile)"),
        ("default_end_stage_cautious", "communications with the Zarg Empire were established (cautious)"),
        ("base_contact_set", "contact detected"),
        ("default_stage_2", "investigation progressed"),
        ("no_summary_stage_proactive", "communications established (proactive)"),
        ("default_unknown_icon_stage", "Unknown Icon Stage"),
    ],
)
def test_render_first_contact_stage(stages, stage, expected):
    assert datamodel.render_first_contact_stage(stage, NAMES) == expected
