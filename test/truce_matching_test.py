from stellarisdashboard.parsing.timeline import (
    WarTruceCandidate,
    match_truces_to_wars,
)


def make_war(name, attackers, defenders):
    """Leaders are the first country on each side."""
    return WarTruceCandidate(
        war=name,
        attacker_leader=attackers[0],
        defender_leader=defenders[0],
        participants=frozenset(attackers + defenders),
    )


def matched_wars(truces, wars):
    return {t: c.war for t, c in match_truces_to_wars(truces, wars).items()}


def test_exact_match():
    wars = [make_war("a", [1, 2], [3])]
    assert matched_wars({10: [3, 2, 1]}, wars) == {10: "a"}


def test_multi_party_war_matches_by_leaders():
    # only the leaders and some of the allies are part of the truce
    wars = [make_war("a", [1, 2, 3], [4, 5, 6])]
    assert matched_wars({10: [1, 4, 5]}, wars) == {10: "a"}


def test_truce_missing_one_leader_does_not_match():
    wars = [make_war("a", [1, 2], [3, 4])]
    assert matched_wars({10: [1, 2, 4]}, wars) == {}
    assert matched_wars({10: [2, 3, 4]}, wars) == {}


def test_candidate_with_largest_overlap_wins():
    small = make_war("small", [1], [4])
    big = make_war("big", [1, 2, 3], [4, 5])
    assert matched_wars({10: [1, 2, 3, 4]}, [small, big]) == {10: "big"}
    assert matched_wars({10: [1, 2, 3, 4]}, [big, small]) == {10: "big"}


def test_each_war_is_resolved_once():
    wars = [make_war("a", [1, 2], [3]), make_war("b", [1], [3])]
    assert matched_wars({10: [1, 2, 3], 11: [1, 3]}, wars) == {10: "a", 11: "b"}
    # a single war is not matched to two truces
    single = [make_war("a", [1], [3])]
    assert matched_wars({10: [1, 3], 11: [1, 3]}, single) == {10: "a"}


def test_no_match():
    wars = [make_war("a", [1, 2], [3])]
    assert matched_wars({10: [7, 8]}, wars) == {}
    assert matched_wars({}, wars) == {}
    assert matched_wars({10: [1, 2, 3]}, []) == {}
