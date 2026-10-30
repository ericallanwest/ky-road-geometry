import numpy as np
import pandas as pd
from pyproj import Transformer

from ky_road_geometry import crashes

TABLE = pd.DataFrame({"ROUTE": ["036-KY-0003-000", "036-KY-0003-010"], "CO_NAME": ["Floyd", "Floyd"],
                      "BEG_MP": [0.0, 0.1], "END_MP": [0.2, 0.2]})


def grid(route, y0, n=41):
    g = np.arange(n)
    return pd.DataFrame({"route": route, "part": 0, "grid": g, "m": g * 0.005, "x": 5_000_000 + g * 26.4,
                         "y": y0})


def test_a_crash_lands_on_the_nearer_carriageway_of_its_own_route_or_not_at_all():
    pts = pd.concat([grid("036-KY-0003-000", 3_700_000.0), grid("036-KY-0003-010", 3_700_060.0)])
    to_ll = Transformer.from_crs(crashes.EPSG, 4326, always_xy=True)
    lon, lat = to_ll.transform([5_000_264.0, 5_000_264.0, 5_000_264.0], [3_700_050.0, 3_700_400.0, 3_700_005.0])
    c = pd.DataFrame({"road": ["KY0003", "KY0003", "US0023"], "county": "FLOYD", "parking_lot": False,
                      "lon": lon, "lat": lat})
    placed = crashes.place(c, pts, TABLE)
    near, far, other = placed.iloc[0], placed.iloc[1], placed.iloc[2]
    assert near["placed"] and near["route"] == "036-KY-0003-010" and near["grid"] == 10
    assert abs(near["snap_ft"] - 10) < 0.5
    assert not far["placed"]
    assert not other["placed"] and pd.isna(other["route"])     # US 23 isn't in this network


def test_pieces_halve_traffic_where_the_highway_is_divided():
    d = grid("036-KY-0003-000", 0.0).assign(mu_inv=0.1, mu_non=0.3, mu_stop_inv=0.2, mu_stop_non=0.4)
    d.loc[d["m"] >= 0.1, ["mu_inv", "mu_non"]] = [0.25, 0.05]
    aadt = pd.DataFrame({"route": ["036-KY-0003-000"], "BEGIN_MP": [0.0], "END_MP": [0.3], "LASTCNT": [4000]})
    p = crashes.pieces(d, TABLE, aadt, years=10).set_index("piece")
    assert not p.loc[0, "divided"] and p.loc[0, "aadt"] == 4000 and p.loc[0, "mu"] == 0.3   # both ways
    assert p.loc[1, "divided"] and p.loc[1, "aadt"] == 2000 and p.loc[1, "mu"] == 0.25     # this carriageway's way
    assert np.isclose(p.loc[0, "vmt_100m"], 4000 * 365.25 * 10 * 20 * 0.005 / 1e8)


def test_rates_are_crashes_per_hundred_million_vehicle_miles_by_tenth_of_demand():
    n = 100
    pcs = pd.DataFrame({"mu": np.linspace(0.01, 0.4, n), "miles": 0.1, "vmt_100m": 0.5,
                        "wet_departure": np.arange(n) % 3, "dry_departure": 1, "animal": 0})
    rows = crashes.by_decile(pcs)
    assert [r["decile"] for r in rows] == list(range(1, 11))
    first = rows[0]
    assert first["dry_departure"] == 10 and first["dry_departure_rate"] == 2.0          # 10 crashes over 5 x 100M
    assert first["wet_share"] == round(first["wet_departure"] / (first["wet_departure"] + 10), 3)
