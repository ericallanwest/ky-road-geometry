"""Cross slope and superelevation of the travel lane, from the lidar.

For each grid point and direction of travel, the surface is read across the right-hand lane: two
lines parallel to the road, ``LANE_IN_FT`` and ``LANE_OUT_FT`` to the right of the route line,
each the mean of 13 reads along a 24-ft strip (smoothing pavement texture and DEM noise). The
cross slope is the rise between them per foot, in percent, positive when the surface rises to
the right. Reading within one lane matters: on a two-lane road the route line runs down the
crown, and a band across it would average the two lanes' opposite slopes to nothing.

On a crowned tangent the right lane falls away from the crown, about -2%. In a curve the lane
banks toward the inside: superelevation ``e`` is the rise toward the outside of the curve.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import dem

LANE_IN_FT = 4.0
LANE_OUT_FT = 8.0
STRIP_FT = 24.0
STRIP_STEP_FT = 2.0
SMOOTH = 5            # grid points in the rolling median that settles the reading


def cross_slopes(root, prof: pd.DataFrame, log=print) -> pd.DataFrame:
    """Cross slope (percent, rising to the right of travel) of the right-hand lane, both ways."""
    along = np.arange(-STRIP_FT / 2, STRIP_FT / 2 + 1e-9, STRIP_STEP_FT)
    x, y = prof["x"].to_numpy(), prof["y"].to_numpy()
    tx, ty = prof["tx"].to_numpy(), prof["ty"].to_numpy()
    out = {}
    for d, sign in (("inv", 1.0), ("non", -1.0)):
        # travelling with the measure the right-hand normal is (ty, -tx); against it, the reverse
        nx, ny = sign * ty, -sign * tx
        zs = []
        for off in (LANE_IN_FT, LANE_OUT_FT):
            px = x[:, None] + off * nx[:, None] + along[None, :] * tx[:, None]
            py = y[:, None] + off * ny[:, None] + along[None, :] * ty[:, None]
            z, _ = dem.sample(root, px.ravel(), py.ravel())
            with np.errstate(invalid="ignore"):
                zs.append(np.nanmean(z.reshape(px.shape), axis=1))
            log(f"{d}: read the strip {off:g} ft out")
        out[f"cs_raw_{d}"] = 100 * (zs[1] - zs[0]) / (LANE_OUT_FT - LANE_IN_FT)
    cs = pd.DataFrame(out, index=prof.index)
    key = [prof["route"], prof["part"]]
    order = prof.sort_values(["route", "part", "grid"]).index
    for d in ("inv", "non"):
        s = cs.loc[order, f"cs_raw_{d}"]
        cs.loc[order, f"cs_{d}"] = s.groupby([k.loc[order] for k in key]).transform(
            lambda v: v.rolling(SMOOTH, center=True, min_periods=3).median()).to_numpy()
    return cs


def superelevation(cs_right: np.ndarray, kappa: np.ndarray, direction_sign: float) -> np.ndarray:
    """Rise toward the outside of the curve (percent): positive when the lane banks into it.

    ``kappa`` is the curvature in the direction of increasing measure (positive turning left);
    ``direction_sign`` is +1 travelling with the measure, -1 against it. A curve turning left in
    the direction of travel has its outside on the right."""
    turning_left = np.sign(kappa) * direction_sign > 0
    return np.where(turning_left, cs_right, -cs_right)
