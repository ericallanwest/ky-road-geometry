import numpy as np
import pandas as pd
import shapely

from ky_road_geometry import context


def test_points_take_the_tract_they_fall_in():
    tracts = pd.DataFrame({"geoid": ["21195930100", "21195930200"],
                           "geometry": [shapely.box(0, 0, 100, 100), shapely.box(100, 0, 200, 100)]})
    got = context.tract_of(np.array([50.0, 150.0, 250.0]), np.array([50.0, 50.0, 50.0]), tracts)
    assert list(got) == ["21195930100", "21195930200", None]


def test_ruca_codes_group_into_four():
    assert [context.RUCA_GROUP[c] for c in (1, 4, 7, 10)] == ["Metropolitan", "Micropolitan", "Small town", "Rural"]
    assert len(context.RUCA_GROUP) == 10
