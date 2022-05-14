import json
import logging

logger = logging.getLogger(__name__)


class NameRenderer:
    default_name = "Unknown name"

    def __init__(self, localization_files):
        self.localization_files = localization_files
        self.name_mapping = None

    def load_name_mapping(self):
        """
        Load a mapping of all name keys to their corresponding templates / localized values.

        Localization files can be passed in, by default the dashboard tries to locate them from
        """
        self.name_mapping = {"global_event_country": "Global event country"}
        for p in self.localization_files:
            # manually parse yaml, yaml.safe_load doesnt seem to work
            with open(p, "rt") as f:
                for line in f:
                    try:
                        key, val, *rest = line.strip().split('"')
                        self.name_mapping[key.strip().rstrip(":0")] = val.strip()
                    except Exception:
                        pass

    def render_from_json(self, name_json: str):
        rendered = self.render_from_dict(json.loads(name_json))
        if rendered == self.default_name:
            logger.warning(
                "Failed to resolve a name, please check if you configured localization files."
            )
            logger.warning(f"Instructions can be found in README.md")
            logger.warning(f"Failed name: {name_json!r}")
        return rendered

    def render_from_dict(self, name_dict: dict):
        if name_dict.get("literal") == "yes":
            return name_dict.get("key", self.default_name)
        rendered = self.name_mapping.get(name_dict.get("key"), self.default_name)
        for var in name_dict.get("variables", []):
            key = var.get("key")
            subst_value = self.render_from_dict(var.get("value", {}))
            # try all combinations of escaping identifiers to substitute the variables
            for lparen, rparen in [
                ("<", ">"),
                ("[", "]"),
                ("$", "$"),
            ]:
                rendered = rendered.replace(f"{lparen}{key}{rparen}", subst_value)
        return rendered


global_renderer: NameRenderer = None


def render_name(json_str: str):
    global global_renderer
    if global_renderer is None:
        from stellarisdashboard import config

        global_renderer = NameRenderer(config.CONFIG.localization_files)
        global_renderer.load_name_mapping()
    return global_renderer.render_from_json(json_str)


PHYSICS_TECHS = {
    "tech_databank_uplinks",
    "tech_basic_science_lab_1",
    "tech_curator_lab",
    "tech_archeology_lab",
    "tech_physics_lab_1",
    "tech_physics_lab_2",
    "tech_physics_lab_3",
    "tech_global_research_initiative",
    "tech_administrative_ai",
    "tech_cryostasis_1",
    "tech_cryostasis_2",
    "tech_self_aware_logic",
    "tech_automated_exploration",
    "tech_sapient_ai",
    "tech_positronic_implants",
    "tech_combat_computers_1",
    "tech_combat_computers_2",
    "tech_combat_computers_3",
    "tech_combat_computers_autonomous",
    "tech_auxiliary_fire_control",
    "tech_synchronized_defences",
    "tech_fission_power",
    "tech_fusion_power",
    "tech_cold_fusion_power",
    "tech_antimatter_power",
    "tech_zero_point_power",
    "tech_reactor_boosters_1",
    "tech_reactor_boosters_2",
    "tech_reactor_boosters_3",
    "tech_shields_1",
    "tech_shields_2",
    "tech_shields_3",
    "tech_shields_4",
    "tech_shields_5",
    "tech_shield_rechargers_1",
    "tech_planetary_shield_generator",
    "tech_sensors_2",
    "tech_sensors_3",
    "tech_sensors_4",
    "tech_power_plant_1",
    "tech_power_plant_2",
    "tech_power_plant_3",
    "tech_power_plant_4",
    "tech_power_hub_1",
    "tech_power_hub_2",
    "tech_hyper_drive_1",
    "tech_hyper_drive_2",
    "tech_hyper_drive_3",
    "tech_wormhole_stabilization",
    "tech_gateway_activation",
    "tech_gateway_construction",
    "tech_jump_drive_1",
    "tech_ftl_inhibitor",
    "tech_matter_generator",
}

SOCIETY_TECHS = {
    "tech_planetary_defenses",
    "tech_eco_simulation",
    "tech_hydroponics",
    "tech_gene_crops",
    "tech_nano_vitality_crops",
    "tech_nutrient_replication",
    "tech_biolab_1",
    "tech_biolab_2",
    "tech_biolab_3",
    "tech_alien_life_studies",
    "tech_colonization_1",
    "tech_colonization_2",
    "tech_colonization_3",
    "tech_colonization_4",
    "tech_colonization_5",
    "tech_tomb_world_adaption",
    "tech_space_trading",
    "tech_frontier_health",
    "tech_frontier_hospital",
    "tech_tb_mountain_range",
    "tech_tb_volcano",
    "tech_tb_dangerous_wildlife",
    "tech_tb_dense_jungle",
    "tech_tb_quicksand_basin",
    "tech_tb_noxious_swamp",
    "tech_tb_massive_glacier",
    "tech_tb_toxic_kelp",
    "tech_tb_deep_sinkhole",
    "tech_terrestrial_sculpting",
    "tech_ecological_adaptation",
    "tech_climate_restoration",
    "tech_genome_mapping",
    "tech_vitality_boosters",
    "tech_epigenetic_triggers",
    "tech_cloning",
    "tech_gene_banks",
    "tech_gene_seed_purification",
    "tech_morphogenetic_field_mastery",
    "tech_gene_tailoring",
    "tech_glandular_acclimation",
    "tech_genetic_resequencing",
    "tech_gene_expressions",
    "tech_selected_lineages",
    "tech_capacity_boosters",
    "tech_regenerative_hull_tissue",
    "tech_doctrine_fleet_size_1",
    "tech_doctrine_fleet_size_2",
    "tech_doctrine_fleet_size_3",
    "tech_doctrine_fleet_size_4",
    "tech_doctrine_fleet_size_5",
    "tech_interstellar_fleet_traditions",
    "tech_refit_standards",
    "tech_command_matrix",
    "tech_doctrine_navy_size_1",
    "tech_doctrine_navy_size_2",
    "tech_doctrine_navy_size_3",
    "tech_doctrine_navy_size_4",
    "tech_centralized_command",
    "tech_combat_training",
    "tech_ground_defense_planning",
    "tech_global_defense_grid",
    "tech_psionic_theory",
    "tech_telepathy",
    "tech_precognition_interface",
    "tech_psi_jump_drive_1",
    "tech_galactic_ambitions",
    "tech_manifest_destiny",
    "tech_interstellar_campaigns",
    "tech_galactic_campaigns",
    "tech_planetary_government",
    "tech_planetary_unification",
    "tech_colonial_centralization",
    "tech_galactic_administration",
    "tech_galactic_markets",
    "tech_subdermal_stimulation",
    "tech_galactic_benevolence",
    "tech_adaptive_bureaucracy",
    "tech_colonial_bureaucracy",
    "tech_galactic_bureaucracy",
    "tech_living_state",
    "tech_collective_self",
    "tech_autonomous_agents",
    "tech_embodied_dynamism",
    "tech_neural_implants",
    "tech_artificial_moral_codes",
    "tech_synthetic_thought_patterns",
    "tech_collective_production_methods",
    "tech_resource_processing_algorithms",
    "tech_cultural_heritage",
    "tech_heritage_site",
    "tech_hypercomms_forum",
    "tech_autocurating_vault",
    "tech_holographic_rituals",
    "tech_consecration_fields",
    "tech_transcendent_faith",
    "tech_ascension_theory",
    "tech_ascension_theory_apoc",
    "tech_psionic_shield",
}

ENGINEERING_TECHS = {
    "tech_space_exploration",
    "tech_corvettes",
    "tech_destroyers",
    "tech_cruisers",
    "tech_battleships",
    "tech_titans",
    "tech_corvette_build_speed",
    "tech_corvette_hull_1",
    "tech_corvette_hull_2",
    "tech_destroyer_build_speed",
    "tech_destroyer_hull_1",
    "tech_destroyer_hull_2",
    "tech_cruiser_build_speed",
    "tech_cruiser_hull_1",
    "tech_cruiser_hull_2",
    "tech_battleship_build_speed",
    "tech_battleship_hull_1",
    "tech_battleship_hull_2",
    "tech_titan_hull_1",
    "tech_titan_hull_2",
    "tech_starbase_1",
    "tech_starbase_2",
    "tech_starbase_3",
    "tech_starbase_4",
    "tech_starbase_5",
    "tech_modular_engineering",
    "tech_space_defense_station_improvement",
    "tech_strike_craft_1",
    "tech_strike_craft_2",
    "tech_strike_craft_3",
    "tech_assault_armies",
    "tech_ship_armor_1",
    "tech_ship_armor_2",
    "tech_ship_armor_3",
    "tech_ship_armor_4",
    "tech_ship_armor_5",
    "tech_crystal_armor_1",
    "tech_crystal_armor_2",
    "tech_thrusters_1",
    "tech_thrusters_2",
    "tech_thrusters_3",
    "tech_thrusters_4",
    "tech_space_defense_station_1",
    "tech_defense_platform_hull_1",
    "tech_basic_industry",
    "tech_powered_exoskeletons",
    "tech_mining_network_2",
    "tech_mining_network_3",
    "tech_mining_network_4",
    "tech_mineral_processing_1",
    "tech_mineral_processing_2",
    "tech_engineering_lab_1",
    "tech_engineering_lab_2",
    "tech_engineering_lab_3",
    "tech_robotic_workers",
    "tech_droid_workers",
    "tech_synthetic_workers",
    "tech_synthetic_leaders",
    "tech_space_construction",
    "tech_afterburners_1",
    "tech_afterburners_2",
    "tech_assembly_pattern",
    "tech_construction_templates",
    "tech_mega_engineering",
}

ALL_KNOWN_TECHS = set.union(PHYSICS_TECHS, ENGINEERING_TECHS, SOCIETY_TECHS)

ASCENSION_PERKS = {
    "ap_enigmatic_engineering",  #: "Enigmatic Engineering",
    "ap_nihilistic_acquisition",  #: "Nihilistic Acquisition",
    "ap_colossus",  #: "Colossus",
    "ap_engineered_evolution",  #: "Engineered Evolution",
    "ap_evolutionary_mastery",  #: "Evolutionary Mastery",
    "ap_the_flesh_is_weak",  #: "The Flesh is Weak",
    "ap_synthetic_evolution",  #: "Synthetic Evolution",
    "ap_mind_over_matter",  #: "Mind over Matter",
    "ap_transcendence",  #: "Transcendence",
    "ap_world_shaper",  #: "World Shaper",
    "ap_galactic_force_projection",  #: "Galactic Force Projection",
    "ap_defender_of_the_galaxy",  #: "Defender of the Galaxy",
    "ap_interstellar_dominion",  #: "Interstellar Dominion",
    "ap_grasp_the_void",  #: "Grasp the Void",
    "ap_eternal_vigilance",  #: "Eternal Vigilance",
    "ap_galactic_contender",  #: "Galactic Contender",
    "ap_technological_ascendancy",  #: "Technological Ascendancy",
    "ap_one_vision",  #: "One Vision",
    "ap_consecrated_worlds",  #: "Consecrate Worlds",
    "ap_mastery_of_nature",  #: "Mastery of Nature",
    "ap_imperial_prerogative",  #: "Imperial Prerogative",
    "ap_executive_vigor",  #: "Executive Vigor",
    "ap_transcendent_learning",  #: "Transcendent Learning",
    "ap_shared_destiny",  #: "Shared Destiny",
    "ap_voidborn",  #: "Voidborn",
    "ap_master_builders",  #: "Master Builders",
    "ap_galactic_wonders",  #: "Galactic Wonders",
    "ap_synthetic_age",  #: "Synthetic Age",
    "ap_machine_worlds",  #: "Machine Worlds",
}

COLONIZABLE_PLANET_CLASSES_PLANETS = {
    "pc_desert",
    "pc_arid",
    "pc_savannah",
    "pc_tropical",
    "pc_continental",
    "pc_ocean",
    "pc_tundra",
    "pc_arctic",
    "pc_alpine",
    "pc_gaia",
    "pc_nuked",
    "pc_machine",
}
COLONIZABLE_PLANET_CLASSES_MEGA_STRUCTURES = {
    "pc_ringworld_habitable",
    "pc_habitat",
}

# Planet classes for the planetary diversity mod
# (see https://steamcommunity.com/workshop/filedetails/discussion/1466534202/3397295779078104093/)
COLONIZABLE_PLANET_CLASSES_PD_PLANETS = {
    "pc_antarctic",
    "pc_deadcity",
    "pc_retinal",
    "pc_irradiated_terrestrial",
    "pc_lush",
    "pc_geocrystalline",
    "pc_marginal",
    "pc_irradiated_marginal",
    "pc_marginal_cold",
    "pc_crystal",
    "pc_floating",
    "pc_graveyard",
    "pc_mushroom",
    "pc_city",
    "pc_archive",
    "pc_biolumen",
    "pc_technoorganic",
    "pc_tidallylocked",
    "pc_glacial",
    "pc_frozen_desert",
    "pc_steppe",
    "pc_hadesert",
    "pc_boreal",
    "pc_sandsea",
    "pc_subarctic",
    "pc_geothermal",
    "pc_cascadian",
    "pc_swamp",
    "pc_mangrove",
    "pc_desertislands",
    "pc_mesa",
    "pc_oasis",
    "pc_hajungle",
    "pc_methane",
    "pc_ammonia",
}
COLONIZABLE_PLANET_CLASSES = (
    COLONIZABLE_PLANET_CLASSES_PLANETS
    | COLONIZABLE_PLANET_CLASSES_MEGA_STRUCTURES
    | COLONIZABLE_PLANET_CLASSES_PD_PLANETS
)

DESTROYED_BY_WEAPONS_PLANET_CLASSES = {
    "pc_shattered",
    "pc_shielded",
    "pc_ringworld_shielded",
    "pc_habitat_shielded",
    "pc_ringworld_habitable_damaged",
}
DESTROYED_BY_EVENTS_AND_CRISES_PLANET_CLASSES = {
    "pc_egg_cracked",
    "pc_shrouded",
    "pc_ai",
    "pc_infested",
    "pc_gray_goo",
}
DESTROYED_PLANET_CLASSES = (
    DESTROYED_BY_WEAPONS_PLANET_CLASSES | DESTROYED_BY_EVENTS_AND_CRISES_PLANET_CLASSES
)


def is_destroyed_planet(planet_class):
    return planet_class in DESTROYED_PLANET_CLASSES


def is_colonizable_planet(planet_class):
    return planet_class in COLONIZABLE_PLANET_CLASSES


def is_colonizable_megastructure(planet_class):
    return planet_class in COLONIZABLE_PLANET_CLASSES_MEGA_STRUCTURES


LOWERCASE_WORDS = {"the", "in", "of", "for", "is", "over", "under"}
WORD_REPLACEMENT = {
    "Ai": "AI",
    "Ftl": "FTL",
    "Tb": "Tile Blocker",
}


def convert_id_to_name(object_id: str, remove_prefix="") -> str:
    words = [word for word in object_id.split("_") if word != remove_prefix]
    words = [
        word.capitalize() if word not in LOWERCASE_WORDS else word for word in words
    ]
    words = [WORD_REPLACEMENT.get(word, word) for word in words]
    return " ".join(words)
