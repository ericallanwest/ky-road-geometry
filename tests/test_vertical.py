import numpy as np
import pandas as pd
import pytest

from ky_road_geometry import vertical
from ky_road_geometry.grid import STEP_MI

FT = 5280 * STEP_MI          # 26.4 ft between grid points


def profile(z_of_s, n=400):
    """A straight route whose grid points are 26.4 ft apart, with elevation z(s)."""
    s = np.arange(n) * FT
    return pd.DataFrame({"route": "001-KY-0001-000", "part": 0, "grid": np.arange(n), "m": np.arange(n) * STEP_MI,
                         "s": s, "z": z_of_s(s)})


def test_grade_classes_follow_kytc_bins():
    assert vertical.grade_class(np.array([0.2, -0.45, 2.4, 2.5, 6.4, -8.5, 12])).tolist() == \
        ["A", "B", "B", "C", "D", "F", "F"]


def test_point_grade_is_a_central_difference():
    p = profile(lambda s: 1000 + 0.04 * s)                  # a steady 4% climb
    np.testing.assert_allclose(vertical.point_grade(p), 4.0)


def test_records_fit_grade_and_tell_crest_from_sag():
    # 0-0.5 mi climbs 3%, 0.5-1.0 mi is a crest (grade 3% to -3%), 1.0-1.5 mi falls 3%
    L = 0.5 * 5280

    def z(s):
        up = 0.03 * np.minimum(s, L)
        crest = np.clip(s - L, 0, L)
        return 100 + up + 0.03 * crest - 0.03 * crest ** 2 / L - 0.03 * np.clip(s - 2 * L, 0, None)

    p = profile(z, n=int(1.5 / STEP_MI))
    records = pd.DataFrame({"route": "001-KY-0001-000", "BEGIN_MP": [0.0, 0.5, 1.0], "END_MP": [0.5, 1.0, 1.5],
                            "GRADEDIR": ["U", "C", "D"], "GRADEPER": [3.0, None, 3.0], "GRADECLS": ["C", None, "C"],
                            "MILES": [0.5, 0.5, 0.5]})
    fits = vertical.fit_records(p, records)
    assert fits["dem_grade"].iloc[0] == pytest.approx(3.0, abs=0.01)
    assert fits["dem_grade"].iloc[2] == pytest.approx(-3.0, abs=0.01)
    assert fits["dem_curv"].iloc[1] < 0                       # a crest
    result = vertical.score(fits)
    assert result["all"]["direction_agrees"] == 1.0 and result["crest_sag_agrees"] == 1.0
    assert result["all"]["class_exact"] == 1.0


def test_a_bridge_valley_is_found_and_decked_over():
    def z(s):   # a 1% climb, with a 40-ft-deep valley 200 ft wide where a bridge carries the road
        valley = 40 * np.clip(np.cos(np.pi * (s - 5000) / 200), 0, None) ** 0.2 * (np.abs(s - 5000) < 100)
        return 500 + 0.01 * s - valley

    p = profile(z)
    p["grade"] = vertical.point_grade(p)
    dips = vertical.bridge_dips(p)
    assert len(dips) == 1
    assert dips["s0"].iloc[0] < 4900 < 5100 < dips["s1"].iloc[0] < 5300
    assert dips["depth_ft"].iloc[0] > 30
    road = vertical.deck(p, dips)
    np.testing.assert_allclose(road, 500 + 0.01 * p["s"], atol=0.05)   # the climb carries on over the deck
