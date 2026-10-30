"""Grade from the lidar profile, and how it compares with KYTC's grade inventory.

The profile is the DEM sampled at every 0.005-mi grid point along a route's centerline. Grade at
a point is the central difference over its two neighbors, about 52.8 ft apart, on the ground
distance ``s``.

KYTC's grade inventory splits each route (inventory direction) into records: uphill (``U``) and
downhill (``D``) tangents carrying their percent grade (``GRADEPER``, unsigned) and class
(``GRADECLS``), and crests (``C``) and sags (``S``). Each record is scored against the profile
over its own span: a least-squares line through the profile gives a tangent's grade, and a
parabola's curvature tells a crest from a sag.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

GRADE_CLASSES = [("A", 0.0), ("B", 0.45), ("C", 2.45), ("D", 4.45), ("E", 6.45), ("F", 8.45)]


def grade_class(pct: np.ndarray) -> np.ndarray:
    """KYTC's grade classes: A 0.0-0.4, B 0.5-2.4, C 2.5-4.4, D 4.5-6.4, E 6.5-8.4, F 8.5+."""
    edges = [lo for _, lo in GRADE_CLASSES[1:]]
    labels = np.array([c for c, _ in GRADE_CLASSES])
    return labels[np.searchsorted(edges, np.abs(pct), side="right")]


def point_grade(prof: pd.DataFrame) -> np.ndarray:
    """Percent grade at each grid point: central difference within its route part."""
    p = prof.sort_values(["route", "part", "grid"])
    same = lambda k: (p["route"].shift(k) == p["route"]) & (p["part"].shift(k) == p["part"])  # noqa: E731
    z, s = p["z"], p["s"]
    nxt, prv = same(-1), same(1)
    dz = np.where(nxt & prv, z.shift(-1) - z.shift(1), np.where(nxt, z.shift(-1) - z, z - z.shift(1)))
    ds = np.where(nxt & prv, s.shift(-1) - s.shift(1), np.where(nxt, s.shift(-1) - s, s - s.shift(1)))
    return pd.Series(100 * dz / ds, index=p.index).reindex(prof.index).to_numpy()


STEEP = 15.0          # percent: steeper than any state route here, so a valley under a bridge
MAX_SPAN_FT = 3000.0  # longest dip treated as one bridge
MIN_DEPTH_FT = 4.0    # how far the ground must drop below the deck line


def bridge_dips(prof: pd.DataFrame) -> pd.DataFrame:
    """Where the bare-earth profile drops into a valley the road crosses on a bridge.

    A bare-earth DEM has the bridges taken out, so under one the profile falls steeply to the
    ground and climbs back. A dip is a fall steeper than ``STEEP`` followed within
    ``MAX_SPAN_FT`` by a climb as steep, at least ``MIN_DEPTH_FT`` below the straight line between
    the last ordinary points either side (the abutments). Returns one row per dip: route, part,
    abutment measures and distances, and depth.
    """
    rows = []
    for (route, part), g in prof.sort_values("grid").groupby(["route", "part"], sort=False):
        s, z, m = g["s"].to_numpy(), g["z"].to_numpy(), g["m"].to_numpy()
        gr = g["grade"].to_numpy()
        i = 0
        while i < len(s):
            if not gr[i] < -STEEP:
                i += 1
                continue
            j = i + 1
            while j < len(s) and s[j] - s[i] <= MAX_SPAN_FT and not gr[j] > STEEP:
                j += 1
            if j >= len(s) or s[j] - s[i] > MAX_SPAN_FT:
                i += 1
                continue
            a = i - 1
            while a > 0 and abs(gr[a]) >= STEEP:
                a -= 1
            b = j + 1
            while b < len(s) - 1 and abs(gr[b]) >= STEEP:
                b += 1
            a, b = max(a, 0), min(b, len(s) - 1)
            chord = z[a] + (z[b] - z[a]) * (s[a:b + 1] - s[a]) / (s[b] - s[a])
            depth = float(np.nanmax(chord - z[a:b + 1]))
            if depth >= MIN_DEPTH_FT:
                rows.append({"route": route, "part": part, "m0": m[a], "m1": m[b], "s0": s[a], "s1": s[b],
                             "depth_ft": round(depth, 1)})
            i = b + 1
    return pd.DataFrame(rows, columns=["route", "part", "m0", "m1", "s0", "s1", "depth_ft"])


def deck(prof: pd.DataFrame, spans: pd.DataFrame) -> np.ndarray:
    """The road surface: the profile, with each bridge span replaced by the straight line between
    its abutments. ``spans`` has route, part, m0, m1."""
    z = prof["z"].to_numpy().copy()
    key = prof["route"].to_numpy(), prof["part"].to_numpy(), prof["m"].to_numpy()
    for sp in spans.itertuples(index=False):
        on = (key[0] == sp.route) & (key[1] == sp.part)
        idx = np.flatnonzero(on & (key[2] >= sp.m0 - 1e-9) & (key[2] <= sp.m1 + 1e-9))
        if len(idx) < 3:
            continue
        s = prof["s"].to_numpy()[idx]
        order = np.argsort(s)
        idx, s = idx[order], s[order]
        z[idx] = z[idx[0]] + (z[idx[-1]] - z[idx[0]]) * (s - s[0]) / (s[-1] - s[0])
    return z


def score_bridges(dips: pd.DataFrame, bridges: pd.DataFrame, pad_mi: float = 0.01) -> dict:
    """Dips against KYTC's bridges carried by these routes: how many of each are matched. (Only
    ``LOCATION_BR`` "C", carried on the bridge: "B" is a bridge over the route, with no dip.)"""
    carried = bridges[bridges["LOCATION_BR"] == "C"].copy()

    def overlaps(r0, a0, a1, b0, b1):
        return (a0 <= b1 + pad_mi) & (a1 >= b0 - pad_mi)

    dips = dips.copy()
    dips["bridge"] = False
    carried["found"] = False
    for route, d in dips.groupby("route"):
        b = carried[carried["route"] == route]
        for i, row in d.iterrows():
            hit = overlaps(route, row["m0"], row["m1"], b["BEGIN_MP"], b["END_MP"])
            if hit.any():
                dips.at[i, "bridge"] = True
                carried.loc[b.index[hit], "found"] = True
    length = pd.cut(carried["BR_TOTALLENGTH"], [0, 50, 100, 250, 500, 1e6],
                    labels=["<50 ft", "50-100", "100-250", "250-500", "500+"])
    return {"dips": len(dips), "dips_on_a_bridge": float(dips["bridge"].mean()) if len(dips) else None,
            "bridges": len(carried), "bridges_found": float(carried["found"].mean()),
            "found_by_length": carried.groupby(length, observed=True)["found"].mean().round(3).to_dict()}


def fit_records(prof: pd.DataFrame, records: pd.DataFrame, min_points: int = 3) -> pd.DataFrame:
    """For each inventory record, a line and a parabola through the profile points on its span.

    Returns the records with ``n`` (points), ``dem_grade`` (percent, signed in the inventory
    direction), ``dem_curv`` (second derivative, percent per 100 ft: positive is a sag) and
    ``dem_rmse`` (ft, the line's residual).
    """
    rec = records.reset_index(drop=True).assign(rec=lambda d: np.arange(len(d)))
    pts = prof.dropna(subset=["z"]).sort_values("m")
    hit = pd.merge_asof(pts, rec[["route", "BEGIN_MP", "END_MP", "rec"]].sort_values("BEGIN_MP"),
                        left_on="m", right_on="BEGIN_MP", by="route")
    hit = hit[hit["rec"].notna() & (hit["m"] < hit["END_MP"])]
    out = {}
    for r, g in hit.groupby("rec"):
        if len(g) < min_points or g["part"].nunique() > 1:   # s restarts at a gap between parts
            continue
        s = g["s"].to_numpy() - g["s"].mean()
        z = g["z"].to_numpy()
        slope, icpt = np.polyfit(s, z, 1)
        curv = np.polyfit(s, z, 2)[0] * 2 if len(g) >= 4 else np.nan
        out[int(r)] = (len(g), 100 * slope, curv * 100 * 100, float(np.sqrt(np.mean((z - (slope * s + icpt)) ** 2))))
    fit = pd.DataFrame.from_dict(out, orient="index", columns=["n", "dem_grade", "dem_curv", "dem_rmse"])
    return rec.join(fit, on="rec")


CLEAN_RMSE_FT = 1.0   # a tangent whose profile sits within this of a straight line is a clean fit


def score(fits: pd.DataFrame) -> dict:
    """How the profile agrees with the inventory: tangents' direction, grade and class; crests
    and sags' shape.

    Both sides have errors, so the summary is robust: medians, a rank correlation, and the same
    figures again over the clean fits (the profile within ``CLEAN_RMSE_FT`` of a straight line).
    A tangent whose profile is far from straight is more often a bad record (one Knott County
    "tangent" runs 4.5 mi at 8.4%, which would climb 2,000 ft) than a bad profile.
    """
    t = fits[fits["GRADEDIR"].isin(["U", "D"]) & fits["GRADEPER"].notna() & fits["dem_grade"].notna()]
    vc = fits[fits["GRADEDIR"].isin(["C", "S"]) & fits["dem_curv"].notna()]
    shape_ok = np.where(vc["GRADEDIR"] == "S", vc["dem_curv"] > 0, vc["dem_curv"] < 0)

    def summary(t):
        sign_ok = np.where(t["GRADEDIR"] == "U", t["dem_grade"] > 0, t["dem_grade"] < 0)
        steep = (t["GRADEPER"] >= 0.5).to_numpy()   # a nearly flat record has no direction to get wrong
        lidar = t["dem_grade"].abs()
        err = lidar - t["GRADEPER"]
        return {
            "tangents": int(len(t)), "miles": round(float(t["MILES"].sum()), 1),
            "direction_agrees": float(sign_ok[steep].mean()),
            "direction_agrees_by_miles": float(np.average(sign_ok[steep], weights=t["MILES"][steep])),
            "grade_abs_error_p50": float(err.abs().median()), "grade_bias_p50": float(err.median()),
            "rank_corr": float(lidar.rank().corr(t["GRADEPER"].rank())),
            "class_exact": float((t["GRADECLS"].to_numpy() == grade_class(lidar)).mean()),
            "class_within_one": float((np.abs(_cls_index(t["GRADECLS"]) - _cls_index(grade_class(lidar))) <= 1).mean()),
        }

    clean = t[t["dem_rmse"] < CLEAN_RMSE_FT]
    return {
        "all": summary(t), "clean": summary(clean),
        "class_confusion": pd.crosstab(clean["GRADECLS"], grade_class(clean["dem_grade"]),
                                       rownames=["inventory"], colnames=["lidar"]).to_dict(),
        "vertical_curves": int(len(vc)), "crest_sag_agrees": float(shape_ok.mean()),
    }


def _cls_index(labels) -> np.ndarray:
    order = {c: k for k, (c, _) in enumerate(GRADE_CLASSES)}
    return np.array([order.get(c, -9) for c in labels])
