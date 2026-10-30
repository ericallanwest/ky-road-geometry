import numpy as np
import pandas as pd
import pytest

from ky_road_geometry import sight

STEP = 5.0     # fine spacing, so the answers aren't limited by the grid


def profile(z_of_s, length=8000.0):
    s = np.arange(0, length, STEP)
    z = z_of_s(s)
    p = pd.DataFrame({"route": "R", "part": 0, "grid": np.arange(len(s)), "s": s, "z": z})
    p["grade"] = np.gradient(z, s) * 100
    return p


def test_flat_road_is_seen_to_the_cap_and_never_lit_short():
    sd = sight.sight_distances(profile(lambda s: np.full_like(s, 700.0)))
    mid = len(sd) // 2
    assert sd["sight_inv"].iloc[mid] == pytest.approx(sight.MAX_FT)
    assert sd["headlight_non"].iloc[mid] == pytest.approx(sight.MAX_FT)


def test_crest_sight_matches_aashto():
    K = 50.0                                    # ft of curve per percent of grade change
    sd = sight.sight_distances(profile(lambda s: 900 - (s - 4000) ** 2 / (200 * K)))
    expected = np.sqrt(100 * K * (np.sqrt(2 * sight.EYE_FT) + np.sqrt(2 * sight.OBJECT_FT)) ** 2)
    for d in ("inv", "non"):                    # the same both ways on a symmetric crest
        assert sd[f"sight_{d}"].iloc[len(sd) // 2] == pytest.approx(expected, abs=2 * STEP)


def test_sag_headlight_distance_matches_aashto():
    K = 50.0
    sd = sight.sight_distances(profile(lambda s: 600 + (s - 4000) ** 2 / (200 * K)))
    # AASHTO, S < L: L = A S^2 / (200 (h + S tan b)), so S^2 = 200 K h + 200 K tan(b) S
    t = 200 * K * np.tan(np.radians(sight.BEAM_DEG))
    expected = (t + np.sqrt(t ** 2 + 4 * 200 * K * sight.HEADLIGHT_FT)) / 2
    assert sd["headlight_inv"].iloc[len(sd) // 2] == pytest.approx(expected, abs=2 * STEP)
    assert sd["sight_inv"].iloc[len(sd) // 2] == pytest.approx(sight.MAX_FT)   # a sag hides nothing by day


def test_stopping_sight_distance_table():
    # AASHTO Green Book, level road: 360 ft at 45 mph, 495 ft at 55 mph (rounded up)
    np.testing.assert_allclose(sight.stopping_sight_required([45, 55]), [360, 495], atol=5)
    assert sight.stopping_sight_required(50, -6) > sight.stopping_sight_required(50, 6)
