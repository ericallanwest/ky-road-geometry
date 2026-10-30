"""Horizontal curves from the network geometry, and how they compare with KYTC's curve inventory.

Curvature is read off the route lines themselves. Each part is resampled into equal chords
(``station_ft``); curvature at the joint between two chords is the change of heading over the
distance between their midpoints (radians per foot, positive turning left in the direction of
increasing measure). Its signal has a telling shape: tangents sit near zero, a circular arc holds a
constant value, and spirals ramp between the two.

A curve is a run of one sign above the tangent boundary (radius ``TANGENT_RADIUS_FT``), with dropouts
of up to ``MAX_BRIDGE`` stations bridged, turning at least ``MIN_DEFLECTION_DEG``, found on a lightly
smoothed signal. Its radius comes from the curvature averaged over the curve's core, the stations
at ``CORE`` or more of its peak: averaging the spirals in would bias the radius long. A circle
fitted to the core (Kasa's algebraic fit) gives a residual that flags a "curve" that is really two
curves, or a curve with a spiral swallowed.

The settings were chosen against KYTC's curve inventory for District 12 (``score``): 50-ft chords
and the smoothed signal over the core give degrees that match the inventory's without bias (median
ratio 1.01). The raw signal's vertex kinks scatter sharp curves' degrees; 26.4-ft chords find a few
more curves but read them 21% sharp.

Degree of curve, as KYTC records it, is the arc definition: D = 5729.58 / R.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TANGENT_RADIUS_FT = 3048.0
MAX_BRIDGE = 2
MIN_STATIONS = 3
MIN_DEFLECTION_DEG = 1.0
CORE = 0.8
HALF_WINDOW = 2
DEG_PER_RAD_FT = 5729.578   # degree of curve (arc definition) = this x curvature (rad/ft)
CURVE_CLASSES = [("A", 0.0), ("B", 3.45), ("C", 5.45), ("D", 8.45), ("E", 13.95), ("F", 27.95)]


def curve_class(deg: np.ndarray) -> np.ndarray:
    """KYTC's curve classes by degree: A 0-3.4, B 3.5-5.4, C 5.5-8.4, D 8.5-13.9, E 14-27.9, F 28+."""
    labels = np.array([c for c, _ in CURVE_CLASSES])
    return labels[np.searchsorted([lo for _, lo in CURVE_CLASSES[1:]], np.asarray(deg), side="right")]


STATION_FT = 50.0


def stations(parts: dict[str, list[np.ndarray]], station_ft: float = STATION_FT) -> pd.DataFrame:
    """Each route part resampled into chords of ``station_ft``, with the measure at their ends."""
    frames = []
    for route, ps in parts.items():
        for k, xym in enumerate(ps):
            cum = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(xym[:, :2], axis=0).T))])
            if cum[-1] < 3 * station_ft:
                continue
            s = np.append(np.arange(0.0, cum[-1], station_ft), cum[-1])
            if s[-1] - s[-2] < 0.5 * station_ft:     # fold a stub of a last chord into the one before
                s = np.delete(s, -2)
            x, y, m = (np.interp(s, cum, xym[:, i]) for i in range(3))
            frames.append(pd.DataFrame({
                "route": route, "part": k, "station": np.arange(len(s) - 1), "s0": s[:-1], "s1": s[1:],
                "m0": m[:-1], "m1": m[1:], "x0": x[:-1], "y0": y[:-1], "x1": x[1:], "y1": y[1:]}))
    st = pd.concat(frames, ignore_index=True)
    st["chord"] = np.hypot(st["x1"] - st["x0"], st["y1"] - st["y0"])
    st["heading"] = np.arctan2(st["y1"] - st["y0"], st["x1"] - st["x0"])
    return st


def curvature(st: pd.DataFrame, half_window: int = HALF_WINDOW) -> pd.DataFrame:
    """Turn ``dtheta`` (rad) at the end of each chord, raw ``kappa_raw`` and smoothed ``kappa`` (rad/ft)."""
    st = st.sort_values(["route", "part", "station"]).reset_index(drop=True)
    same = (st["route"].shift(-1) == st["route"]) & (st["part"].shift(-1) == st["part"])
    turn = (st["heading"].shift(-1) - st["heading"] + np.pi) % (2 * np.pi) - np.pi
    st["dtheta"] = np.where(same, turn, np.nan)
    st["kappa_raw"] = st["dtheta"] / (0.5 * (st["chord"] + st["chord"].shift(-1)))
    st["kappa"] = st.groupby(["route", "part"])["kappa_raw"].transform(
        lambda k: k.rolling(2 * half_window + 1, center=True, min_periods=1).mean())
    return st


def _runs(kappa: np.ndarray) -> np.ndarray:
    """Curve labels (0 = tangent) for one part: same-sign runs above the tangent boundary, with
    short dropouts bridged."""
    sign = np.sign(np.nan_to_num(kappa)) * (np.abs(np.nan_to_num(kappa)) >= 1 / TANGENT_RADIUS_FT)
    idx = np.flatnonzero(sign != 0)
    label = np.zeros(len(kappa), dtype=int)
    if not len(idx):
        return label
    new = np.r_[True, (np.diff(idx) > MAX_BRIDGE + 1) | (sign[idx][1:] != sign[idx][:-1])]
    run = np.cumsum(new)
    for r in np.unique(run):
        members = idx[run == r]
        label[members[0]:members[-1] + 1] = r
    return label


def kasa(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Kasa's algebraic least-squares circle: radius and RMS distance of the points from it."""
    A = np.column_stack([x, y, np.ones_like(x)])
    b = -(x ** 2 + y ** 2)
    (D, E, F), *_ = np.linalg.lstsq(A, b, rcond=None)
    cx, cy = -D / 2, -E / 2
    r = np.sqrt(max(cx ** 2 + cy ** 2 - F, 0.0))
    return float(r), float(np.sqrt(np.mean((np.hypot(x - cx, y - cy) - r) ** 2)))


def curves(st: pd.DataFrame, radius_from: str = "smooth") -> pd.DataFrame:
    """One row per detected curve: where it is (measures), which way it turns, and its shape.
    The core's mean curvature comes from ``kappa_raw`` or, with ``radius_from="smooth"``, ``kappa``."""
    rows = []
    for (route, part), g in st.groupby(["route", "part"], sort=False):
        lab = _runs(g["kappa"].to_numpy())
        for r in np.unique(lab[lab > 0]):
            c = g[lab == r]
            k = c["kappa"].to_numpy()
            kr = c["kappa_raw" if radius_from == "raw" else "kappa"].to_numpy()
            deflection = float(np.degrees(abs(np.nansum(c["dtheta"]))))
            if len(c) < MIN_STATIONS or deflection < MIN_DEFLECTION_DEG:
                continue
            core = np.abs(k) >= CORE * np.nanmax(np.abs(k))
            if not np.isfinite(kr[core]).any():
                continue
            kappa_core = float(np.nanmean(np.abs(kr[core])))
            cc = c[core]
            fit_r, rms = kasa(np.r_[cc["x0"], cc["x1"].iloc[-1:]], np.r_[cc["y0"], cc["y1"].iloc[-1:]]) \
                if core.sum() >= 2 else (np.nan, np.nan)
            rows.append({"route": route, "part": part, "m0": c["m0"].iloc[0], "m1": c["m1"].iloc[-1],
                         "length_ft": float(c["chord"].sum()), "direction": "L" if np.nansum(k) > 0 else "R",
                         "radius_ft": 1 / kappa_core if kappa_core > 0 else np.inf,
                         "degree": DEG_PER_RAD_FT * kappa_core, "deflection_deg": deflection,
                         "core_stations": int(core.sum()), "fit_radius_ft": fit_r, "fit_rms_ft": rms})
    out = pd.DataFrame(rows)
    out["cls"] = curve_class(out["degree"])
    return out


WELL_MATCHED_IOU = 0.5   # a detected curve and a record overlapping by half their union describe one curve


def score(found: pd.DataFrame, inventory: pd.DataFrame) -> dict:
    """Detected curves against KYTC's curve records (those with a degree and a direction), on the
    routes the inventory covers (it has no -010 twin carriageways).

    Each record pairs with the detected curve it overlaps most. Finding is any overlap; the shape
    (degree, direction, class) is compared over the well-matched pairs, which overlap by at least
    ``WELL_MATCHED_IOU`` of their union: where a detected curve spans several records (a compound
    or reverse curve) or a record spans several curves, their degrees don't describe the same arc.
    """
    found = found[found["route"].isin(inventory["route"].unique())].reset_index(drop=True)
    inv = inventory[inventory["CURVEDEG"].gt(0) & inventory["CURVEDIR"].isin(["L", "R"])].reset_index(drop=True)
    pairs = []
    for route, g in inv.groupby("route"):
        f = found[found["route"] == route]
        for i, rec in g.iterrows():
            ov = np.minimum(f["m1"], rec["END_MP"]) - np.maximum(f["m0"], rec["BEGIN_MP"])
            if (ov > 0).any():
                j = ov.idxmax()
                union = max(f.at[j, "m1"], rec["END_MP"]) - min(f.at[j, "m0"], rec["BEGIN_MP"])
                pairs.append((i, j, ov[j] / union))
    inv["found"] = inv.index.isin([i for i, _, _ in pairs])
    matched_found = found.index.isin({j for _, j, _ in pairs})
    well = [(i, j) for i, j, iou in pairs if iou >= WELL_MATCHED_IOU]
    hit = inv.loc[[i for i, _ in well]].reset_index(drop=True)
    got = found.loc[[j for _, j in well]].reset_index(drop=True)
    return {
        "inventory_curves": len(inv), "found": float(inv["found"].mean()),
        "found_by_class": inv.groupby("CURVECLS")["found"].mean().round(3).to_dict(),
        "detected": len(found), "detected_on_a_recorded_curve": float(matched_found.mean()),
        "detected_on_a_recorded_curve_by_class": pd.Series(matched_found).groupby(found["cls"].to_numpy())
        .mean().round(3).to_dict(),
        "well_matched": len(well),
        "direction_agrees": float((got["direction"] == hit["CURVEDIR"]).mean()),
        "degree_ratio_p50": float((got["degree"] / hit["CURVEDEG"]).median()),
        "degree_rank_corr": float(got["degree"].rank().corr(hit["CURVEDEG"].rank())),
        "degree_log_corr": float(np.corrcoef(np.log(got["degree"]), np.log(hit["CURVEDEG"]))[0, 1]),
        "class_exact": float((got["cls"] == hit["CURVECLS"]).mean()),
        "class_within_one": float((np.abs(_idx(got["cls"]) - _idx(hit["CURVECLS"])) <= 1).mean()),
        "class_confusion": pd.crosstab(hit["CURVECLS"], got["cls"], rownames=["inventory"],
                                       colnames=["derived"]).to_dict(),
    }


def _idx(labels) -> np.ndarray:
    order = {c: k for k, (c, _) in enumerate(CURVE_CLASSES)}
    return np.array([order[c] for c in labels])
