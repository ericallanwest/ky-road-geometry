"""KyFromAbove's DEM tile indexes, pinned as a dated snapshot.

KyFromAbove publishes bare-earth DEMs as cloud-optimized GeoTIFFs on AWS
(``s3://kyfromabove/elevation/DEM``): 5,000-ft tiles in EPSG:3089, 2 ft per pixel for Phases 2
and 3 and 5 ft for Phase 1, each phase indexed by a GeoPackage of tile footprints. The indexes
change whenever new lidar lands, so which tile is read at each place comes from a snapshot
taken once and kept with the results, not from whatever the bucket says today.

Every tile sits on a regular 5,000-ft grid, so a cell number ``(ix, iy) = floor(x / 5000),
floor(y / 5000)`` finds it. Where phases overlap the newest wins, falling back to an older one
where it has no data.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import struct
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import shapely
from pyproj import Transformer

from .network import EPSG

BUCKET = "https://kyfromabove.s3.us-west-2.amazonaws.com/elevation/DEM"
INDEXES = {   # phase: (file, table)
    3: ("kyfromabove_phase3_dem_5k_grid.gpkg", "phase3_tile_index"),
    2: ("kyfromabove_phase2_5k_dem_grid.gpkg", "kyfromabove_phase2_5k_dem_grid"),
    1: ("kyfromabove_phase1_dem_5k_grid.gpkg", "kyfromabove_phase1_dem_5k_grid"),
}
PIXEL_FT = {3: 2.0, 2: 2.0, 1: 5.0}
CELL_FT = 5000.0


def _center(blob: bytes) -> tuple[float, float]:
    """The center of a GeoPackage geometry's envelope, read from the blob's header. (Phases 1 and
    2 store footprints as MultiSurface, a curve type Shapely doesn't read; the header is enough.)"""
    if (blob[3] >> 1) & 0b111 == 0:
        raise ValueError("geometry blob without an envelope")
    minx, maxx, miny, maxy = struct.unpack("<4d" if blob[3] & 1 else ">4d", blob[8:40])
    return (minx + maxx) / 2, (miny + maxy) / 2


def read_index(path: Path, table: str, phase: int) -> pd.DataFrame:
    con = sqlite3.connect(path)
    srs = con.execute("select srs_id from gpkg_geometry_columns where table_name = ?", (table,)).fetchone()[0]
    rows = con.execute(f'select geom, tilename, key, aws_url from "{table}"').fetchall()
    con.close()
    centers = np.array([_center(r[0]) for r in rows])
    xy = Transformer.from_crs(srs, EPSG, always_xy=True).transform(centers[:, 0], centers[:, 1])
    return pd.DataFrame({
        "phase": phase, "ix": np.floor(np.asarray(xy[0]) / CELL_FT).astype(int),
        "iy": np.floor(np.asarray(xy[1]) / CELL_FT).astype(int),
        "tilename": [r[1] for r in rows], "year": [int(r[2].split("_")[1]) for r in rows],
        "url": [r[3] for r in rows]})


def cells_near(parts: dict[str, list[np.ndarray]], pad_ft: float = 100.0) -> set[tuple[int, int]]:
    """Grid cells within ``pad_ft`` of any route (vertices densified to 50 ft)."""
    cells = set()
    for ps in parts.values():
        for p in ps:
            line = shapely.segmentize(shapely.linestrings(p[:, :2]), 50.0)
            xy = shapely.get_coordinates(line)
            for dx in (-pad_ft, 0.0, pad_ft):
                for dy in (-pad_ft, 0.0, pad_ft):
                    k = np.floor((xy + [dx, dy]) / CELL_FT).astype(int)
                    cells.update(map(tuple, np.unique(k, axis=0)))
    return cells


def snapshot(parts, out_dir: Path, log=print) -> Path:
    """Download the three indexes, keep the tiles near the network, and pin them by date."""
    out = Path(out_dir) / "tiles"
    out.mkdir(parents=True, exist_ok=True)
    near = cells_near(parts)
    frames, sources = [], {}
    for phase, (name, table) in INDEXES.items():
        url = f"{BUCKET}/TileGrids/{name}"
        r = requests.get(url, timeout=600)
        r.raise_for_status()
        (out / name).write_bytes(r.content)
        sources[phase] = {"url": url, "last_modified": r.headers.get("Last-Modified")}
        d = read_index(out / name, table, phase)
        d = (d[[c in near for c in zip(d["ix"], d["iy"])]]
             .sort_values("year", ascending=False).drop_duplicates(["ix", "iy"]))   # newest wins
        frames.append(d)
        log(f"phase {phase}: {len(d):,} tiles near the network")
    index = pd.concat(frames, ignore_index=True)
    stamp = dt.date.today().strftime("%Y%m%d")
    path = out / f"tile_index_{stamp}.parquet"
    index.to_parquet(path, index=False)
    (out / f"tile_index_{stamp}.json").write_text(json.dumps(
        {"taken": stamp, "cells": len(near), "tiles": len(index), "sources": sources}, indent=2))
    log(f"{len(near):,} cells; {len(index):,} tiles -> {path}")
    return path


def latest(out_dir: Path) -> pd.DataFrame:
    paths = sorted((Path(out_dir) / "tiles").glob("tile_index_*.parquet"))
    if not paths:
        raise FileNotFoundError("no tile index snapshot: run `kyrg tiles`")
    return pd.read_parquet(paths[-1])
