"""Tests for low-cloud ceiling extraction and height-bin tallies."""
import pytest

from metar import Metar


def report(sky):
    """Build a Metar object whose body is just the given sky groups."""
    return Metar.Metar("KEWR 101651Z " + sky)


def test_ceiling_conditions_keeps_only_ceiling_layers():
    """FEW and SCT are ignored; BKN, OVC and VV survive in report order."""
    m = report("FEW005 SCT010 BKN/// OVC050")
    conditions = m.ceiling_conditions()
    assert [cover for cover, height in conditions] == ["BKN", "OVC"]
    assert conditions[0][1] is None
    assert conditions[1][1].value("FT") == 5000


def test_ceiling_conditions_empty_without_ceiling_clouds():
    """Scattered or fewer clouds, and a clear sky, carry no ceiling."""
    assert report("FEW010 SCT020").ceiling_conditions() == []
    assert report("SKC").ceiling_conditions() == []
    assert report("CLR").ceiling_conditions() == []


def test_ceiling_conditions_retains_vertical_visibility():
    """A vertical-visibility group is a ceiling even without a height."""
    m = report("VV///")
    conditions = m.ceiling_conditions()
    assert conditions == [("VV", None)]


def test_lowest_known_layer_sets_the_definite_bin():
    """The lowest BKN/OVC/VV layer determines the single definite bin."""
    result = report("BKN010 OVC020").low_cloud_bins([500, 1000, 2000])
    assert result["bins"] == {500: 0, 1000: 1, 2000: 0}
    assert result["possible"] == {500: 0, 1000: 0, 2000: 0}
    assert result["no_ceiling"] == 0


def test_each_report_counts_once_across_layers():
    """Multiple ceiling layers in one report never add up to two tallies."""
    result = report("BKN005 BKN010 OVC030").low_cloud_bins([500, 1000, 3000])
    assert sum(result["bins"].values()) == 1
    assert result["bins"][500] == 1


def test_vertical_visibility_height_is_binned():
    """A known vertical-visibility height is treated as a definite ceiling."""
    result = report("VV002").low_cloud_bins([500, 1000])
    assert result["bins"] == {500: 1, 1000: 0}


def test_ceiling_above_highest_bin_is_not_binned():
    """A real ceiling beyond every edge is distinct from having no ceiling."""
    result = report("OVC050").low_cloud_bins([1000, 3000])
    assert result["bins"] == {1000: 0, 3000: 0}
    assert result["possible"] == {1000: 0, 3000: 0}
    assert result["no_ceiling"] == 0


def test_scattered_clouds_have_no_ceiling():
    """Only scattered or fewer clouds means the report has no ceiling."""
    result = report("FEW010 SCT020").low_cloud_bins([500, 1000])
    assert result["bins"] == {500: 0, 1000: 0}
    assert result["possible"] == {500: 0, 1000: 0}
    assert result["no_ceiling"] == 1


def test_missing_height_without_higher_layer_may_be_in_any_bin():
    """A BKN with no height is a ceiling, but every bin stays only possible."""
    result = report("BKN///").low_cloud_bins([500, 1000, 2000])
    assert result["bins"] == {500: 0, 1000: 0, 2000: 0}
    assert result["possible"] == {500: 1, 1000: 1, 2000: 1}
    assert result["no_ceiling"] == 0


def test_missing_height_is_bounded_by_higher_known_layer():
    """A known higher layer bounds the unknown base instead of replacing it."""
    result = report("BKN/// OVC010").low_cloud_bins([500, 1000, 2000])
    assert result["bins"] == {500: 0, 1000: 0, 2000: 0}
    # The unknown base is below 1000 ft, so it can only reach the 1000 ft bin.
    assert result["possible"] == {500: 1, 1000: 1, 2000: 0}
    assert result["no_ceiling"] == 0


def test_missing_height_below_lowest_edge():
    """When the bounding layer sits inside the first bin only that bin matches."""
    result = report("BKN/// OVC005").low_cloud_bins([500, 1000, 2000])
    assert result["possible"] == {500: 1, 1000: 0, 2000: 0}


def test_missing_vertical_visibility_uses_higher_bound():
    """An unknown vertical visibility is bounded by a higher known ceiling."""
    result = report("VV/// OVC010").low_cloud_bins([500, 1000, 2000])
    assert result["bins"] == {500: 0, 1000: 0, 2000: 0}
    assert result["possible"] == {500: 1, 1000: 1, 2000: 0}


def test_report_is_never_both_definite_and_possible():
    """A report lands either in a definite bin or in possible bins, not both."""
    definite_report = report("BKN010").low_cloud_bins([500, 1000])
    assert sum(definite_report["bins"].values()) == 1
    assert sum(definite_report["possible"].values()) == 0

    uncertain_report = report("BKN///").low_cloud_bins([500, 1000])
    assert sum(uncertain_report["bins"].values()) == 0
    assert sum(uncertain_report["possible"].values()) == 2


def test_bin_edges_honor_caller_units():
    """Bin edges are interpreted in the requested height unit."""
    result = report("OVC010").low_cloud_bins([305], units="M")
    assert result["bins"] == {305: 1}
    too_low = report("OVC010").low_cloud_bins([300], units="M")
    assert too_low["bins"] == {300: 0}


def test_accumulating_many_reports_keeps_tallies_separate():
    """Callers sum per-report tallies while definite and possible stay apart."""
    edges = [500, 1000, 3000]
    codes = [
        "BKN005",          # definite 500
        "OVC020",          # definite 3000
        "BKN/// OVC010",   # possible 500, 1000
        "FEW010 SCT020",   # no ceiling
    ]
    total = {"bins": {edge: 0 for edge in edges},
             "possible": {edge: 0 for edge in edges},
             "no_ceiling": 0}
    for code in codes:
        result = report(code).low_cloud_bins(edges)
        for edge in edges:
            total["bins"][edge] += result["bins"][edge]
            total["possible"][edge] += result["possible"][edge]
        total["no_ceiling"] += result["no_ceiling"]

    assert total["bins"] == {500: 1, 1000: 0, 3000: 1}
    assert total["possible"] == {500: 1, 1000: 1, 3000: 0}
    assert total["no_ceiling"] == 1


@pytest.mark.parametrize("edges", [[1000, 500], [500, 500], [500, 1000, 1000]])
def test_bin_edges_must_be_strictly_increasing(edges):
    """Unordered or duplicated edges cannot define unambiguous bins."""
    with pytest.raises(ValueError):
        report("BKN010").low_cloud_bins(edges)
