"""Sight distance along the lidar profile, in each direction of travel.

A driver's eye is 3.5 ft above the road and an object worth stopping for is 2.0 ft high (AASHTO).
Over a crest the road itself hides the object: the sight distance available at a point is how far
ahead the object stays visible, computed directly on the profile, with no fitted curves. From the
eye, the road ahead hides everything below the steepest line of sight to it so far; the object is
seen while its top clears that line.

At night in a sag the headlights are the limit: a beam from 2.0 ft, aimed 1 degree above the
vehicle's own grade, reaches the road where the road climbs into it.

Both are capped at ``MAX_FT``; sight lines never cross a gap between route parts. Lateral sight
lines around horizontal curves (a cut slope on the inside) aren't modeled.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

EYE_FT = 3.5
OBJECT_FT = 2.0
HEADLIGHT_FT = 2.0
BEAM_DEG = 1.0
MAX_FT = 1500.0


def _scan(s: np.ndarray, z: np.ndarray, grade: np.ndarray, last: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Sight and headlight distance looking toward increasing index, for every point at once.
    ``last`` is the last index of each point's part."""
    n = len(s)
    i = np.arange(n)
    eye = z + EYE_FT
    beam = np.tan(np.arctan(grade / 100) + np.radians(BEAM_DEG))
    steepest = np.full(n, -np.inf)            # steepest line from the eye to the road so far
    sight = np.full(n, np.nan)
    light = np.full(n, np.nan)
    d = 1
    while True:
        j = i + d
        ok = j <= np.minimum(last, n - 1)
        jj = np.where(ok, j, 0)
        run = s[jj] - s
        ok &= run <= MAX_FT
        if not ok.any():
            break
        with np.errstate(divide="ignore", invalid="ignore"):
            top = (z[jj] + OBJECT_FT - eye) / run
            hidden = ok & np.isnan(sight) & (top < steepest)
            sight[hidden] = s[np.where(hidden, jj - 1, 0)][hidden] - s[hidden]
            reach = ok & np.isnan(light) & (z[jj] >= z + HEADLIGHT_FT + beam * run)
            light[reach] = run[reach]
            steepest = np.where(ok, np.maximum(steepest, (z[jj] - eye) / run), steepest)
        d += 1
    # never hidden within the cap (or the part ran out): at least as far as was looked
    end = np.minimum(last, n - 1)
    seen = np.minimum(s[end] - s, MAX_FT)
    return np.where(np.isnan(sight), seen, sight), np.where(np.isnan(light), seen, light)


def sight_distances(prof: pd.DataFrame) -> pd.DataFrame:
    """Per grid point: crest sight and sag headlight distance, with the measure (``inv``) and
    against it (``non``). ``prof`` needs route, part, grid, s, z, grade."""
    p = prof.sort_values(["route", "part", "grid"]).reset_index()
    key = p["route"].astype(str) + "|" + p["part"].astype(str)
    last = p.groupby(key).cumcount(ascending=False).to_numpy() + np.arange(len(p))
    s, z, g = p["s"].to_numpy(), p["z"].to_numpy(), p["grade"].to_numpy()
    sight_inv, light_inv = _scan(s, z, g, last)
    # against the measure: the same scan on the reversed profile (distance and grade flip)
    first = p.groupby(key).cumcount().to_numpy()
    rev = slice(None, None, -1)
    last_rev = (len(p) - 1 - (np.arange(len(p)) - first))[rev]
    sight_non, light_non = _scan(-s[rev], z[rev], -g[rev], last_rev)
    out = pd.DataFrame({"sight_inv": sight_inv, "headlight_inv": light_inv,
                        "sight_non": sight_non[rev], "headlight_non": light_non[rev]}, index=p["index"])
    return out.reindex(prof.index)


def stopping_sight_required(v_mph, grade_pct=0.0, t_s: float = 2.5, a_fts2: float = 11.2) -> np.ndarray:
    """AASHTO stopping sight distance (ft) at ``v_mph``, on a grade (downhill negative)."""
    v = np.asarray(v_mph, float)
    return 1.47 * v * t_s + v ** 2 / (30 * (a_fts2 / 32.2 + np.asarray(grade_pct) / 100))
