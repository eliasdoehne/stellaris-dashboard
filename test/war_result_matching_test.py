from stellarisdashboard.parsing.timeline import (
    WarSidesCandidate,
    collect_war_results,
    match_results_to_wars,
)


def make_war(name, attackers, defenders, last_seen=100):
    return WarSidesCandidate(
        war=name,
        last_seen_days=last_seen,
        attackers=frozenset(attackers),
        defenders=frozenset(defenders),
    )


def test_attacker_and_defender_victory():
    wars = [make_war("a", [1, 2], [3]), make_war("b", [4], [5])]
    results = {(2, 3, 150), (5, 4, 160)}
    assert match_results_to_wars(results, wars, 200) == {
        "a": (True, 150),
        "b": (False, 160),
    }


def test_result_must_be_in_time_window():
    wars = [make_war("a", [1], [2], last_seen=100)]
    assert match_results_to_wars({(1, 2, 100)}, wars, 200) == {}
    assert match_results_to_wars({(1, 2, 201)}, wars, 200) == {}
    assert match_results_to_wars({(1, 2, 101)}, wars, 200) == {"a": (True, 101)}


def test_winner_and_loser_must_be_on_opposite_sides():
    wars = [make_war("a", [1, 2], [3])]
    assert match_results_to_wars({(1, 2, 150)}, wars, 200) == {}
    assert match_results_to_wars({(1, 9, 150)}, wars, 200) == {}


def test_earliest_result_and_each_result_used_once():
    wars = [make_war("a", [1], [2]), make_war("b", [1], [2])]
    results = {(1, 2, 150), (2, 1, 120)}
    assert match_results_to_wars(results, wars, 200) == {
        "a": (False, 120),
        "b": (True, 150),
    }
    assert len(match_results_to_wars({(1, 2, 150)}, wars, 200)) == 1


def test_collect_war_results_dedupes_and_tolerates_malformed_entries():
    def event(definition, data, date="2300.01.02"):
        return {"date": date, "definition": definition, "data": data, "types": []}

    countries = {
        1: {
            "timeline_events": [
                event("timeline_war_won", [1, 2]),
                event("timeline_first_war_won", [1, 3]),
                "garbage",
                event("timeline_war_won", [1]),
                event("timeline_war_won", [1, "x"]),
                event("timeline_war_won", [1, 2], date=None),
                event("timeline_war_won", [1, 2], date="none"),
                event("other_event", [1, 2]),
            ]
        },
        2: {"timeline_events": [event("timeline_war_lost", [2, 1])]},
        3: {"timeline_events": "none"},
        4: "none",
    }
    days = 100 * 360 + 1
    assert collect_war_results(countries) == {(1, 2, days), (1, 3, days)}
    assert collect_war_results(None) == set()
