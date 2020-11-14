import pytest
import stellarisdashboard.dashboard_app.utils as u


@pytest.mark.parametrize(
    "requested_version,actual_version,expected_result",
    [
        ("v1.0", "v1.0", False),
        ("v1.0", "v0.9", True),
        ("v0.9", "v1.0", False),
        ("v0.9-alpha", "v1.0", False),
        ("v1.0", "v0.9-alpha", True),
        ("v1.0-beta", "v1.0-alpha", True),
        ("v1.0-alpha", "v1.0-beta", False),
        ("v1.10", "v1.9", True),
        ("v1.9", "v1.10", False),
        ("v1.10", "v1.9-beta", True),
        ("v1.9-beta", "v1.10", False),
        ("v1.0.0.0.1", "v1.0", True),
        ("v1.0", "v1.0.0.0.1", False),
    ],
)
def test_version_comparison(requested_version, actual_version, expected_result):
    assert (
        u.is_old_version(
            requested_version=requested_version, actual_version=actual_version
        )
        == expected_result
    )


@pytest.mark.parametrize(
    "actual_version",
    [
        # output of git tag
        "0.4.1",
        "v0.0.1",
        "v0.1.0",
        "v0.1.1",
        "v0.1.2-alpha",
        "v0.1.3",
        "v0.1.4",
        "v0.1.5",
        "v0.1.5-alpha",
        "v0.2-beta",
        "v0.2.0",
        "v0.2.1",
        "v0.3-beta",
        "v0.3-beta.2",
        "v0.4-beta",
        "v0.4.2",
        "v0.5.0",
        "v0.6.0",
        "v0.6.1",
        "v0.6.2",
        "v1.0",
        "v1.1",
        "v1.2",
    ],
)
def test_all_existing_versions_are_old(actual_version):
    assert u.is_old_version(requested_version=u.VERSION, actual_version=actual_version)
