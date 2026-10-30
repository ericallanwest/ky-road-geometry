"""The project page's data: ``docs/data/`` from the analysis outputs.

* ``site.json``: headline numbers, the scores against KYTC's inventories, the demand summary;
* ``map.geojson``: every route cut into 0.1-mi pieces carrying the most demanding values in them
  (only directions traffic really drives: a divided highway's ``-000`` carriageway is driven with
  the measure and its ``-010`` twin against it);
* ``routes/<route>.json``: each route's profile every 0.01 mi (elevation, grade, curvature,
  superelevation, speed and demand each way) with KYTC's records and the detected features.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import shapely
from pyproj import Transformer

from . import network
from .grid import STEP_MI
from .network import EPSG

PIECE = 20            # grid points per map piece: 0.1 mi
PROFILE_EVERY = 2     # grid points per profile sample: 0.01 mi


def _r(v, nd=2):
    return None if v is None or not np.isfinite(v) else round(float(v), nd)


def _arr(a, nd=2):
    return [_r(v, nd) for v in a]


def _write(path: Path, obj) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, separators=(",", ":"))
    path.write_text(text)
    return len(text)


def directions(table: pd.DataFrame) -> dict[str, tuple[str, ...]]:
    """Which ways traffic drives each route: both, unless it's half of a divided highway."""
    twins = {r[:-3] for r in table["ROUTE"] if r.endswith("-010")}
    return {r: ("non",) if r.endswith("-010") else ("inv",) if r[:-3] in twins else ("inv", "non")
            for r in table["ROUTE"]}


def map_geojson(d: pd.DataFrame, ways: dict) -> dict:
    to_wgs = Transformer.from_crs(EPSG, 4326, always_xy=True)
    feats = []
    for (route, part), g in d.sort_values("grid").groupby(["route", "part"], sort=False):
        w = ways.get(route, ("inv", "non"))
        mu = np.nanmax(np.column_stack([g[f"mu_{x}"] for x in w]), axis=1)
        ms = np.nanmax(np.column_stack([g[f"mu_stop_{x}"] for x in w]), axis=1)
        v = np.nanmin(np.column_stack([g[f"speed_{x}"] for x in w]), axis=1)
        piece = (g["grid"].to_numpy() // PIECE)
        xs, ys = g["x"].to_numpy(), g["y"].to_numpy()
        for k in np.unique(piece):
            idx = np.flatnonzero(piece == k)
            stop = min(idx[-1] + 2, len(g))                  # reach the next piece's first point
            if stop - idx[0] < 2:
                continue
            line = shapely.simplify(shapely.linestrings(np.column_stack([xs[idx[0]:stop], ys[idx[0]:stop]])), 5.0)
            xy = shapely.get_coordinates(line)
            if len(xy) < 2:
                continue
            lon, lat = to_wgs.transform(xy[:, 0], xy[:, 1])
            feats.append({"type": "Feature", "properties": {
                "r": route, "m": round(float(k * PIECE * STEP_MI), 2),
                "mu": _r(np.nanmax(mu[idx])), "ms": _r(np.nanmax(ms[idx])), "v": _r(np.nanmin(v[idx]), 0),
                "g": _r(np.nanmax(np.abs(g["grade"].to_numpy()[idx])), 1),
                "d": _r(np.nanmax(np.abs(g["kappa"].to_numpy()[idx])) * 5729.58, 1)},
                "geometry": {"type": "LineString", "coordinates": np.round(np.column_stack([lon, lat]), 5).tolist()}})
    return {"type": "FeatureCollection", "features": feats}


def route_files(d: pd.DataFrame, table: pd.DataFrame, ways: dict, inv: dict, found: pd.DataFrame,
                dips: pd.DataFrame, out: Path) -> int:
    info = table.set_index("ROUTE")
    size = 0
    for route, g in d.groupby("route"):
        g = g[g["grid"] % PROFILE_EVERY == 0].sort_values("grid")
        k = g["grid"].to_numpy() // PROFILE_EVERY
        full = pd.RangeIndex(k.min(), k.max() + 1)
        col = lambda c: g.set_index(k)[c].reindex(full).to_numpy()  # noqa: E731
        bare = np.where(np.abs(col("z_bare") - col("z")) > 0.5, col("z_bare"), np.nan)
        prof = {"z": _arr(col("z"), 1), "bare": _arr(bare, 1), "grade": _arr(col("grade"), 2),
                "deg": _arr(col("kappa") * 5729.58, 1)}
        for w in ("inv", "non"):
            if w in ways.get(route, ()):
                prof[w] = {"e": _arr(col(f"e_{w}"), 1), "v": _arr(col(f"speed_{w}"), 0),
                           "mu": _arr(col(f"mu_{w}"), 3), "mu_stop": _arr(col(f"mu_stop_{w}"), 3)}

        def spans(t, *fields):
            t = t[t["route"] == route]
            return [[round(float(a), 3), round(float(b), 3), *[_r(x, 2) if isinstance(x, float) else x for x in xs]]
                    for a, b, *xs in zip(t["BEGIN_MP"], t["END_MP"], *[t[f] for f in fields])]

        r = info.loc[route]
        f = found[found["route"] == route]
        dp = dips[dips["route"] == route]
        size += _write(out / f"{route}.json", {
            "route": route, "name": None if pd.isna(r["RD_NAME"]) else str(r["RD_NAME"]), "county": str(r["CO_NAME"]),
            "x0": round(float(full[0] * PROFILE_EVERY * STEP_MI), 3), "dx": PROFILE_EVERY * STEP_MI,
            "ways": list(ways.get(route, ())), "profile": prof,
            "grades": spans(inv["grades"], "GRADEDIR", "GRADEPER"),
            "curves": spans(inv["curves"][inv["curves"]["CURVEDEG"].gt(0)], "CURVEDIR", "CURVEDEG"),
            "bridges": spans(inv["bridges"][inv["bridges"]["LOCATION_BR"] == "C"], "BR_TOTALLENGTH"),
            "found_curves": [[round(float(a), 3), round(float(b), 3), c, round(float(dg), 1)]
                             for a, b, c, dg in zip(f["m0"], f["m1"], f["direction"], f["degree"])],
            "dips": [[round(float(a), 3), round(float(b), 3), float(x)] for a, b, x in zip(dp["m0"], dp["m1"], dp["depth_ft"])],
        })
    return size


def build(data: Path, docs: Path, log=print) -> None:
    data, docs = Path(data), Path(docs)
    an, out = data / "analysis", docs / "data"
    table, parts = network.read(data / "network.parquet")
    net = json.loads((data / "network.json").read_text())
    d = pd.read_parquet(an / "demand.parquet")
    ways = directions(table)
    inv = {k: pd.read_parquet(data / "inventory" / f"{k}.parquet") for k in ("grades", "curves", "bridges")}
    found = pd.read_parquet(an / "curves.parquet")
    dips = pd.read_parquet(an / "bridge_dips.parquet")

    gj = map_geojson(d, ways)
    xy = np.vstack([f["geometry"]["coordinates"] for f in gj["features"]])
    log(f"map.geojson: {_write(out / 'map.geojson', gj) / 1e6:.1f} MB")
    log(f"routes/: {route_files(d, table, ways, inv, found, dips, out / 'routes') / 1e6:.1f} MB")

    # the page opens on a 5-25 mi route, at least half of it KYTC grade tangents to compare, where much of the
    # road asks a lot
    share = d.assign(hi=np.fmax(d["mu_inv"], d["mu_non"]) >= 0.2).groupby("route")["hi"].mean()
    length = table.set_index("ROUTE")["LENGTH_MI"].reindex(share.index)
    g = inv["grades"]
    tangent_mi = g[g["GRADEDIR"].isin(["U", "D"]) & g["GRADEPER"].notna()].groupby("route")["MILES"].sum()
    covered = (tangent_mi.reindex(share.index).fillna(0) / length) >= 0.5
    pick = share[length.between(5, 25) & covered].sort_values().index[-1]

    fits = pd.read_parquet(an / "grade_fits.parquet")
    tangents = fits[fits["GRADEDIR"].isin(["U", "D"]) & fits["dem_rmse"].notna()]
    manifest = [json.loads(line) for line in (data / "dem" / "manifest.jsonl").read_text().splitlines()]
    used = [p for r in manifest for p in r["phases"] if "blocks" in p]
    snap = sorted((data / "tiles").glob("tile_index_*.json"))[-1]
    _write(out / "site.json", {
        "network": {"counties": net["counties"], "routes": int(table["ROUTE"].str.endswith("-000").sum()),
                    "miles": round(float(table.loc[table["ROUTE"].str.endswith(("-000", "-010")), "LENGTH_MI"].sum()), 1),
                    "fetched": net["fetched"][:10], "service": net["service"],
                    "bounds": [[float(xy[:, 0].min()), float(xy[:, 1].min())], [float(xy[:, 0].max()), float(xy[:, 1].max())]]},
        "lidar": {"cells": len({(r["ix"], r["iy"]) for r in manifest if r["phases"]}),
                  "blocks": int(sum(p["blocks"] for p in used)),
                  "by_phase": pd.Series([p["phase"] for p in used]).value_counts().sort_index().to_dict(),
                  "snapshot": json.loads(snap.read_text())["taken"], "points": int(len(d)),
                  "with_elevation": float(d["z"].notna().mean())},
        "grades": json.loads((an / "grades.json").read_text()),
        "grade_fit_rmse_ft": float(tangents["dem_rmse"].median()),
        "curves": json.loads((an / "curves.json").read_text()),
        "demand": json.loads((an / "demand.json").read_text()),
        # KSP's crash reports stay local; these are counts and rates over the whole district
        "crashes": json.loads((an / "crashes.json").read_text()) if (an / "crashes.json").exists() else None,
        "featured": pick,
    })
    log(f"site.json written; featured {pick}")
