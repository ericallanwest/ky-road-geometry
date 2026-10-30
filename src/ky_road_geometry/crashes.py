"""Crash reports on the district's state routes: a check on the geometry and on the demand.

The Kentucky State Police publish crash reports through a public search, crashinformationky.org
(``scripts/kycrash_pull.py`` pulls a district's, a year at a time). The reports are KSP's, all
rights reserved: they stay in ``data/``, and only counts and rates built from them are published.

**Placing a crash.** A report names its road (``RdwyNumber``, e.g. ``KY0194``) and county, and
gives coordinates. It's placed on that route in that county, at the grid point nearest its
coordinates, if one is within ``SNAP_FT``; on a divided highway, on the nearer carriageway.
Parking-lot crashes are left out.

**Kinds.** Roadway departures are the single-vehicle crashes that run off the road or into
something beside it, the kind grip and geometry bear on. Animal crashes are the placebo: geometry
shouldn't explain them. The surface is as the officer recorded it: wet (including standing or
moving water), dry, or snow and ice.

**What the officer saw.** Each report records whether the road was straight or curved there, and
level, on a grade or at a hillcrest. The curve code is checked against the curves found in the
lidar and KYTC's curve inventory, within ``NEAR_FT`` of the crash; the hillcrest code against the
places where a crest leaves less sight, one way or the other, than it takes to stop.

**Rates.** The network is cut into the map's 0.1-mi pieces, each with its most demanding point
(either way of travel), the AADT of the KYTC traffic section around it (halved on each
carriageway of a divided highway) and its crashes. Rates are crashes per 100 million
vehicle-miles by decile of demand. A negative binomial regression of each piece's count on its
demand, with log AADT and the piece's vehicle-miles as exposure, gives the rate ratio per 0.1 of
demand.
"""
from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import Transformer
from scipy.spatial import cKDTree

from . import network
from .grid import STEP_MI
from .network import EPSG
from .site import PIECE

SNAP_FT = 150.0
NEAR_FT = 100.0
DEPARTURE = {
    "RAN OFF ROADWAY (1 VEHICLE WITH/EARTH EMBANKMENT/DITCH)",
    "COLLISION WITH FIXED OBJECT NON - INTERSECTION - FIRST EVENT COLLISION 09 - 32 EXCLUDING 16",
    "OVERTURNED IN ROADWAY",
}
ANIMAL = {"COLLISION WITH ANIMAL NON INTERSECTION", "COLLISION WITH ANIMAL IN INTERSECTION"}
SURFACE = {"DRY": "dry", "WET": "wet", "WATER (STANDING OR MOVING)": "wet", "ICE": "winter", "SNOW/SLUSH": "winter"}
OUTCOMES = {"wet_departure": "Roadway departures, wet", "dry_departure": "Roadway departures, dry",
            "animal": "Animal crashes (placebo)"}


def read(raw: Path) -> pd.DataFrame:
    """One row per crash report in the extract zips, with the fields this check uses."""
    frames = []
    for z in sorted(Path(raw).glob("*.zip")):
        with zipfile.ZipFile(z) as zf, zf.open("Incident.txt") as f:
            frames.append(pd.read_csv(f, dtype=str, encoding="utf-8-sig"))
    d = pd.concat(frames, ignore_index=True).drop_duplicates("IncidentID")
    character = d["RdwyCharacter"].fillna("")
    kind = d["DirAnalysisCode"].map(lambda c: "departure" if c in DEPARTURE else "animal" if c in ANIMAL else "other")
    return pd.DataFrame({
        "year": pd.to_datetime(d["CollisionDate"], format="%m/%d/%Y %I:%M:%S %p").dt.year,
        "county": d["County"].str.strip().str.upper(),
        "road": d["RdwyNumber"].fillna("").str.strip(),
        "lat": pd.to_numeric(d["Latitude"], errors="coerce"),
        "lon": pd.to_numeric(d["Longitude"], errors="coerce"),
        "kind": kind,
        "surface": d["RdwyConditionCode"].map(SURFACE).fillna("other"),
        "parking_lot": d["DirAnalysisCode"].eq("COLLISION IN PARKING LOT"),
        # the officer's view of the road: STRAIGHT or CURVE, & LEVEL, GRADE or HILLCREST
        "coded_curve": np.where(character == "", None, character.str.startswith("CURVE")),
        "coded_crest": np.where(character == "", None, character.str.endswith("HILLCREST")),
    })


def place(crashes: pd.DataFrame, pts: pd.DataFrame, table: pd.DataFrame) -> pd.DataFrame:
    """Each crash on its own route (either carriageway) at the nearest grid point, if within ``SNAP_FT``.
    ``pts``: route, part, grid, m, x, y."""
    code = {name.upper(): route[:3] for name, route in zip(table["CO_NAME"], table["ROUTE"])}
    ok = crashes["road"].str.fullmatch(r"(US|KY)\d{4}") & crashes["county"].isin(code) & ~crashes["parking_lot"]
    c = crashes[ok].copy()
    c["base"] = c["county"].map(code) + "-" + c["road"].str[:2] + "-" + c["road"].str[2:6]
    c["x"], c["y"] = Transformer.from_crs(4326, EPSG, always_xy=True).transform(c["lon"].to_numpy(), c["lat"].to_numpy())
    pts = pts[pts["route"].str.endswith(("-000", "-010"))].reset_index(drop=True)
    pts_base = pts["route"].str[:11]
    out = []
    for base, g in c.groupby("base"):
        idx = np.flatnonzero(pts_base == base)
        if not len(idx):
            continue
        dist, k = cKDTree(pts.loc[idx, ["x", "y"]].to_numpy()).query(g[["x", "y"]].to_numpy())
        hit = pts.loc[idx[k], ["route", "part", "grid", "m"]].set_index(g.index)
        out.append(hit.assign(snap_ft=dist))
    placed = c.join(pd.concat(out), how="left")
    placed["placed"] = placed["snap_ft"] <= SNAP_FT
    return placed


def flags(d: pd.DataFrame, found: pd.DataFrame, inv_curves: pd.DataFrame) -> pd.DataFrame:
    """Per grid point: within ``NEAR_FT`` of a curve found in the lidar, or of a KYTC-recorded curve; and
    whether a crest leaves less sight than it takes to stop, either way."""
    near_mi = NEAR_FT / 5280
    out = pd.DataFrame(False, index=d.index, columns=["curve_lidar", "curve_kytc"])
    out["short_sight"] = (d["sight_short_inv"] | d["sight_short_non"]).to_numpy()
    inv = inv_curves.groupby("route")
    fc = found.groupby(["route", "part"])
    for (route, part), g in d.groupby(["route", "part"]):
        m = g["m"].to_numpy()
        lo, hi = m.min(), m.max()
        for col, spans in (("curve_lidar", fc.get_group((route, part))[["m0", "m1"]].to_numpy()
                            if (route, part) in fc.groups else np.empty((0, 2))),
                           ("curve_kytc", inv.get_group(route)[["BEGIN_MP", "END_MP"]].to_numpy()
                            if route in inv.groups else np.empty((0, 2)))):
            spans = spans[(spans[:, 1] >= lo - near_mi) & (spans[:, 0] <= hi + near_mi)]
            hit = np.zeros(len(m), bool)
            for a, b in spans:
                hit |= (m >= min(a, b) - near_mi) & (m <= max(a, b) + near_mi)
            out.loc[g.index, col] = hit
    return out


def pieces(d: pd.DataFrame, table: pd.DataFrame, aadt: pd.DataFrame, years: int) -> pd.DataFrame:
    """The map's 0.1-mi pieces: most demanding point (each way traffic drives), AADT, vehicle-miles."""
    tw = table[table["ROUTE"].str.endswith("-010")]
    twin = tw.set_index(tw["ROUTE"].str[:-4])[["BEG_MP", "END_MP"]]
    p = d.assign(piece=d["grid"] // PIECE)
    # divided where the route is a second carriageway, or the mainline alongside one
    base = p["route"].str[:-4]
    tw = twin.reindex(base)
    alongside = (p["route"].str.endswith("-000") & tw["BEG_MP"].notna().to_numpy()
                 & (p["m"].to_numpy() >= tw["BEG_MP"].to_numpy() - 0.01) & (p["m"].to_numpy() <= tw["END_MP"].to_numpy() + 0.01))
    p["divided"] = p["route"].str.endswith("-010") | alongside
    # demand each way traffic drives: one way on a carriageway of a divided highway
    p["mu_pt"] = np.where(p["route"].str.endswith("-010"), p["mu_non"],
                          np.where(p["divided"], p["mu_inv"], np.fmax(p["mu_inv"], p["mu_non"])))
    p["ms_pt"] = np.where(p["route"].str.endswith("-010"), p["mu_stop_non"],
                          np.where(p["divided"], p["mu_stop_inv"], np.fmax(p["mu_stop_inv"], p["mu_stop_non"])))
    g = p.groupby(["route", "part", "piece"])
    out = g.agg(m=("m", "mean"), n=("grid", "size"), mu=("mu_pt", "max"), mu_stop=("ms_pt", "max"),
                divided=("divided", "mean")).reset_index()
    out["divided"] = out["divided"] > 0.5
    out["miles"] = out["n"] * STEP_MI
    # AADT of the traffic section around the piece's middle, on the mainline's measure
    out["aadt"] = np.nan
    spans = aadt[aadt["LASTCNT"] > 0]
    by_route = {r: s.sort_values("BEGIN_MP") for r, s in spans.groupby("route")}
    mainline = out["route"].str[:-4] + "-000"
    for r, idx in out.groupby(mainline).groups.items():
        s = by_route.get(r)
        if s is None:
            continue
        m = out.loc[idx, "m"].to_numpy()
        k = np.searchsorted(s["BEGIN_MP"].to_numpy(), m, side="right") - 1
        inside = (k >= 0) & (m <= s["END_MP"].to_numpy()[np.clip(k, 0, None)] + 1e-6)
        out.loc[idx, "aadt"] = np.where(inside, s["LASTCNT"].to_numpy()[np.clip(k, 0, None)], np.nan)
    out["aadt"] = np.where(out["divided"], out["aadt"] / 2, out["aadt"])
    out["vmt_100m"] = out["aadt"] * 365.25 * years * out["miles"] / 1e8
    return out


def counts(pcs: pd.DataFrame, placed: pd.DataFrame) -> pd.DataFrame:
    """Each piece's crashes, by outcome."""
    c = placed[placed["placed"]].assign(piece=lambda x: (x["grid"] // PIECE).astype(int),
                                        part=lambda x: x["part"].astype(int))
    c["wet_departure"] = (c["kind"] == "departure") & (c["surface"] == "wet")
    c["dry_departure"] = (c["kind"] == "departure") & (c["surface"] == "dry")
    c["animal"] = c["kind"] == "animal"
    c["all"] = True
    n = c.groupby(["route", "part", "piece"])[["wet_departure", "dry_departure", "animal", "all"]].sum()
    return pcs.join(n, on=["route", "part", "piece"]).fillna({k: 0 for k in n.columns})


def by_decile(pcs: pd.DataFrame) -> list[dict]:
    """Crash rates per 100 million vehicle-miles in each decile of demand."""
    q = pcs.assign(decile=pd.qcut(pcs["mu"], 10, labels=False) + 1)
    rows = []
    for dec, g in q.groupby("decile"):
        vmt = g["vmt_100m"].sum()
        dep = g["wet_departure"].sum() + g["dry_departure"].sum()
        rows.append({"decile": int(dec), "mu_lo": round(float(g["mu"].min()), 3), "mu_hi": round(float(g["mu"].max()), 3),
                     "miles": round(float(g["miles"].sum()), 1), "vmt_100m": round(float(vmt), 3),
                     **{k: int(g[k].sum()) for k in OUTCOMES},
                     **{f"{k}_rate": round(float(g[k].sum() / vmt), 1) for k in OUTCOMES},
                     "wet_share": round(float(g["wet_departure"].sum() / dep), 3) if dep else None})
    return rows


def model(pcs: pd.DataFrame) -> dict:
    """Negative binomial: count ~ demand + log AADT, offset log vehicle-miles. Rate ratio per 0.1 of demand."""
    import statsmodels.api as sm

    X = sm.add_constant(pd.DataFrame({"mu10": pcs["mu"] * 10, "log_aadt": np.log(pcs["aadt"])}))
    out = {}
    for k in OUTCOMES:
        fit = sm.NegativeBinomial(pcs[k], X, offset=np.log(pcs["vmt_100m"])).fit(disp=0, maxiter=200)
        lo, hi = fit.conf_int().loc["mu10"]
        out[k] = {"rate_ratio": round(float(np.exp(fit.params["mu10"])), 3),
                  "ci": [round(float(np.exp(lo)), 3), round(float(np.exp(hi)), 3)],
                  "p": float(fit.pvalues["mu10"]), "crashes": int(pcs[k].sum()), "converged": bool(fit.mle_retvals["converged"])}
    return out


def officer_check(placed: pd.DataFrame, fl: pd.DataFrame, d: pd.DataFrame) -> dict:
    """How often the officer's curve and hillcrest codes land on the geometry, against how much road is like that."""
    key = ["route", "part", "grid"]
    at = placed[placed["placed"]].merge(d[key].join(fl), on=key, how="left")
    out = {"crashes": int(len(at)), "network_share": {c: round(float(fl[c].mean()), 3) for c in fl.columns}}
    for code, cols in (("coded_curve", ("curve_lidar", "curve_kytc")), ("coded_crest", ("short_sight",))):
        known = at[at[code].notna()]
        yes, no = known[known[code].astype(bool)], known[~known[code].astype(bool)]
        out[code] = {"coded": int(len(yes)), "not_coded": int(len(no)),
                     **{c: {"when_coded": round(float(yes[c].mean()), 3), "when_not": round(float(no[c].mean()), 3)}
                        for c in cols}}
    return out


def build(data: Path, raw: Path, log=print) -> dict:
    an = Path(data) / "analysis"
    table, _ = network.read(Path(data) / "network.parquet")
    d = pd.read_parquet(an / "demand.parquet", columns=["route", "part", "grid", "m", "x", "y", "sight_short_inv",
                                                         "sight_short_non", "mu_inv", "mu_non", "mu_stop_inv",
                                                         "mu_stop_non"])
    crashes = read(raw)
    years = sorted(crashes["year"].unique())
    log(f"{len(crashes):,} crash reports, {years[0]}-{years[-1]}")
    placed = place(crashes, d[["route", "part", "grid", "m", "x", "y"]], table)
    log(f"{len(placed):,} on the district's US and KY routes by road number; {placed['placed'].mean():.1%} placed "
        f"within {SNAP_FT:.0f} ft (median {placed['snap_ft'].median():.0f} ft)")

    fl = flags(d, pd.read_parquet(an / "curves.parquet"), pd.read_parquet(Path(data) / "inventory" / "curves.parquet"))
    d = d.join(fl)
    officer = officer_check(placed, fl, d)
    log(json.dumps(officer, indent=1))

    pcs = counts(pieces(d, table, pd.read_parquet(Path(data) / "inventory" / "aadt.parquet"), len(years)), placed)
    use = pcs[pcs["mu"].notna() & (pcs["aadt"] > 0)].reset_index(drop=True)
    log(f"{len(use):,} of {len(pcs):,} pieces have demand and AADT ({use['miles'].sum():,.0f} mi, "
        f"{use['all'].sum():,.0f} of {pcs['all'].sum():,.0f} placed crashes)")
    deciles = by_decile(use)
    nb = model(use)
    log(json.dumps(nb, indent=1))

    pcs.to_parquet(an / "crash_pieces.parquet", index=False)        # crash counts by place: stays local
    result = {
        "source": "Kentucky State Police crash reports (crashinformationky.org)",
        "years": [int(years[0]), int(years[-1])], "reports": int(len(crashes)),
        "on_routes": int(len(placed)), "placed": int(placed["placed"].sum()), "snap_ft": SNAP_FT,
        "kinds": {k: int(v) for k, v in placed.loc[placed["placed"], "kind"].value_counts().items()},
        "surface_departures": {k: int(v) for k, v in placed.loc[placed["placed"] & (placed["kind"] == "departure"),
                                                                 "surface"].value_counts().items()},
        "officer": officer, "near_ft": NEAR_FT,
        "pieces": {"n": int(len(use)), "miles": round(float(use["miles"].sum()), 1),
                   "vmt_100m": round(float(use["vmt_100m"].sum()), 2)},
        "deciles": deciles, "model": nb,
    }
    (an / "crashes.json").write_text(json.dumps(result, indent=2))
    return result
