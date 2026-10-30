import marimo

__generated_with = "0.25.2"
app = marimo.App(width="medium", app_title="Crest and sag explorer")


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # Crests and sags: how far ahead can a driver see?

    Over a crest the road itself hides what's ahead. At night in a sag, the headlights light the
    road only as far as it climbs into their beam. Either way a driver has a limited distance in
    which to see something in the road, react and stop. Shape a vertical curve and pick a speed to
    see how much sight it leaves, and how hard a car would have to brake to stop within it.

    The sight distances and the friction to stop come from the same code that runs on the
    [project page](https://ericallanwest.github.io/ky-road-geometry/)'s 2,060 miles of lidar
    profiles, `ky_road_geometry.sight` and `ky_road_geometry.demand`, installed into this page and
    run on the same 26.4-ft grid.
    """)
    return


@app.cell
def _():
    import marimo as mo
    return (mo,)


@app.cell
async def _(mo):
    import sys

    if "pyodide" in sys.modules:   # in the browser: install this project's own wheel
        import micropip

        await micropip.install(
            str(mo.notebook_location() / "public" / "ky_road_geometry-0.1.0-py3-none-any.whl"), deps=False)

    import altair as alt
    import numpy as np
    import pandas as pd

    from ky_road_geometry.demand import G_FTS2, REACTION_S, stop_demand
    from ky_road_geometry.sight import (BEAM_DEG, EYE_FT, HEADLIGHT_FT, MAX_FT, OBJECT_FT, sight_distances,
                                        stopping_sight_required)
    from ky_road_geometry.vertical import point_grade
    return (BEAM_DEG, EYE_FT, G_FTS2, HEADLIGHT_FT, MAX_FT, OBJECT_FT, REACTION_S, alt, np, pd, point_grade,
            sight_distances, stop_demand, stopping_sight_required)


@app.cell
def _(mo):
    g1 = mo.ui.slider(-10, 10, step=0.5, value=4, show_value=True, label="Grade in (%)")
    g2 = mo.ui.slider(-10, 10, step=0.5, value=-4, show_value=True, label="Grade out (%)")
    length = mo.ui.slider(0, 1500, step=50, value=400, show_value=True, label="Curve length (ft)")
    speed = mo.ui.slider(20, 70, step=5, value=55, show_value=True, label="Speed (mph)")
    mo.vstack([
        mo.hstack([g1, length], justify="start", gap=3),
        mo.hstack([g2, speed], justify="start", gap=3),
        mo.md("Travel is left to right, and grades rise that way. A grade going in steeper than the grade "
              "coming out makes a crest; the other way round, a sag. The curve is a parabola, as roads are "
              "designed; a length of 0 is a sharp break in grade."),
    ])
    return g1, g2, length, speed


@app.cell
def _(BEAM_DEG, EYE_FT, HEADLIGHT_FT, OBJECT_FT, g1, g2, length, np, pd, point_grade, sight_distances,
      speed, stop_demand, stopping_sight_required):
    STEP_FT = 26.4      # the pipeline's grid: 0.005 mi
    VIEW_FT = 1500.0    # shown either side of the curve's middle; computed twice as far, so sight isn't cut short
    G1, G2, L, V = float(g1.value), float(g2.value), float(length.value), float(speed.value)
    A = G2 - G1         # the change of grade: negative over a crest, positive in a sag

    # elevation along a parabolic curve between straight grades, 0 at the curve's start
    s = np.arange(-2 * VIEW_FT, 2 * VIEW_FT + STEP_FT / 2, STEP_FT)
    x = s + L / 2
    on = np.clip(x, 0, L)
    z = (G1 * x + A * ((on ** 2 / (2 * L) if L > 0 else 0.0) + np.maximum(x - L, 0))) / 100

    prof = pd.DataFrame({"route": "curve", "part": 0, "grid": np.arange(len(s)), "s": s, "z": z})
    prof["grade"] = point_grade(prof)
    prof = prof.join(sight_distances(prof))
    prof["need"] = stopping_sight_required(V, prof["grade"])
    prof["mu_stop"] = stop_demand(V, np.minimum(prof["sight_inv"], prof["headlight_inv"]), prof["grade"])
    view = prof[prof["s"].abs() <= VIEW_FT + 1e-6].reset_index(drop=True)

    # AASHTO's closed forms for the shortest sight on the curve, to check the scan against
    a = abs(A)
    if a < 1e-9:
        kind, formula = "none", np.inf
    elif A < 0:     # crest: from the eye over the road to the top of the object
        kind = "crest"
        c = 200 * (np.sqrt(EYE_FT) + np.sqrt(OBJECT_FT)) ** 2                    # 2,158
        S = np.sqrt(c * L / a) if L > 0 else np.inf
        formula = S if S < L else (L + c / a) / 2
    else:           # sag, at night: where the headlight beam meets the road
        kind = "sag"
        h, t = 200 * HEADLIGHT_FT, 200 * np.tan(np.radians(BEAM_DEG))            # 400 and 3.5
        S = (t * L + np.sqrt((t * L) ** 2 + 4 * a * h * L)) / (2 * a) if L > 0 else np.inf
        formula = S if S < L else ((L + h / a) / (2 - t / a) if a > t / 2 else np.inf)

    # the place the sight is shortest, and its sight line or headlight beam
    col = "headlight_inv" if kind == "sag" else "sight_inv"
    w = int(view[col].idxmin())
    s0, z0, reach = view.at[w, "s"], view.at[w, "z"], view.at[w, col]
    if kind == "sag":
        rise = np.tan(np.arctan(view.at[w, "grade"] / 100) + np.radians(BEAM_DEG))
        ray = pd.DataFrame({"s": [s0, s0 + reach], "z": [z0 + HEADLIGHT_FT, z0 + HEADLIGHT_FT + rise * reach]})
    else:
        z_end = float(np.interp(s0 + reach, prof["s"], prof["z"]))
        ray = pd.DataFrame({"s": [s0, s0 + reach], "z": [z0 + EYE_FT, z_end + OBJECT_FT]})

    ssd = float(stopping_sight_required(V, 0.0))
    k_have = L / a if a > 1e-9 else np.inf
    k_need = ssd ** 2 / (200 * (np.sqrt(EYE_FT) + np.sqrt(OBJECT_FT)) ** 2) if kind != "sag" else \
        ssd ** 2 / (200 * HEADLIGHT_FT + 200 * np.tan(np.radians(BEAM_DEG)) * ssd)
    shortest = float(view[col].min())
    mu_max = float(view["mu_stop"].max())
    return A, L, V, VIEW_FT, formula, k_have, k_need, kind, mu_max, ray, shortest, ssd, view


@app.cell
def _(A, MAX_FT, V, formula, k_have, k_need, kind, mo, mu_max, shortest, ssd):
    def _ft(v):
        return f"{MAX_FT:,.0f} ft or more" if v >= MAX_FT - 1 else f"{v:,.0f} ft"

    _what = {"crest": "Over the crest", "sag": "By headlight", "none": ""}[kind]
    mo.hstack([
        mo.stat(_ft(shortest), label="Shortest sight on the curve",
                caption=f"{_what}; AASHTO: {_ft(formula)}" if kind != "none" else "A straight grade hides nothing"),
        mo.stat(f"{ssd:,.0f} ft", label=f"Needed to stop from {V:.0f} mph",
                caption="Level road, 2.5 s to react"),
        mo.stat("–" if A == 0 else f"{k_have:,.0f}", label="K, ft per 1% change of grade",
                caption=f"AASHTO's minimum: {k_need:,.0f}"),
        mo.stat("1.0 or more" if mu_max >= 0.999 else f"{mu_max:.2f}", label="Most friction to stop in sight",
                caption="AASHTO brakes at 0.35"),
    ], widths="equal", gap=1, wrap=True)
    return


@app.cell
def _(VIEW_FT, alt, kind, mo, ray, view):
    INK2, BLUE, ORANGE, RULE = "#52514e", "#2a78d6", "#eb6834", "#c3c2b7"
    x_scale = alt.Scale(domain=[-VIEW_FT, VIEW_FT], nice=False)
    x_axis = alt.Axis(values=list(range(-int(VIEW_FT), int(VIEW_FT) + 1, 500)), format=",.0f")
    road = alt.Chart(view).mark_line(strokeWidth=2, color=INK2).encode(
        x=alt.X("s:Q", title="feet from the middle of the curve", scale=x_scale, axis=x_axis),
        y=alt.Y("z:Q", title="feet above the curve's start", scale=alt.Scale(zero=False)),
        tooltip=[alt.Tooltip("s:Q", title="ft", format=",.0f"), alt.Tooltip("z:Q", title="elevation", format=".1f"),
                 alt.Tooltip("grade:Q", title="grade %", format=".1f")],
    )
    layers = [road]
    if kind != "none":
        layers.append(alt.Chart(ray).mark_line(strokeWidth=2, strokeDash=[5, 4],
                                               color=ORANGE if kind == "sag" else BLUE).encode(x="s:Q", y="z:Q"))
    _note = {"crest": "Dashed: the sight line where it's shortest, from a 3.5-ft eye to the top of a 2-ft object.",
             "sag": "Dashed: the headlight beam where it reaches the least road, 2 ft up and aimed 1° above the car's grade.",
             "none": ""}[kind]
    mo.vstack([
        mo.md(f"**The road's profile.** The vertical scale is stretched. {_note}"),
        alt.layer(*layers).properties(width="container", height=220),
    ])
    return BLUE, INK2, ORANGE, RULE, x_axis, x_scale


@app.cell
def _(BLUE, INK2, MAX_FT, ORANGE, alt, mo, view, x_axis, x_scale):
    _names = {"sight_inv": "By day, over the road", "headlight_inv": "At night, by headlight",
              "need": "Needed to stop"}
    long = view.melt(id_vars="s", value_vars=list(_names), var_name="k", value_name="ft")
    long["what"] = long["k"].map(_names)
    _colors = alt.Scale(domain=list(_names.values()), range=[BLUE, ORANGE, INK2])
    lines = alt.Chart(long).mark_line(strokeWidth=2).encode(
        x=alt.X("s:Q", title="feet from the middle of the curve", scale=x_scale, axis=x_axis),
        y=alt.Y("ft:Q", title="feet ahead", scale=alt.Scale(domain=[0, MAX_FT])),
        color=alt.Color("what:N", scale=_colors, title=None, legend=alt.Legend(orient="top")),
        strokeDash=alt.condition(alt.datum.what == "Needed to stop", alt.value([5, 4]), alt.value([1, 0])),
        tooltip=["what", alt.Tooltip("s:Q", title="ft", format=",.0f"), alt.Tooltip("ft:Q", title="feet ahead", format=",.0f")],
    )
    mo.vstack([
        mo.md(f"**How far ahead the driver sees, at each place.** Capped at {MAX_FT:,.0f} ft. Where a line "
              "drops below the dashed one, a car at this speed can't stop within the sight, braking as "
              "AASHTO assumes."),
        lines.properties(width="container", height=240),
    ])
    return


@app.cell
def _(INK2, RULE, alt, mo, pd, view, x_axis, x_scale):
    mu = alt.Chart(view).mark_line(strokeWidth=2, color=INK2).encode(
        x=alt.X("s:Q", title="feet from the middle of the curve", scale=x_scale, axis=x_axis),
        y=alt.Y("mu_stop:Q", title="share of the car's weight", scale=alt.Scale(domain=[0, 1])),
        tooltip=[alt.Tooltip("s:Q", title="ft", format=",.0f"), alt.Tooltip("mu_stop:Q", title="friction", format=".2f")],
    )
    _ref = pd.DataFrame({"y": [11.2 / 32.2], "label": ["AASHTO's braking, 11.2 ft/s²"]})
    rule = alt.Chart(_ref).mark_rule(strokeDash=[3, 3], color=RULE).encode(y="y:Q")
    tag = alt.Chart(_ref).mark_text(align="left", dx=4, dy=-6, color=INK2).encode(y="y:Q", x=alt.value(0), text="label:N")
    mo.vstack([
        mo.md("**Friction to stop within the sight left,** after 2.5 s to react: the steady braking that "
              "stops the car in the distance remaining, less what the grade does. 1.0 means it can't be "
              "done; that's where the line flattens at the top."),
        (rule + tag + mu).properties(width="container", height=200),
    ])
    return


@app.cell(hide_code=True)
def _(mo):
    mo.accordion({"How it's computed": mo.md(r"""
    **Sight** (`ky_road_geometry.sight`). From each point of the profile, the scan walks ahead one
    grid step at a time, keeping the steepest line from the driver's eye (3.5 ft up) to the road so
    far. An object 2 ft tall stays in view while its top clears that line; the sight distance is how
    far that lasts. At night the headlights, 2 ft up and aimed 1° above the car's own grade, light
    the road only where it climbs into the beam. Both stop at 1,500 ft. Nothing is fitted: the scan
    runs on the elevations, the way it runs on the lidar.

    **Needed to stop** (`stopping_sight_required`). AASHTO's stopping sight distance:
    1.47 V t + V² / (30 (a / 32.2 + G / 100)), with t = 2.5 s to see and react and a = 11.2 ft/s².

    **Friction to stop** (`ky_road_geometry.demand.stop_demand`). The steady deceleration that stops
    the car within the sight left after 2.5 s, as a share of its weight: v² / (2 g d) − G / 100,
    capped at 1.0.

    **AASHTO's formulas**, the check on the scan. A crest of length L and change of grade A (in %)
    leaves a shortest sight S with L = A S² / 2,158 when S < L, and L = 2 S − 2,158 / A when S > L.
    A sag lit by headlights: L = A S² / (400 + 3.5 S), or L = 2 S − (400 + 3.5 S) / A. K = L / A is
    how gradual the curve is; AASHTO's minimum K at a speed is the K that leaves its stopping sight
    distance.

    What isn't here: sight around horizontal curves (a cut slope on the inside of a bend), and the
    speed a driver would choose; the car holds the speed set above.
    """)})
    return


if __name__ == "__main__":
    app.run()
