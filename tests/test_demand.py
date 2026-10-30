import numpy as np
import pytest

from ky_road_geometry import cross, demand


def test_superelevation_banks_into_the_curve_either_way():
    cs_right = np.array([4.0, 4.0, -4.0, -4.0])     # lane rising (+) or falling (-) to the right
    kappa = np.array([0.002, -0.002, 0.002, -0.002])  # left, right, left, right (with the measure)
    np.testing.assert_allclose(cross.superelevation(cs_right, kappa, 1.0), [4, -4, -4, 4])
    np.testing.assert_allclose(cross.superelevation(cs_right, kappa, -1.0), [-4, 4, 4, -4])


def test_curve_speed_settles_on_aashto_side_friction():
    # R = 500 ft, e = 6%: V = sqrt(g R (e + f(V))) settles near 40.5 mph, where f is about 0.159
    v = demand.curve_speed(np.array([500.0]), np.array([6.0]), np.array([60.0]))[0]
    assert v == pytest.approx(40.5, abs=0.5)
    assert demand.curve_speed(np.array([np.inf]), np.array([2.0]), np.array([60.0]))[0] == 60.0


def test_speeds_ramp_down_into_a_slow_stretch_and_back_up():
    s = np.arange(0, 4000, 26.4)
    cap = np.where((s > 2000) & (s < 2500), 30.0, 60.0)
    v = demand.ramp(s, cap)
    assert v.max() <= 60 + 1e-9 and v[(s > 2000) & (s < 2500)].max() <= 30 + 1e-9
    fps = v * demand.MPH
    slowing = np.diff(fps ** 2) / (2 * np.diff(s))
    assert slowing.min() >= -demand.DECEL - 1e-6 and slowing.max() <= demand.ACCEL + 1e-6


def test_stop_demand_from_sight_distance():
    # 50 mph, 500 ft of sight, level: 183 ft of reaction leaves 317 ft to stop from 73.3 ft/s
    assert demand.stop_demand(50, 500, 0.0) == pytest.approx(0.264, abs=0.002)
    assert demand.stop_demand(50, 500, -5.0) == pytest.approx(0.314, abs=0.002)   # downhill needs more
    assert demand.stop_demand(50, 150, 0.0) == 1.0                                 # not even time to react
