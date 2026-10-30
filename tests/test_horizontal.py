import numpy as np
import pytest

from ky_road_geometry import horizontal


def road_with_arc(radius=500.0, turn_deg=60.0, left=True):
    """1,000 ft of tangent, a circular arc, 1,000 ft of tangent, drawn every 10 ft, M in miles."""
    t1 = np.column_stack([np.linspace(-1000, 0, 101), np.zeros(101)])
    sign = 1 if left else -1
    phi = np.radians(np.linspace(0, turn_deg, 200))
    arc = np.column_stack([radius * np.sin(phi), sign * radius * (1 - np.cos(phi))])
    end, heading = arc[-1], sign * np.radians(turn_deg)
    t2 = end + np.outer(np.linspace(0, 1000, 101)[1:], [np.cos(heading), np.sin(heading)])
    xy = np.vstack([t1, arc[1:], t2])
    m = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(xy, axis=0).T))]) / 5280
    return {"R": [np.column_stack([xy, m])]}


@pytest.mark.parametrize("left", [True, False])
def test_an_arc_is_found_with_its_radius_and_direction(left):
    st = horizontal.curvature(horizontal.stations(road_with_arc(left=left)))
    found = horizontal.curves(st)
    assert len(found) == 1
    c = found.iloc[0]
    assert c["direction"] == ("L" if left else "R")
    assert c["radius_ft"] == pytest.approx(500, rel=0.05)
    assert c["degree"] == pytest.approx(5729.58 / 500, rel=0.05)
    assert c["deflection_deg"] == pytest.approx(60, abs=2)
    arc_mi = (1000 + 500 * np.radians(60)) / 5280            # where the arc ends, in miles
    assert c["m0"] < 1000 / 5280 + 0.01 and c["m1"] > arc_mi - 0.01


def test_kasa_fits_a_circle_exactly():
    phi = np.linspace(0.3, 1.2, 20)
    r, rms = horizontal.kasa(10 + 250 * np.cos(phi), -5 + 250 * np.sin(phi))
    assert r == pytest.approx(250) and rms == pytest.approx(0, abs=1e-6)


def test_curve_classes_follow_kytc_bins():
    assert horizontal.curve_class(np.array([1.0, 3.5, 5.4, 8.5, 14.0, 28.0])).tolist() == list("ABBDEF")
