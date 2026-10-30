# Where friction matters

**[Project page](https://ericallanwest.github.io/ky-road-geometry/)** ·
**[Crest and sag explorer](https://ericallanwest.github.io/ky-road-geometry/explorer/)**

Road geometry from public lidar along 2,060 miles of eastern Kentucky's state routes, checked
against the state's own inventories and turned into a map of how much grip the road asks of a car.

How much friction a road needs depends on its geometry: how sharp its curves are, how steep its
grades, how much its lanes bank, and how far ahead a driver can see. Kentucky publishes the
ingredients: the Kentucky Transportation Cabinet's road network with its curve, grade, speed-limit
and bridge inventories, and KyFromAbove's statewide lidar, a bare-earth elevation model at 2-ft
resolution. This project derives the geometry from those sources for KYTC's District 12 (Floyd,
Johnson, Knott, Lawrence, Letcher, Martin and Pike counties), checks it against the inventories, and
combines it into friction demand: the share of a car's weight its tires must deliver, braking and
cornering, at the speed the road is likely driven.

It measures demand only. What the pavement supplies, and what level of demand calls for a closer
look, are other questions.

## Pipeline

| Step | Command | What it does |
|---|---|---|
| Network | `kyrg network` | KYTC All Roads for the district's US and KY routes, measures anchored to milepoints, `-010` twin carriageways turned to run with the mainline |
| Inventories | `kyrg inventory` | KYTC's public curve, grade, speed-limit and bridge records for the district |
| Tile index | `kyrg tiles` | KyFromAbove's three DEM tile indexes, pinned as a dated snapshot of the tiles near the network |
| Lidar | `kyrg harvest` | Reads only the 512 x 512 blocks within 65 ft of a road, newest phase first, into local corridor GeoTIFFs |
| Profile | `kyrg profile` | Elevation every 0.005 mi along every route part |
| Grade | `kyrg grades` | Grade, bridge dips found and decked over, scored against the grade and bridge inventories |
| Sight | `kyrg sight` | Crest stopping sight and sag headlight distance, each way, computed on the profile |
| Curves | `kyrg curves` | Curvature from 50-ft chords, curves and their radius, scored against the curve inventory |
| Demand | `kyrg demand` | Lane cross slope and superelevation, speed profile, friction demand |
| Page | `kyrg site` | `docs/data/` for the project page |

## Results (District 12)

**Lidar.** 26,771 blocks of 1,855 5,000-ft tiles (1,619 from Phase 3, 2023-2026; 237 from Phase 2;
1 from Phase 1), 683 MB on disk; 99.8% of 418,532 grid points have an elevation.

**Curves**, against KYTC's 16,853 recorded curves:

| Check | Result |
|---|---|
| Recorded curves found | 96.5% (97-99% for classes B-F) |
| Curves found that sit on a recorded curve | 93.3% of 11,809 |
| Degree of curve, well-matched pairs (7,880) | median ratio 1.01, rank correlation 0.82 |
| Left or right | 92.4% agree |
| Class | 59.7% exact, 92.8% within one |

**Grades**, against KYTC's grade records: crests and sags read the right way 96.4% of the time (7,857
records). On the 3,080 tangents where the lidar profile is a clean straight line, uphill or downhill
agrees 87% of the time and the class is within one 91% of the time. The inventory reads steeper than
the lidar by a median 0.9 percentage points, uphill and downhill alike, while the lidar's straight
lines fit its profile within a median 0.17 ft.

**Bridges.** A bare-earth model has the bridges taken out, so dips where the profile falls into a
valley and climbs back are searched for: 92% of the 396 found sit on a recorded bridge, and they find
97% of recorded bridges 500 ft and longer (93% at 250-500 ft, 86% at 100-250 ft, 14% under 50 ft).

**Cross slope.** On tangents the right-hand lane falls a median 1.5% from the crown. Where measured,
superelevation rises with curvature, from 2.5% on gentle curves to 5.5% at 15-25 degrees, and eases
off on the sharpest switchbacks. 4.8% of readings are steeper than 12% and dropped as off the
pavement; demand assumes a normal crown there.

**Demand**, over 837,064 point-directions: median 0.11 of the car's weight, 0.20 at the 90th
percentile and 0.26 at the 99th. Curves hold 62% of them below the posted limit plus 5 mph.

## Run it

```sh
uv sync
uv run kyrg network
uv run kyrg inventory
uv run kyrg tiles
uv run kyrg harvest        # about 9 minutes; resumable
uv run kyrg profile
uv run kyrg grades
uv run kyrg sight
uv run kyrg curves
uv run kyrg demand         # about 2 minutes
uv run kyrg site
uv run pytest
```

`--counties Knott` (or any of the seven) limits `harvest`, `profile` and the analysis steps to one
county. Everything fetched or computed lands in `data/` (not tracked); `docs/data/` is tracked.

## Crest and sag explorer

`notebooks/explorer.py` is a [marimo](https://marimo.io) notebook: shape a vertical curve, pick a
speed, and it runs the pipeline's own sight-distance and stopping code on it, checked against
AASHTO's closed forms. The Pages workflow builds the package wheel and exports the notebook to
WebAssembly at `/explorer/`, so it runs in the browser. Locally: `uv run marimo edit notebooks/explorer.py`.

## Code

| Module | |
|---|---|
| `network.py` | Fetch and assemble the measured network |
| `inventory.py` | KYTC's curve, grade, speed-limit and bridge records |
| `tiles.py` | KyFromAbove tile indexes, snapshotted |
| `dem.py` | Corridor harvest and bilinear sampling |
| `grid.py` | The 0.005-mi grid with ground distance and direction |
| `vertical.py` | Grade, bridge dips, scoring against the grade and bridge records |
| `sight.py` | Crest and sag sight distance; AASHTO stopping sight distance |
| `horizontal.py` | Curvature, curves, Kasa circle fit, scoring against the curve records |
| `cross.py` | Lane cross slope and superelevation |
| `speed.py` | Posted speeds along the grid |
| `demand.py` | Speed profile and friction demand |
| `site.py` | The project page's data |

`scripts/kycrash_pull.py` pulls crash extracts from the Kentucky State Police's public search for
an optional crash check; the raw extracts stay local (KSP, all rights reserved).

## Data

- Road network and inventories: Kentucky Transportation Cabinet, public ArcGIS feature services on
  `https://services2.arcgis.com/CcI36Pduqd0OR4W9/ArcGIS/rest/services/`: `KYTC_All_Roads`,
  `Horizontal_Curves_Public` (layer 85), `Grade_Vertical_Curves_Public`, `Speed_Limits_Public` and
  `Kentucky_Bridge_Locations_view`, fetched 2026-10-30.
- Elevation: KyFromAbove bare-earth DEMs, `https://kyfromabove.s3.us-west-2.amazonaws.com/elevation/DEM`,
  tile indexes snapshotted 2026-10-30.

This project isn't affiliated with KYTC or KyFromAbove.

## License

MIT
