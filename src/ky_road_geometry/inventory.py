"""KYTC's public roadway inventories: the ground truth the derived geometry is checked against.

Each is an event layer on the same LRS: a route (``RT_UNIQUE``), a milepoint span and its
attributes. Horizontal curves carry the degree of curve (``CURVEDEG``) and its class
(``CURVECLS``, A to F); grades carry the percent grade (``GRADEPER``) and its class
(``GRADECLS``, A to F); speed limits carry ``SPEEDLIM``; traffic counts carry the AADT of the
latest count (``LASTCNT``, counted in ``LASTCNTYR``). Only attributes are fetched: the spans
are placed on the network by route and milepoint. ``-010`` twin carriageways can list a span with
its milepoints falling; spans are stored low to high, with the original order kept in
``reversed``.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pandas as pd

from .network import query, route_id

BASE = "https://services2.arcgis.com/CcI36Pduqd0OR4W9/ArcGIS/rest/services/"
LAYERS = {
    "curves": ("Horizontal_Curves_Public/FeatureServer/85",
               ["CURVECLS", "D_CURVECLS", "CURVEDEG", "CURVEDIR", "D_CURVEDIR", "INV_TYPE"]),
    "grades": ("Grade_Vertical_Curves_Public/FeatureServer/0",
               ["GRADECLS", "D_GRADECLS", "GRADEDIR", "D_GRADEDIR", "GRADEPER", "GRADEELV", "INV_TYPE"]),
    "speed_limits": ("Speed_Limits_Public/FeatureServer/0", ["SPEEDLIM"]),
    # bridge records name their span LEADING_MP-TRAILING_MP; LOCATION_BR "C" is carried on the bridge
    "bridges": ("Kentucky_Bridge_Locations_view/FeatureServer/0",
                ["LOCATION_BR", "D_LOCATION_BR", "BR_BRIDGEID", "BR_TOTALLENGTH", "BR_FEATUREINTERSECTED",
                 "TYPE", "MIDPOINT_MP"]),
    # traffic sections: the geometry is each section's middle third, where the counter sits; BASE_BMP-BASE_EMP
    # is the whole section (KYTC_Traffic_Counts has the same fields but only District 5)
    "aadt": ("Traffic_Section_Middle_Third/FeatureServer/0",
             ["MILES", "LASTCNT", "LASTCNTYR", "ADTSTATN", "D_ADTSRCE"]),
}
COMMON = ["RT_UNIQUE", "BEGIN_MP", "END_MP", "MILES", "GOV_LEVEL", "CNTY_NAME", "RT_PREFIX"]
SPAN = {"bridges": ("LEADING_MP", "TRAILING_MP"), "aadt": ("BASE_BMP", "BASE_EMP")}
NUMERIC_DISTRICT = {"bridges", "aadt"}   # these layers store DISTRICT as a number, the others as text


def fetch(layer: str, district: str = "12", prefixes=("US", "KY"), log=print) -> pd.DataFrame:
    path, fields = LAYERS[layer]
    kinds = ",".join(f"'{p}'" for p in prefixes)
    lead, trail = SPAN.get(layer, ("BEGIN_MP", "END_MP"))
    common = ["RT_UNIQUE", lead, trail, "GOV_LEVEL", "CNTY_NAME", "RT_PREFIX"] + (
        ["MILES"] if layer not in SPAN else [])
    dist = district if layer in NUMERIC_DISTRICT else f"'{district}'"
    feats = query(BASE + path + "/query",
                  {"where": f"DISTRICT = {dist} AND RT_PREFIX IN ({kinds})",
                   "outFields": ",".join(common + fields), "returnGeometry": "false"}, log=log)
    d = pd.DataFrame([f["attributes"] for f in feats]).rename(columns={lead: "BEGIN_MP", trail: "END_MP"})
    d.insert(0, "route", d.pop("RT_UNIQUE").map(route_id))
    d["BEGIN_MP"], d["END_MP"] = pd.to_numeric(d["BEGIN_MP"]), pd.to_numeric(d["END_MP"])   # text in some layers
    d["reversed"] = d["END_MP"] < d["BEGIN_MP"]
    lo, hi = d[["BEGIN_MP", "END_MP"]].min(axis=1), d[["BEGIN_MP", "END_MP"]].max(axis=1)
    d["BEGIN_MP"], d["END_MP"] = lo, hi
    return d.sort_values(["route", "BEGIN_MP"]).reset_index(drop=True)


def build(out_dir: Path, district: str = "12", layers=None, log=print) -> None:
    """Fetch the inventories (all, or the ``layers`` named), each stamped with when it was fetched."""
    out = Path(out_dir) / "inventory"
    out.mkdir(parents=True, exist_ok=True)
    meta_path = out / "inventory.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {"district": district, "layers": {}}
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    meta.setdefault("fetched", now)
    for layer in layers or LAYERS:
        path, _ = LAYERS[layer]
        d = fetch(layer, district, log=log)
        d.to_parquet(out / f"{layer}.parquet", index=False)
        meta["layers"][layer] = {"url": BASE + path, "records": len(d), "fetched": now}
        log(f"{layer}: {len(d):,} records")
    meta_path.write_text(json.dumps(meta, indent=2))
