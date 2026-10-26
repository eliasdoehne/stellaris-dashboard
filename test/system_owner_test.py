from stellarisdashboard.parsing.timeline import choose_system_owner

STARPORT = "starbase_level_starport"
ANCHOR = "starbase_level_exd"
CITADEL = "starbase_level_deep_space_citadel_1"
RING = "starbase_level_orbital_ring_tier_1"
WAYSTATION = "starbase_level_waystation_3"


def test_single_starbase():
    assert choose_system_owner([(STARPORT, 1)], previous_owner=None) == 1


def test_no_starbases():
    assert choose_system_owner([], previous_owner=None) is None


def test_foreign_citadel_does_not_own_system():
    assert choose_system_owner([(STARPORT, 1), (CITADEL, 2)], previous_owner=2) == 1
    assert choose_system_owner([(CITADEL, 2), (STARPORT, 1)], previous_owner=None) == 1


def test_foreign_rings_do_not_own_system():
    starbases = [(STARPORT, 1), (RING, 2), (RING, 1), (RING, 2)]
    assert choose_system_owner(starbases, previous_owner=2) == 1


def test_crisis_anchor_beats_orphaned_citadel_and_ring():
    starbases = [(ANCHOR, 9), (CITADEL, 3), (RING, 3)]
    assert choose_system_owner(starbases, previous_owner=3) == 9


def test_waystations_and_unresolved_owners_are_skipped():
    assert choose_system_owner([(WAYSTATION, 5), (STARPORT, 1)], previous_owner=None) == 1
    assert choose_system_owner([(STARPORT, None), (CITADEL, 2)], previous_owner=None) == 2
    assert choose_system_owner([(WAYSTATION, 5)], previous_owner=None) is None


def test_system_with_only_secondary_starbases_goes_to_the_first():
    assert choose_system_owner([(CITADEL, 2), (RING, 3)], previous_owner=None) == 2


def test_conflict_prefers_owner_other_than_previous_owner():
    assert choose_system_owner([(STARPORT, 1), (ANCHOR, 2)], previous_owner=1) == 2
    assert choose_system_owner([(STARPORT, 1), (ANCHOR, 2)], previous_owner=2) == 1
    assert choose_system_owner([(STARPORT, 1), (ANCHOR, 2)], previous_owner=None) == 1
