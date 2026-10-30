"""Posted speed along the grid, from KYTC's speed-limit records.

Each grid point takes the limit of the record whose span covers it. Where none does, Kentucky's
statutory limit for a rural state highway, 55 mph, applies. Friction demand uses the posted
limit plus 5 mph as the speed drivers actually carry.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_MPH = 55.0
OVER_MPH = 5.0


def posted(prof: pd.DataFrame, limits: pd.DataFrame) -> np.ndarray:
    pts = prof[["route", "m"]].reset_index().sort_values("m")
    rec = limits[["route", "BEGIN_MP", "END_MP", "SPEEDLIM"]].dropna().sort_values("BEGIN_MP")
    hit = pd.merge_asof(pts, rec, left_on="m", right_on="BEGIN_MP", by="route")
    mph = np.where(hit["SPEEDLIM"].notna() & (hit["m"] < hit["END_MP"]), hit["SPEEDLIM"], DEFAULT_MPH)
    return pd.Series(mph, index=hit["index"]).reindex(prof.index).to_numpy()
