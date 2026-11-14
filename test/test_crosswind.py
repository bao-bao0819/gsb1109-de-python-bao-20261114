"""Test signed crosswind component ranges."""
import math

import pytest

from metar import Metar

sta_time = "KEWR 101651Z "


def report(wind_group):
    """Return a Metar object for the given wind group."""
    return Metar.Metar(sta_time + wind_group)


def test_crosswind_bounds_single_direction():
    """A zero-width sector collapses to the single component."""
    expected = 10 * math.sin(math.radians(90 - 360))
    low, high = Metar.crosswind_bounds((90, 90), 10, 360)
    assert low == pytest.approx(expected)
    assert high == pytest.approx(expected)


def test_crosswind_bounds_full_circle():
    """The whole compass spans the full signed range in both directions."""
    low, high = Metar.crosswind_bounds((0, 360), 10, 36)
    assert low == pytest.approx(-10)
    assert high == pytest.approx(10)


def test_crosswind_bounds_north_crossing_sector():
    """A 350..020 sector is passed unwrapped through north."""
    low, high = Metar.crosswind_bounds((350, 380), 10, 360)
    assert low == pytest.approx(10 * math.sin(math.radians(-10)))
    assert high == pytest.approx(10 * math.sin(math.radians(20)))


def test_crosswind_bounds_extremum_inside_sector():
    """The extrema are taken inside the sector, not only at its endpoints."""
    low, high = Metar.crosswind_bounds((120, 240), 10, 90)
    assert low == pytest.approx(5)
    assert high == pytest.approx(10)


def test_crosswind_bounds_magnetic_variation():
    """The variation aligns the wind reference with the runway bearing."""
    low, high = Metar.crosswind_bounds(
        (90, 90), 10, 100, magnetic_variation=10
    )
    assert low == pytest.approx(0)
    assert high == pytest.approx(0)


def test_fixed_direction():
    """A fixed wind direction yields identical lower and upper bounds."""
    result = report("09010KT").wind_crosswind(360)
    expected = 10 * math.sin(math.radians(90 - 360))
    assert result["mean"][0] == pytest.approx(expected)
    assert result["mean"][1] == pytest.approx(expected)
    assert result["gust"] is None
    assert result["calm"] is False
    assert result["variable_direction"] is False


def test_north_crossing_variable_sector():
    """350V020 keeps the sector continuous across north."""
    result = report("36010KT 350V020").wind_crosswind(360)
    assert result["mean"][0] == pytest.approx(
        10 * math.sin(math.radians(-10))
    )
    assert result["mean"][1] == pytest.approx(
        10 * math.sin(math.radians(20))
    )
    assert result["variable_direction"] is False


def test_sector_with_interior_extremum():
    """The maximum component is reached inside the reported sector."""
    result = report("18010KT 120V240").wind_crosswind(90)
    assert result["mean"][0] == pytest.approx(5)
    assert result["mean"][1] == pytest.approx(10)


def test_variable_direction_spans_full_range():
    """VRB makes every direction, hence the full signed range, possible."""
    result = report("VRB10KT").wind_crosswind(36)
    assert result["mean"][0] == pytest.approx(-10)
    assert result["mean"][1] == pytest.approx(10)
    assert result["variable_direction"] is True
    assert result["calm"] is False


def test_gust_envelope_for_variable_direction():
    """The gust envelope is evaluated independently over the full compass."""
    result = report("VRB10G25KT").wind_crosswind(36)
    assert result["mean"][0] == pytest.approx(-10)
    assert result["mean"][1] == pytest.approx(10)
    assert result["gust"][0] == pytest.approx(-25)
    assert result["gust"][1] == pytest.approx(25)


def test_gust_envelope_for_fixed_direction():
    """The gust uses its own speed across the same direction sector."""
    result = report("21010G30KT").wind_crosswind(270)
    mean_component = 10 * math.sin(math.radians(210 - 270))
    gust_component = 30 * math.sin(math.radians(210 - 270))
    assert result["mean"][0] == pytest.approx(mean_component)
    assert result["mean"][1] == pytest.approx(mean_component)
    assert result["gust"][0] == pytest.approx(gust_component)
    assert result["gust"][1] == pytest.approx(gust_component)
    assert abs(result["gust"][0]) > abs(result["mean"][0])


def test_gust_envelope_uses_the_same_sector():
    """The gust envelope reuses the reported direction sector."""
    result = report("18010G20KT 120V240").wind_crosswind(90)
    assert result["mean"][0] == pytest.approx(5)
    assert result["mean"][1] == pytest.approx(10)
    assert result["gust"][0] == pytest.approx(10)
    assert result["gust"][1] == pytest.approx(20)


def test_gust_can_be_excluded():
    """include_gust=False leaves the gust entry unset."""
    result = report("21010G30KT").wind_crosswind(270, include_gust=False)
    assert result["gust"] is None


def test_calm_and_variable_calm_are_distinct():
    """Calm and VRB-calm report different direction information."""
    calm = report("00000KT").wind_crosswind(36)
    assert calm == {
        "mean": None,
        "gust": None,
        "variable_direction": False,
        "calm": True,
    }
    variable_calm = report("VRB00KT").wind_crosswind(36)
    assert variable_calm == {
        "mean": None,
        "gust": None,
        "variable_direction": True,
        "calm": True,
    }


def test_missing_speed_is_not_zero_filled():
    """Missing speeds stay missing and never become zero components."""
    missing = report("090//KT").wind_crosswind(36)
    assert missing == {
        "mean": None,
        "gust": None,
        "variable_direction": False,
        "calm": False,
    }
    missing_variable = report("VRB//KT").wind_crosswind(36)
    assert missing_variable == {
        "mean": None,
        "gust": None,
        "variable_direction": True,
        "calm": False,
    }


def test_speed_units_are_honored():
    """Components are returned in the requested speed units."""
    result = report("09010KT").wind_crosswind(360, units="MPS")
    expected = 10 * 0.514444 * math.sin(math.radians(90 - 360))
    assert result["mean"][0] == pytest.approx(expected)
    assert result["mean"][1] == pytest.approx(expected)


def test_magnetic_variation_is_applied():
    """Variation converts the magnetic wind to the true runway reference."""
    result = report("09010KT").wind_crosswind(100, magnetic_variation=10)
    assert result["mean"][0] == pytest.approx(0)
    assert result["mean"][1] == pytest.approx(0)
