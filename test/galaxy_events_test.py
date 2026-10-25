from stellarisdashboard.dashboard_app.event_registry import GALACTIC_CRISIS_PHRASES
from stellarisdashboard.parsing import galaxy_events
from stellarisdashboard.parsing.galaxy_events import collect_galaxy_events, flag_date_days


def flag(year, month=1, day=1):
    return ((year - 2200) * 360 + (month - 1) * 30 + day - 1 + 2617000) * 24


def test_flag_date_days():
    assert flag_date_days(62808000) == 0  # game_started of a 2200.01.01 start
    assert flag_date_days(flag(2452, 6, 25)) == 252 * 360 + 5 * 30 + 24
    assert flag_date_days({"flag_date": flag(2450), "flag_days": 720}) == 250 * 360
    for value in (None, "yes", True, {"flag_days": 3}):
        assert flag_date_days(value) is None


def test_collect_galaxy_events():
    now = 260 * 360
    flags = {
        "extradimensional_invasion_happened": flag(2452, 6, 25),
        "crisis_stage_1": flag(2452, 6, 25),
        "crisis_stage_2": flag(2455),
        "voidworms_plague_crisis_started": flag(2308),
        "great_khan_dead": flag(2379, 12),
        "war_in_heaven_started": flag(2470),  # after the current date
        "unrelated_flag": flag(2300),
    }
    countries = {
        3: {"type": "awakened_fallen_empire", "last_changed_country_type": "2402.12.22"},
        5: {"type": "default", "flags": {"declared_crisis": flag(2458, 3), "other": flag(2210)}},
        4: {"type": "awakened_fallen_empire", "last_changed_country_type": "0.01.01"},
        26: {"type": "voidworms"},
        620757096: {"type": "portal_holder"},
        671088712: {"type": "extradimensional"},
        99: "none",
    }
    events = {(e.key, e.country_ids): e for e in collect_galaxy_events(flags, countries, now)}
    assert set(events) == {
        ("unbidden_arrived", (671088712,)),
        ("crisis_stage_2", (671088712,)),
        ("voidworm_plague_started", (26,)),
        ("great_khan_died", ()),
        ("war_in_heaven_started", (3, 4)),
        ("fallen_empire_awakened", (3,)),
        ("fallen_empire_awakened", (4,)),
        ("declared_crisis", (5,)),
    }
    assert events["unbidden_arrived", (671088712,)].date_days == flag_date_days(flag(2452, 6, 25))
    assert events["voidworm_plague_started", (26,)].date_days == 108 * 360
    assert events["war_in_heaven_started", (3, 4)].date_days == now
    assert events["war_in_heaven_started", (3, 4)].all_countries
    assert events["fallen_empire_awakened", (3,)].date_days == 202 * 360 + 11 * 30 + 21
    assert events["fallen_empire_awakened", (4,)].date_days == now
    assert list(collect_galaxy_events("none", "none", now)) == []


def test_every_galaxy_event_has_a_ledger_phrase():
    keys = {s.key for s in galaxy_events.SIGNALS}
    keys |= {f"crisis_stage_{n}" for n in galaxy_events.CRISIS_STAGES}
    keys |= {galaxy_events.FALLEN_EMPIRE_AWAKENED, galaxy_events.DECLARED_CRISIS}
    assert keys == set(GALACTIC_CRISIS_PHRASES)
