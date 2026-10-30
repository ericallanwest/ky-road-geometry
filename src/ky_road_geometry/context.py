"""How rugged and how rural the district's roads are, by the USDA Economic Research Service's measures.

ERS's Area and Road Ruggedness Scales (2020 census tracts) rate each tract by the terrain ruggedness
index of its land and along its roads: level, nearly level, slightly, moderately or highly rugged
(and, for land, extremely rugged). The same file carries each tract's primary Rural-Urban Commuting
Area code: metropolitan (1-3), micropolitan (4-6), small town (7-9) or rural (10). Tracts are placed
with the Census Bureau's 2020 TIGER/Line boundaries. Every grid point takes the codes of the tract it
falls in, and the road's geometry, its demand and its crash rates are summed up by ruggedness and by
rurality.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import shapefile
import shapely
from pyproj import Transformer

from . import crashes, network
from .grid import STEP_MI
from .network import EPSG
from .site import PIECE

RUCA_GROUP = {**{c: "Metropolitan" for c in (1, 2, 3)}, **{c: "Micropolitan" for c in (4, 5, 6)},
              **{c: "Small town" for c in (7, 8, 9)}, 10: "Rural"}
RRS_ORDER = ["Level", "Nearly level", "Slightly rugged", "Moderately rugged", "Highly rugged"]


def read_ers(path: Path, counties: list[str]) -> pd.DataFrame:
    """ERS's 2020 ruggedness and RUCA codes for the tracts of these Kentucky counties."""
    e = pd.read_csv(path, encoding="latin-1", dtype={"TractFIPS20": str, "CountyFIPS20": str, "StateFIPS20": str})
    e = e[(e["StateFIPS20"] == "21") & e["CountyName20"].isin([f"{c} County" for c in counties])]
    return pd.DataFrame({"geoid": e["TractFIPS20"], "county": e["CountyName20"].str.removesuffix(" County"),
                         "rrs": e["RRS"], "rrs_name": e["RRSDescription"], "ars_name": e["ARSDescription"],
                         "ruca": e["PrimaryRUCA"], "ruca_group": e["PrimaryRUCA"].map(RUCA_GROUP),
                         "population": e["Population"], "land_sq_mi": e["LandArea"]}).reset_index(drop=True)


def read_tracts(path: Path, geoids) -> pd.DataFrame:
    """TIGER/Line 2020 tract polygons (a state's zip), in the network's coordinates."""
    to_ky = Transformer.from_crs(4269, EPSG, always_xy=True)
    rows = []
    with shapefile.Reader(str(path)) as r:
        for sr in r.iterShapeRecords():
            gid = sr.record["GEOID"]
            if gid in geoids:
                geom = shapely.geometry.shape(sr.shape.__geo_interface__)
                rows.append({"geoid": gid, "geometry": shapely.transform(geom, lambda xy: np.column_stack(
                    to_ky.transform(xy[:, 0], xy[:, 1])))})
    return pd.DataFrame(rows)


def tract_of(x: np.ndarray, y: np.ndarray, tracts: pd.DataFrame) -> np.ndarray:
    """The GEOID of the tract each point falls in (None outside them all)."""
    tree = shapely.STRtree(tracts["geometry"].to_numpy())
    pt, tr = tree.query(shapely.points(x, y), predicate="within")
    out = np.full(len(x), None, dtype=object)
    out[pt] = tracts["geoid"].to_numpy()[tr]
    return out


def summarize(pts: pd.DataFrame, by: str, top: float, pcs: pd.DataFrame | None, curves_at: pd.Series) -> list[dict]:
    """Road, geometry, demand and crash rates for each value of ``by``."""
    rows = []
    for key, g in pts.groupby(by):
        miles = len(g) * STEP_MI
        row = {by: key, "miles": round(miles, 1), "share": round(len(g) / len(pts), 3),
               "curves_per_mile": round(float((curves_at == key).sum() / miles), 2),
               "grade_p90": round(float(np.nanquantile(np.abs(g["grade"]), 0.9)), 1),
               "short_sight": round(float(g["short_sight"].mean()), 3),
               "mu_p50": round(float(np.nanmedian(g["mu_pt"])), 3),
               "mu_p90": round(float(np.nanquantile(g["mu_pt"], 0.9)), 3),
               "top_tenth": round(float((g["mu_pt"] >= top).mean()), 3)}
        if pcs is not None:
            p = pcs[pcs[by] == key]
            vmt = p["vmt_100m"].sum()
            row.update({"vmt_100m": round(float(vmt), 2),
                        **{f"{k}_rate": round(float(p[k].sum() / vmt), 1) if vmt else None for k in crashes.OUTCOMES}})
        rows.append(row)
    return rows


def build(data: Path, log=print) -> dict:
    data = Path(data)
    an = data / "analysis"
    table, _ = network.read(data / "network.parquet")
    ers = read_ers(next((data / "context").glob("ers_arrs_2020/*.csv")), sorted(table["CO_NAME"].unique()))
    tracts = read_tracts(next((data / "context").glob("tiger2020_tract_21/*.zip")), set(ers["geoid"]))
    log(f"{len(ers)} tracts with ERS codes, {len(tracts)} with boundaries")

    d = pd.read_parquet(an / "demand.parquet", columns=["route", "part", "grid", "m", "x", "y", "grade",
                                                         "sight_short_inv", "sight_short_non", "mu_inv", "mu_non",
                                                         "mu_stop_inv", "mu_stop_non"])
    d = d.join(crashes.ways(d, table))
    d["short_sight"] = d["sight_short_inv"] | d["sight_short_non"]
    d["geoid"] = tract_of(d["x"].to_numpy(), d["y"].to_numpy(), tracts)
    log(f"{d['geoid'].notna().mean():.2%} of grid points inside a tract")
    d = d.merge(ers[["geoid", "rrs_name", "ruca_group"]], on="geoid", how="left")
    pts = d[d["geoid"].notna()]
    top = float(np.nanquantile(pts["mu_pt"], 0.9))        # the district's most demanding tenth of road

    # each curve found in the lidar counts where its middle is
    found = pd.read_parquet(an / "curves.parquet")
    found["m"] = (found["m0"] + found["m1"]) / 2
    key = d.sort_values("m")[["route", "part", "m", "rrs_name", "ruca_group"]]
    at = pd.merge_asof(found.sort_values("m"), key, on="m", by=["route", "part"], direction="nearest")

    pcs = None
    if (an / "crash_pieces.parquet").exists():          # crash counts by piece, from `kyrg crashes`
        pcs = pd.read_parquet(an / "crash_pieces.parquet")
        mid = d.assign(piece=d["grid"] // PIECE).groupby(["route", "part", "piece"])[["rrs_name", "ruca_group"]].first()
        pcs = pcs.join(mid, on=["route", "part", "piece"])
        pcs = pcs[pcs["mu"].notna() & (pcs["aadt"] > 0)]

    by_rrs = summarize(pts, "rrs_name", top, pcs, at["rrs_name"])
    by_rrs.sort(key=lambda r: RRS_ORDER.index(r["rrs_name"]))
    by_ruca = summarize(pts, "ruca_group", top, pcs, at["ruca_group"])
    by_ruca.sort(key=lambda r: list(dict.fromkeys(RUCA_GROUP.values())).index(r["ruca_group"]))
    ky = pd.read_csv(next((data / "context").glob("ers_arrs_2020/*.csv")), encoding="latin-1",
                     usecols=["StateFIPS20", "RRSDescription"], dtype={"StateFIPS20": str})
    ky = ky[ky["StateFIPS20"] == "21"]
    result = {
        "tracts": int(len(ers)), "top_tenth_mu": round(top, 3),
        "tracts_by_rrs": ers["rrs_name"].value_counts().reindex(RRS_ORDER).dropna().astype(int).to_dict(),
        "tracts_by_ruca": ers["ruca_group"].value_counts().to_dict(),
        "kentucky_highly_rugged": int((ky["RRSDescription"] == "Highly rugged").sum()),
        "kentucky_tracts": int(len(ky)),
        "by_rrs": by_rrs, "by_ruca": by_ruca,
    }
    (an / "context.json").write_text(json.dumps(result, indent=2))

    # the tracts as a map backdrop: simplified outlines with their codes
    to_wgs = Transformer.from_crs(EPSG, 4326, always_xy=True)
    feats = []
    for t in tracts.merge(ers, on="geoid").itertuples():
        g = shapely.simplify(t.geometry, 60.0)
        g = shapely.transform(g, lambda xy: np.round(np.column_stack(to_wgs.transform(xy[:, 0], xy[:, 1])), 5))
        feats.append({"type": "Feature", "geometry": shapely.geometry.mapping(g),
                      "properties": {"geoid": t.geoid, "county": t.county, "rrs": t.rrs_name,
                                     "ruca": t.ruca_group, "pop": int(t.population)}})
    (an / "tracts.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats},
                                                  separators=(",", ":")))
    log(json.dumps({k: v for k, v in result.items() if not k.startswith("by_")}, indent=1))
    log("\n" + pd.DataFrame(by_rrs).to_string() + "\n" + pd.DataFrame(by_ruca).to_string())
    return result
