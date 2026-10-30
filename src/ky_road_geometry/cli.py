"""Command line: ``kyrg network|inventory|tiles|harvest|profile``."""
from __future__ import annotations

import argparse
import time
from pathlib import Path


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="kyrg")
    ap.add_argument("command", choices=["network", "inventory", "tiles", "harvest", "profile", "grades", "sight",
                                        "curves", "demand"])
    ap.add_argument("--data", type=Path, default=Path("data"))
    ap.add_argument("--counties", nargs="*", help="only routes in these counties (harvest, profile)")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--station-ft", type=float, default=50.0, help="chord length for curvature (curves)")
    a = ap.parse_args(argv)
    t0 = time.time()

    def log(msg):
        print(f"[{time.time() - t0:6.1f}s] {msg}", flush=True)

    from . import network
    if a.command == "network":
        network.build(a.data, log=log)
        return
    if a.command == "inventory":
        from . import inventory
        inventory.build(a.data, log=log)
        return

    table, parts = network.read(a.data / "network.parquet")
    if a.counties:
        keep = table.loc[table["CO_NAME"].isin(a.counties), "ROUTE"]
        parts = {r: parts[r] for r in keep}
        log(f"{len(parts)} routes in {', '.join(a.counties)}")
    if a.command == "tiles":
        from . import tiles
        tiles.snapshot(parts, a.data, log=log)
    if a.command == "harvest":
        from . import dem, tiles
        index = tiles.latest(a.data)
        near = tiles.cells_near(parts)
        index = index[[c in near for c in zip(index["ix"], index["iy"])]]
        got = dem.harvest(parts, index, a.data, workers=a.workers, log=log)
        log(f"{got['blocks'].sum():,.0f} blocks over {got[['ix', 'iy']].drop_duplicates().shape[0]:,} cells; "
            f"phases {got['phase'].value_counts().to_dict()}")
    if a.command == "profile":
        from . import dem, grid
        pts = grid.points(parts)
        pts["z"], pts["phase"] = dem.sample(a.data, pts["x"].to_numpy(), pts["y"].to_numpy())
        out = a.data / "profile.parquet"
        pts.to_parquet(out, index=False)
        log(f"{len(pts):,} points, {pts['z'].notna().mean():.2%} with elevation -> {out}")
    if a.command == "grades":
        import json

        import pandas as pd

        from . import vertical
        prof = pd.read_parquet(a.data / "profile.parquet")
        prof = prof[prof["route"].isin(parts)].reset_index(drop=True)
        prof["grade"] = vertical.point_grade(prof)
        out = a.data / "analysis"
        out.mkdir(exist_ok=True)

        # bridges: dips found in the bare-earth profile, checked against KYTC's bridge records
        dips = vertical.bridge_dips(prof)
        bridges = pd.read_parquet(a.data / "inventory" / "bridges.parquet")
        bridges = bridges[bridges["route"].isin(parts)]
        bridge_score = vertical.score_bridges(dips, bridges)
        dips.to_parquet(out / "bridge_dips.parquet", index=False)
        log(f"bridges: {json.dumps(bridge_score)}")
        # the road rides the deck over every dip and every recorded bridge (abutments one cell out)
        carried = bridges[bridges["LOCATION_BR"] == "C"]          # "B" bridges pass over the route
        part_of = prof.groupby(["route", "part"])["m"].agg(["min", "max"]).reset_index()
        rec = carried.merge(part_of, on="route")
        rec = rec[(rec["BEGIN_MP"] <= rec["max"]) & (rec["END_MP"] >= rec["min"])]
        spans = pd.concat([dips[["route", "part", "m0", "m1"]],
                           pd.DataFrame({"route": rec["route"], "part": rec["part"],
                                         "m0": rec["BEGIN_MP"] - 0.005, "m1": rec["END_MP"] + 0.005})])
        prof["z_bare"] = prof["z"]
        prof["z"] = vertical.deck(prof, spans)
        prof["grade"] = vertical.point_grade(prof)
        prof.to_parquet(out / "profile_road.parquet", index=False)

        inv = pd.read_parquet(a.data / "inventory" / "grades.parquet")
        fits = vertical.fit_records(prof, inv[inv["route"].isin(parts)])
        fits.to_parquet(out / "grade_fits.parquet", index=False)
        result = {**vertical.score(fits), "bridges": bridge_score}
        (out / "grades.json").write_text(json.dumps(result, indent=2, default=str))
        log(json.dumps({k: v for k, v in result.items() if k != "class_confusion"}, indent=1))
        log("\n" + pd.DataFrame(result["class_confusion"]).fillna(0).astype(int).to_string())
    if a.command == "curves":
        import json

        import pandas as pd

        from . import horizontal
        st = horizontal.curvature(horizontal.stations(parts, a.station_ft))
        found = horizontal.curves(st)
        out = a.data / "analysis"
        out.mkdir(exist_ok=True)
        st.to_parquet(out / "curvature.parquet", index=False)
        found.to_parquet(out / "curves.parquet", index=False)
        result = horizontal.score(found, pd.read_parquet(a.data / "inventory" / "curves.parquet"))
        result["station_ft"] = a.station_ft
        (out / "curves.json").write_text(json.dumps(result, indent=2, default=str))
        log(json.dumps({k: v for k, v in result.items() if k != "class_confusion"}, indent=1))
        log("\n" + pd.DataFrame(result["class_confusion"]).fillna(0).astype(int).to_string())
    if a.command == "demand":
        import json

        import numpy as np
        import pandas as pd

        from . import cross, demand
        out = a.data / "analysis"
        prof = pd.read_parquet(out / "profile_sight.parquet")
        prof = prof[prof["route"].isin(parts)].reset_index(drop=True)
        # curvature at each grid point, from the 50-ft chords' smoothed signal
        st = pd.read_parquet(out / "curvature.parquet")
        mid = st.assign(s=(st["s0"] + st["s1"]) / 2).dropna(subset=["kappa"])
        prof["kappa"] = np.nan
        for (route, part), g in prof.groupby(["route", "part"]).groups.items():
            k = mid[(mid["route"] == route) & (mid["part"] == part)]
            if len(k) >= 2:
                prof.loc[g, "kappa"] = np.interp(prof.loc[g, "s"], k["s"], k["kappa"])
        prof["kappa"] = prof["kappa"].fillna(0.0)
        cs = cross.cross_slopes(a.data, prof, log=log)
        prof = pd.concat([prof, cs], axis=1)
        prof = pd.concat([prof, demand.demand(prof)], axis=1)
        prof.to_parquet(out / "demand.parquet", index=False)
        both = lambda c: np.r_[prof[f"{c}_inv"], prof[f"{c}_non"]]  # noqa: E731
        mu, mu_stop = both("mu"), both("mu_stop")
        summary = {"point_directions": int(len(mu)),
                   "mu_quantiles": {q: round(float(np.nanquantile(mu, q)), 3) for q in (0.5, 0.9, 0.99)},
                   "mu_stop_quantiles": {q: round(float(np.nanquantile(mu_stop, q)), 3) for q in (0.5, 0.9, 0.99)},
                   "speed_below_limit_plus_5": float(np.mean(both("speed") < np.r_[prof["speed_mph"], prof["speed_mph"]] + 4.9))}
        (out / "demand.json").write_text(json.dumps(summary, indent=2))
        log(json.dumps(summary, indent=1))
    if a.command == "sight":
        import numpy as np
        import pandas as pd

        from . import sight, speed
        prof = pd.read_parquet(a.data / "analysis" / "profile_road.parquet")
        prof = prof[prof["route"].isin(parts)].reset_index(drop=True)
        sd = sight.sight_distances(prof)
        prof = pd.concat([prof, sd], axis=1)
        prof["speed_mph"] = speed.posted(prof, pd.read_parquet(a.data / "inventory" / "speed_limits.parquet"))
        v = prof["speed_mph"] + speed.OVER_MPH
        for d, sign in (("inv", 1), ("non", -1)):
            prof[f"ssd_need_{d}"] = sight.stopping_sight_required(v, sign * prof["grade"])
            prof[f"sight_short_{d}"] = prof[f"sight_{d}"] < prof[f"ssd_need_{d}"]
        prof.to_parquet(a.data / "analysis" / "profile_sight.parquet", index=False)
        short = np.r_[prof["sight_short_inv"], prof["sight_short_non"]]
        log(f"{len(prof):,} points; sight shorter than stopping distance at limit+5 on {short.mean():.1%} "
            f"of point-directions; median sight {np.median(np.r_[prof['sight_inv'], prof['sight_non']]):.0f} ft")


if __name__ == "__main__":
    main()
