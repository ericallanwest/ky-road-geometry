import struct

import numpy as np
import pytest
from rasterio.transform import from_origin

from ky_road_geometry import dem, grid, tiles


def test_bilinear_reads_a_plane_exactly_and_uses_the_nearest_pixel_at_the_edge():
    t = from_origin(5000.0, 10000.0, 2.0, 2.0)                # 2-ft pixels, top-left at (5000, 10000)
    cols, rows = np.meshgrid(np.arange(10), np.arange(10))
    xc, yc = 5000 + (cols + 0.5) * 2, 10000 - (rows + 0.5) * 2
    z = 3.0 * xc - 2.0 * yc                                   # a plane, sampled at pixel centers
    x, y = np.array([5003.3, 5010.0, 5000.2]), np.array([9995.1, 9990.0, 9999.9])
    out = dem._bilinear(z, t, x, y)
    np.testing.assert_allclose(out[:2], 3 * x[:2] - 2 * y[:2])
    assert out[2] == pytest.approx(3 * 5001 - 2 * 9999)       # outside the centers: the corner pixel


def test_tile_centers_come_from_the_geopackage_header():
    blob = b"GP" + bytes([0, 0b011]) + struct.pack("<i", 3857) + struct.pack("<4d", 10.0, 20.0, 30.0, 50.0) + b"wkb"
    assert tiles._center(blob) == (15.0, 40.0)


def test_grid_points_sit_on_exact_measures_with_ground_distance():
    # a 1-mi straight route drawn 1.02 mi long: the measure runs 2% short of the ground
    xym = np.array([[0.0, 0.0, 0.0], [1.02 * 5280, 0.0, 1.0]])
    p = grid.points({"R": [xym]})
    assert p["grid"].tolist() == list(range(200))              # half-open: no point at the last measure
    np.testing.assert_allclose(p["m"], np.arange(200) * 0.005)
    np.testing.assert_allclose(p["s"], p["m"] * 1.02 * 5280)
    np.testing.assert_allclose(p[["tx", "ty"]], np.tile([1.0, 0.0], (200, 1)))
