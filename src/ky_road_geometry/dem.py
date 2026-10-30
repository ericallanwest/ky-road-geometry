"""Elevation from KyFromAbove, read once into a corridor around the network.

A 2-ft tile is 2,500 x 2,500 float32 pixels that LZW barely compresses, so reading a few points
from S3 costs most of 30 MB. Instead, ``harvest`` reads each tile the network crosses once, only
its 512 x 512 blocks that the corridor (``CORRIDOR_FT`` either side of a road) touches, blanks
everything outside the corridor and writes a local GeoTIFF on the same pixel grid: a few percent
of the tile. Where the newest phase leaves part of the corridor without data, the next phase is
harvested for that cell too.

``sample`` reads elevations from those files by bilinear interpolation between the four nearest
pixel centers, newest phase first.
"""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
import shapely
from rasterio.features import geometry_mask
from rasterio.windows import Window

from .tiles import CELL_FT

CORRIDOR_FT = 65.0
NODATA = -999999.0
COMPLETE = 0.999       # corridor share with data above which older phases aren't harvested
GDAL_ENV = {"GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR", "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
            "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES", "GDAL_HTTP_MAX_RETRY": "4",
            "GDAL_HTTP_RETRY_DELAY": "3", "VSI_CACHE": "FALSE"}


def local_path(root: Path, phase: int, ix: int, iy: int) -> Path:
    return Path(root) / "dem" / f"phase{phase}" / f"{ix}_{iy}.tif"


def _corridor(lines: shapely.STRtree, geoms: np.ndarray, ix: int, iy: int):
    """The corridor inside one cell, as a polygon (None if no road comes near it)."""
    box = shapely.box(ix * CELL_FT, iy * CELL_FT, (ix + 1) * CELL_FT, (iy + 1) * CELL_FT)
    hit = lines.query(shapely.buffer(box, CORRIDOR_FT))
    if not len(hit):
        return None
    poly = shapely.intersection(shapely.buffer(shapely.union_all(geoms[hit]), CORRIDOR_FT), box)
    return None if poly.is_empty else poly


def _harvest_tile(url: str, corridor, dest: Path) -> dict:
    """Copy the corridor's blocks of one tile into ``dest``. Returns what it read."""
    with rasterio.Env(**GDAL_ENV), rasterio.open(url) as src:
        inside = geometry_mask([corridor], out_shape=src.shape, transform=src.transform, invert=True)
        profile = {"driver": "GTiff", "width": src.width, "height": src.height, "count": 1,
                   "dtype": "float32", "crs": src.crs, "transform": src.transform, "nodata": np.nan,
                   "tiled": True, "blockxsize": 512, "blockysize": 512, "compress": "zstd",
                   "predictor": 3, "sparse_ok": True}
        bh, bw = src.block_shapes[0]
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".part")
        blocks = valid = 0
        with rasterio.open(tmp, "w", **profile) as dst:
            for r0 in range(0, src.height, bh):
                for c0 in range(0, src.width, bw):
                    want = inside[r0:r0 + bh, c0:c0 + bw]
                    if not want.any():
                        continue
                    w = Window(c0, r0, want.shape[1], want.shape[0])
                    z = src.read(1, window=w).astype(np.float32)
                    z[(z <= NODATA + 1) | ~want] = np.nan
                    dst.write(z, 1, window=w)
                    blocks += 1
                    valid += int(np.isfinite(z).sum())
        tmp.replace(dest)
        return {"blocks": blocks, "corridor_px": int(inside.sum()), "valid_px": valid}


def harvest(parts: dict[str, list[np.ndarray]], index: pd.DataFrame, root: Path, workers: int = 8,
            log=print) -> pd.DataFrame:
    """Harvest every cell the network touches. Resumable: cells already done are skipped."""
    root = Path(root)
    manifest = root / "dem" / "manifest.jsonl"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if manifest.exists():
        for line in manifest.read_text().splitlines():
            rec = json.loads(line)
            done.add((rec["ix"], rec["iy"]))
    # every vertex-to-vertex segment of the network, so a cell's query stays local
    segs = np.concatenate([np.stack([p[:-1, :2], p[1:, :2]], axis=1) for ps in parts.values() for p in ps])
    pieces = shapely.linestrings(segs)
    tree = shapely.STRtree(pieces)
    cells = index.groupby(["ix", "iy"])
    todo = [(ix, iy, g) for (ix, iy), g in cells if (ix, iy) not in done]
    log(f"{len(cells):,} cells, {len(todo):,} to harvest")
    lock = threading.Lock()

    def one(ix, iy, g):
        corridor = _corridor(tree, pieces, ix, iy)
        rec = {"ix": int(ix), "iy": int(iy), "phases": []}
        if corridor is not None:
            for _, t in g.sort_values("phase", ascending=False).iterrows():
                try:
                    got = _harvest_tile(t["url"], corridor, local_path(root, t["phase"], ix, iy))
                except rasterio.errors.RasterioIOError as e:
                    rec["phases"].append({"phase": int(t["phase"]), "error": str(e)[:200]})
                    continue
                share = got["valid_px"] / max(got["corridor_px"], 1)
                rec["phases"].append({"phase": int(t["phase"]), "year": int(t["year"]), **got,
                                      "share": round(share, 5)})
                if share >= COMPLETE:
                    break
        with lock:
            with manifest.open("a") as f:
                f.write(json.dumps(rec) + "\n")
        return rec

    with ThreadPoolExecutor(workers) as pool:
        futures = [pool.submit(one, ix, iy, g) for ix, iy, g in todo]
        for k, fut in enumerate(as_completed(futures), 1):
            fut.result()
            if k % 50 == 0 or k == len(futures):
                log(f"harvested {k:,} / {len(futures):,} cells")
    recs = [json.loads(line) for line in manifest.read_text().splitlines()]
    return pd.DataFrame([{"ix": r["ix"], "iy": r["iy"], **p} for r in recs for p in r["phases"]])


def _bilinear(z: np.ndarray, transform, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    col = (x - transform.c) / transform.a - 0.5
    row = (y - transform.f) / transform.e - 0.5
    c0, r0 = np.floor(col).astype(int), np.floor(row).astype(int)
    fc, fr = col - c0, row - r0
    h, w = z.shape
    ok = (c0 >= 0) & (r0 >= 0) & (c0 + 1 < w) & (r0 + 1 < h)
    out = np.full(len(x), np.nan)
    c, r, a, b = c0[ok], r0[ok], fc[ok], fr[ok]
    out[ok] = (z[r, c] * (1 - a) * (1 - b) + z[r, c + 1] * a * (1 - b)
               + z[r + 1, c] * (1 - a) * b + z[r + 1, c + 1] * a * b)
    # at a tile's outer pixel row or column, the nearest pixel stands in
    miss = ~np.isfinite(out)
    rn, cn = np.round(row[miss]).astype(int), np.round(col[miss]).astype(int)
    inb = (rn >= 0) & (cn >= 0) & (rn < h) & (cn < w)
    vals = np.full(miss.sum(), np.nan)
    vals[inb] = z[rn[inb], cn[inb]]
    out[miss] = vals
    return out


def sample(root: Path, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Elevation (ft, NAVD88) and the phase it came from at each point; NaN and 0 outside the
    harvested corridor."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    z = np.full(len(x), np.nan)
    phase = np.zeros(len(x), dtype=np.int8)
    ix, iy = np.floor(x / CELL_FT).astype(int), np.floor(y / CELL_FT).astype(int)
    for p in (3, 2, 1):
        todo = np.flatnonzero(~np.isfinite(z))
        if not len(todo):
            break
        for (cx, cy), idx in pd.Series(todo).groupby([ix[todo], iy[todo]]):
            path = local_path(root, p, cx, cy)
            if not path.exists():
                continue
            with rasterio.open(path) as src:
                rows, cols = rasterio.transform.rowcol(src.transform, x[idx.values], y[idx.values])
                r0, r1 = max(min(rows) - 2, 0), min(max(rows) + 3, src.height)
                c0, c1 = max(min(cols) - 2, 0), min(max(cols) + 3, src.width)
                w = Window(c0, r0, c1 - c0, r1 - r0)
                arr = src.read(1, window=w)
                vals = _bilinear(arr, src.window_transform(w), x[idx.values], y[idx.values])
            good = np.isfinite(vals)
            z[idx.values[good]] = vals[good]
            phase[idx.values[good]] = p
    return z, phase
