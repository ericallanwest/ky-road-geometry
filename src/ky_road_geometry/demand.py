"""Friction demand: how much grip a car needs at each place, each way, at the speed it's likely driven.

Demand only. How much friction the pavement supplies, and what level of demand calls for a
closer look, are other questions; nothing here measures or judges supply.

**Speed.** The posted limit plus 5 mph, but no faster than a curve allows: a curve of radius R is
taken at about sqrt(g R (e + f)), with f AASHTO's side friction for that speed (Green Book
Table 3-7) and e the lane's measured superelevation. Speeds then ramp between those caps: a
backward pass limits each point to what braking at ``DECEL`` from the next allows, a forward pass to
what accelerating at ``ACCEL`` from the last allows.

**Demand**, as a fraction of the car's weight that the tires must deliver:

* along the road, ``mu_x = |a/g + G/100|``: the speed profile's acceleration a plus the grade G
  (braking downhill adds the grade; holding speed downhill still needs it);
* across it, ``mu_y = |V^2/(g R) - e/100|``: the cornering force superelevation doesn't supply;
* combined on the friction circle, ``mu = sqrt(mu_x^2 + mu_y^2)``;
* and where a crest (by day) or a sag (by night) limits sight, ``mu_stop``: the steady deceleration
  that stops the car within the sight distance left after 2.5 s of perception and reaction.

A grade steeper than ``MAX_GRADE_PCT`` isn't road here (a step in the lidar profile the bridge
search didn't catch, or the route line off the pavement), so demand reads it as that grade.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

G_FTS2 = 32.17
MPH = 5280 / 3600
ACCEL = 3.0           # ft/s^2, leaving a curve
DECEL = 5.0           # ft/s^2, slowing for one
REACTION_S = 2.5
MAX_GRADE_PCT = 15.0
# AASHTO Green Book Table 3-7: maximum side friction for design, by speed
F_SPEED = np.array([15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80], float)
F_MAX = np.array([.32, .27, .23, .20, .18, .16, .15, .14, .13, .12, .11, .10, .09, .08])


def side_friction(v_mph) -> np.ndarray:
    return np.interp(v_mph, F_SPEED, F_MAX)


def curve_speed(radius_ft: np.ndarray, e_pct: np.ndarray, v_limit_mph: np.ndarray) -> np.ndarray:
    """The speed a curve allows (mph), never above ``v_limit_mph``; two passes settle f(V)."""
    v = np.asarray(v_limit_mph, float).copy()
    e = np.nan_to_num(np.asarray(e_pct, float)) / 100
    for _ in range(3):
        cap = np.sqrt(np.maximum(G_FTS2 * radius_ft * (e + side_friction(v)), 0)) / MPH
        v = np.minimum(np.asarray(v_limit_mph, float), cap)
    return v


def ramp(s: np.ndarray, v_mph: np.ndarray) -> np.ndarray:
    """Speeds along one stretch in travel order, after braking and accelerating limits."""
    v = v_mph * MPH
    ds = np.abs(np.diff(s))
    for i in range(len(v) - 2, -1, -1):                       # brake in time for what comes next
        v[i] = min(v[i], np.sqrt(v[i + 1] ** 2 + 2 * DECEL * ds[i]))
    for i in range(1, len(v)):                                # and only gather speed so fast
        v[i] = min(v[i], np.sqrt(v[i - 1] ** 2 + 2 * ACCEL * ds[i - 1]))
    return v / MPH


def stop_demand(v_mph, sight_ft, grade_pct) -> np.ndarray:
    """Friction to stop within ``sight_ft`` after the reaction time (capped at 1.0)."""
    v = np.asarray(v_mph, float) * MPH
    room = np.asarray(sight_ft, float) - v * REACTION_S
    with np.errstate(divide="ignore", invalid="ignore"):
        mu = np.where(room > 0, v ** 2 / (2 * G_FTS2 * room) - np.asarray(grade_pct) / 100, 1.0)
    return np.clip(mu, 0.0, 1.0)


def demand(prof: pd.DataFrame) -> pd.DataFrame:
    """Per grid point and direction: speed and the friction it takes. ``prof`` needs route, part,
    grid, s, grade, kappa (rad/ft, with the measure), cs_inv/cs_non (cross slope, rising to the
    right of travel), speed_mph (posted), and sight_*/headlight_* (ft)."""
    from .cross import superelevation

    p = prof.sort_values(["route", "part", "grid"])
    out = []
    for d, sign in (("inv", 1.0), ("non", -1.0)):
        kappa = p["kappa"].to_numpy()
        radius = np.where(np.abs(kappa) > 1e-6, 1 / np.abs(np.where(kappa == 0, np.nan, kappa)), np.inf)
        e = superelevation(p[f"cs_{d}"].to_numpy(), kappa, sign)
        e = np.where(np.isfinite(e), e, -2.0)                 # no reading: assume a normal crown
        G = sign * np.clip(p["grade"].to_numpy(), -MAX_GRADE_PCT, MAX_GRADE_PCT)
        v_cap = curve_speed(radius, e, p["speed_mph"].to_numpy() + 5.0)
        v = np.empty(len(p))
        acc = np.zeros(len(p))
        for _, idx in p.groupby(["route", "part"], sort=False).indices.items():
            order = idx if sign > 0 else idx[::-1]
            s = p["s"].to_numpy()[order]
            vv = ramp(s, v_cap[order])
            v[order] = vv
            if len(order) > 1:                                # acceleration along travel, ft/s^2
                fps = vv * MPH
                a = np.diff(fps ** 2) / (2 * np.abs(np.diff(s)))
                acc[order] = np.r_[a, a[-1]]
        mu_x = np.abs(acc / G_FTS2 + G / 100)
        with np.errstate(divide="ignore", invalid="ignore"):
            mu_y = np.abs(np.where(np.isfinite(radius), (v * MPH) ** 2 / (G_FTS2 * radius), 0.0) - e / 100)
        sight = np.minimum(p[f"sight_{d}"].to_numpy(), p[f"headlight_{d}"].to_numpy())
        out.append(pd.DataFrame({
            f"speed_{d}": v, f"e_{d}": e, f"accel_{d}": acc, f"mu_x_{d}": mu_x, f"mu_y_{d}": mu_y,
            f"mu_{d}": np.hypot(mu_x, mu_y), f"mu_stop_{d}": stop_demand(v, sight, G)}, index=p.index))
    return pd.concat(out, axis=1).reindex(prof.index)
