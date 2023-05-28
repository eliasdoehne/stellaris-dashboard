import dataclasses
import pathlib

import pytest

from stellarisdashboard import config, game_info


@dataclasses.dataclass
class NameTestcase:
    name_dict: dict
    expected: str = ""
    description: str = "test"


@pytest.mark.skip_github_actions  # requires game localization files to run
@pytest.mark.parametrize(
    "test_case",
    [
        NameTestcase(
            dict(
                key="EMPIRE_DESIGN_humans2",
                variables=[],
            ),
            expected="Commonwealth of Man",
            description="empire, built-in",
        ),
        NameTestcase(
            dict(
                key="EMPIRE_DESIGN_humans2",
                literal="yes",
                variables=[],
            ),
            expected="EMPIRE_DESIGN_humans2",
            description="empire, literal overrides builtin",
        ),
        NameTestcase(
            dict(
                key="format.gen_olig.1",
                variables=[
                    {"key": "generic_oli_desc", "value": {"key": "Sovereign"}},
                    {"key": "generic_states", "value": {"key": "Realms"}},
                    {"key": "This.GetSpeciesName", "value": {"key": "SPEC_Klenn"}},
                ],
            ),
            expected="Sovereign Klenn Realms",
            description="empire, template with variables",
        ),
        NameTestcase(
            dict(
                key="format.imp_spi.2",
                variables=[
                    {"key": "imperial_spi", "value": {"key": "Kingdom"}},
                    {
                        "key": "This.Capital.GetName",
                        "value": {"key": "SPEC_Hythean_planet"},
                    },
                ],
            ),
            expected="Kingdom of Hythea",
            description="empire, template with variables",
        ),
        NameTestcase(
            dict(
                key="format.gen_imp.1",
                variables=[
                    {"key": "generic_aut_desc", "value": {"key": "Combined"}},
                    {"key": "generic_states", "value": {"key": "Suns"}},
                    {"key": "This.GetSpeciesName", "value": {"key": "SPEC_Mollarnock"}},
                ],
            ),
            expected="Combined Mollarnock Suns",
            description="empire, template with variables",
        ),
        NameTestcase(
            dict(
                key="EMPIRE_DESIGN_yondar",
                variables=[],
            ),
            expected="Kingdom of Yondarim",
            description="empire, built-in",
        ),
        NameTestcase(
            dict(
                key="Commonwealth of Custom Name",
                variables=[],
                literal="yes",
            ),
            expected="Commonwealth of Custom Name",
            description="empire, custom",
        ),
        NameTestcase(
            {
                # "$PREFIX$ $NAME$"
                "key": "PREFIX_NAME_FORMAT",
                "variables": [
                    {"key": "NAME", "value": {"key": "ART2_SHIP_Rit-Kwyr"}},
                    {
                        "key": "PREFIX",
                        "value": {
                            # "[This.GetSpeciesName] <trade_league>"
                            "key": "format.trade_league.1",
                            "variables": [
                                {
                                    "key": "trade_league",
                                    "value": {"key": "Trading_Consortium"},
                                },
                                {
                                    "key": "This.GetSpeciesName",
                                    "value": {"key": "SPEC_Qiramulan"},
                                },
                            ],
                        },
                    },
                ],
            },
            expected="Qiramulan Trading Consortium Rit-Kwyr",
            description="fleet, in-game science fleet",
        ),
        NameTestcase(
            {
                "key": "%LEADER_2%",
                "variables": [
                    {"key": "1", "value": {"key": "MAM4_CHR_Tibb"}},
                    {"key": "2", "value": {"key": "MAM4_CHR_daughterofNagg"}},
                ],
            },
            expected="Tibb, daughter of Nagg",
            description="LEADER_2",
        ),
        NameTestcase(
            {
                "key": "%SEQ%",
                "variables": [
                    {"key": "fmt", "value": {"key": "MAM4_STARORDER"}},
                    {"key": "num", "value": {"key": "73"}},
                ],
            },
            expected="73rd Star Order",
            description="SEQ",
        ),
        NameTestcase(
            {
                "key": "%ADJ%",
                "variables": [
                    {
                        "key": "1",
                        "value": {
                            "key": "Democratic",
                            "variables": [
                                {
                                    "key": "1",
                                    "value": {
                                        "key": "%ADJECTIVE%",
                                        "variables": [
                                            {
                                                "key": "adjective",
                                                "value": {"key": "SPEC_Cevanti"},
                                            },
                                            {"key": "1", "value": {"key": "Assembly"}},
                                        ],
                                    },
                                }
                            ],
                        },
                    }
                ],
            },
            expected="Democratic Cevantian Assembly",
            description="nested_country_species_adjective",
        ),
        NameTestcase(
            {
                "key": "PREFIX_NAME_FORMAT",
                "variables": [
                    {"key": "NAME", "value": {"key": "PLANT2_SHIP_Perennia"}},
                    {
                        "key": "PREFIX",
                        "value": {
                            "key": "%ACRONYM%",
                            "variables": [
                                {
                                    "key": "base",
                                    "value": {
                                        "key": "%ADJECTIVE%",
                                        "variables": [
                                            {
                                                "key": "adjective",
                                                "value": {"key": "SPEC_Panaxala"},
                                            },
                                            {"key": "1", "value": {"key": "Theocracy"}},
                                        ],
                                    },
                                }
                            ],
                        },
                    },
                ],
            },
            expected="PTY Perennia",
            description="ACRONYM test",
        ),
    ],
    ids=lambda tc: tc.description,
)
def test_name_rendering_with_game_files(test_case: NameTestcase):
    renderer = game_info.NameRenderer(config.CONFIG.localization_files)
    renderer.load_name_mapping()
    assert renderer.render_from_dict(test_case.name_dict) == test_case.expected


@pytest.mark.parametrize(
    "test_case",
    [
        NameTestcase(
            dict(
                key="HUM2_xxPUREBLOODS_ORD",
                variables=[{"key": "O", "value": {"key": "1st", "literal": "yes"}}],
            ),
            expected="1st Purebloods",
            description="army, $ interpolation",
        ),
        NameTestcase(
            dict(
                key="HUM2_KEY_DOES_NOT_EXIST",
                variables=[{"key": "O", "value": {"key": "1st", "literal": "yes"}}],
            ),
            expected="HUM2_KEY_DOES_NOT_EXIST",
            description="army, unknown returns top-level key",
        ),
        NameTestcase(
            dict(key="HUMAN1_SHIP_SpecialSymbols"),
            expected=r"Special-Symbols. ''!$%&/()",
            description="ship, two words",
        ),
        NameTestcase(
            dict(
                key="format.gen_imp.1",
                variables=[
                    {"key": "generic_aut_desc", "value": {"key": "Combined"}},
                    {"key": "generic_states", "value": {"key": "Suns"}},
                    {"key": "This.GetSpeciesName", "value": {"key": "SPEC_Mollarnock"}},
                ],
            ),
            expected="Combined Mollarnock Suns",
            description="empire, template with variables",
        ),
        NameTestcase(
            dict(
                key="format.gen_imp.1",
                variables=[
                    {"key": "generic_aut_desc", "value": {"key": "Combined"}},
                    {"key": "generic_states", "value": {"key": "Suns"}},
                    {
                        "key": "This.GetSpeciesName",
                        "value": {"key": "SPEC_DOESNOTEXIST"},
                    },
                ],
            ),
            expected="Combined SPEC_DOESNOTEXIST Suns",
            description="empire, template with variables, nested does not exist",
        ),
        NameTestcase(
            dict(
                key="%LEADER_2%",
                variables=[
                    {"key": "1", "value": {"key": "Golveso"}},
                    {"key": "2", "value": {"key": "Selvayn"}},
                ],
            ),
            expected="Golveso Selvayn",
            description="%LEADER_2% template",
        ),
        NameTestcase(
            dict(
                key="%ADJECTIVE%",
                variables=[
                    {"key": "adjective", "value": {"key": "Nexitron"}},
                    {"key": "1", "value": {"key": "Awareness"}},
                ],
            ),
            expected="Nexitron Awareness",
            description="%ADJECTIVE% template",
        ),
        NameTestcase(
            dict(
                key="%ADJECTIVE%",
                variables=[
                    {"key": "adjective", "value": {"key": "NAME_Ketling_Multitude"}},
                    {"key": "1", "value": {"key": "Nation"}},
                ],
            ),
            expected="Ketling Star Pack Nation",
            description=":1 template",
        ),
        NameTestcase(
            dict(
                key="%ADJ%",
                variables=[
                    {
                        "key": "1",
                        "value": {
                            "key": "Allied",
                            "variables": [
                                {
                                    "key": "1",
                                    "value": {
                                        "key": "%ADJECTIVE%",
                                        "variables": [
                                            {
                                                "key": "adjective",
                                                "value": {"key": "Jing"},
                                            },
                                            {"key": "1", "value": {"key": "Systems"}},
                                        ],
                                    },
                                }
                            ],
                        },
                    }
                ],
            ),
            expected="Allied Jing Systems",
            description="%ADJ% test",
        ),
        NameTestcase(
            dict(
                key="%ADJECTIVE%",
                variables=[
                    {"key": "adjective", "value": {"key": "Quetzan"}},
                    {
                        "key": "1",
                        "value": {
                            "key": "%ADJ%",
                            "variables": [
                                {
                                    "key": "1",
                                    "value": {
                                        "key": "Consolidated",
                                        "variables": [
                                            {"key": "1", "value": {"key": "Worlds"}}
                                        ],
                                    },
                                }
                            ],
                        },
                    },
                ],
            ),
            expected="Quetzan Consolidated Worlds",
            description="Nested %ADJECTIVE% and %ADJ% test",
        ),
    ],
    ids=lambda tc: tc.description,
)
def test_name_rendering_with_test_files_english(test_case: NameTestcase):
    renderer = game_info.NameRenderer(
        list(
            (pathlib.Path(__file__).parent / "localization_test_files/english").glob(
                "*.yml"
            )
        )
    )
    renderer.load_name_mapping()
    assert renderer.render_from_dict(test_case.name_dict) == test_case.expected
