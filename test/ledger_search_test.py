import pytest

from stellarisdashboard.dashboard_app import ledger_search as ls


def entry(kind, name, weight=()):
    return ls.Entry(kind, hash(name), name, ls.normalize(name), "", weight)


@pytest.mark.parametrize(
    "name,query,rank",
    [
        ("Sol", "sol", 0),
        ("Solonnen", "sol", 1),
        ("Amukka Solonnen", "sol", 2),
        ("Kel-Sol", "sol", 2),
        ("Plimsoll", "sol", 3),
        ("United Nations of Earth", "earth uni", 4),
        ("United Nations of Earth", "earth xyz", None),
        ("Mars", "sol", None),
        ("Ḱalé'Vön", "kale", 1),
        ("Ḱalé'Vön", "von", 2),
    ],
)
def test_match_rank(name, query, rank):
    query = ls.normalize(query)
    assert ls.match_rank(ls.normalize(name), query, query.split(" ")) == rank


def test_normalize_keeps_length():
    for name in ["Ḱalé'Vön", "Straße", "ÆON", "plain"]:
        assert len(ls.normalize(name)) == len(name)


def test_search_ranks_and_groups():
    entries = [
        entry("leader", "Florence Plimsoll"),
        entry("leader", "Amukka Solonnen"),
        entry("system", "Sol"),
        entry("planet", "Solaris Prime", weight=(True,)),
        entry("planet", "Solaris", weight=(True,)),
        entry("planet", "Solaris Minor", weight=(False,)),
    ]
    groups = ls.search(entries, "  SOL ")
    # groups by their best match: exact system, prefix planets, word-start leader
    assert [kind for kind, _, _ in groups] == ["system", "planet", "leader"]
    leaders = [h.entry.name for h in groups[2][1]]
    assert leaders == ["Amukka Solonnen", "Florence Plimsoll"]
    # equal ranks: lower weight first, then shorter names
    planets = [h.entry.name for h in groups[1][1]]
    assert planets == ["Solaris Minor", "Solaris", "Solaris Prime"]


def test_search_caps_and_counts():
    entries = [entry("leader", f"Unit {i}") for i in range(30)]
    (kind, hits, total), = ls.search(entries, "unit")
    assert len(hits) == ls.PER_KIND and total == 30
    (kind, hits, total), = ls.search(entries, "unit", kind="leader")
    assert len(hits) == 30
    assert ls.search(entries, "unit", kind="planet") == []
    assert ls.search(entries, "   ") == []


def test_highlight():
    assert ls.highlight("Amukka Solonnen", "sol") == ("Amukka ", "Sol", "onnen")
    assert ls.highlight("Kalé", "kale") == ("", "Kalé", "")
    assert ls.highlight("United Nations", "nat uni") == ("United ", "Nat", "ions")
    assert ls.highlight("Mars", "sol") == ("Mars", "", "")
    assert ls.highlight("Sutharian Arbitrators", "a") == ("Sutharian ", "A", "rbitrators")
    assert ls.highlight("Plimsoll", "sol") == ("Plim", "sol", "l")
