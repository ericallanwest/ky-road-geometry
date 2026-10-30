"""The analysis grid: a point every 0.005 mi of measure on every route part.

Integer keys (``grid`` = measure / 0.005) so nothing drifts through float arithmetic; half-open,
so a part's last measure has no point. Each point also carries its ground distance along the
part, ``s`` (feet): KYTC's measures run a few percent off the drawn length, and grade and
curvature need real distance. The direction of the road at each point, ``tx, ty``, is the
central difference of its neighbors, which smooths over vertex kinks at the 52.8-ft scale.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

STEP_MI = 0.005


def points(parts: dict[str, list[np.ndarray]], routes=None) -> pd.DataFrame:
    frames = []
    for route in routes if routes is not None else parts:
        for k_part, xym in enumerate(parts[route]):
            m = xym[:, 2]
            cum = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(xym[:, :2], axis=0).T))])
            k = np.arange(int(np.ceil(m[0] / STEP_MI - 1e-9)), int(np.ceil(m[-1] / STEP_MI - 1e-9)))
            if len(k) < 3:
                continue
            mk = k * STEP_MI
            s = np.interp(mk, m, cum)
            x, y = np.interp(s, cum, xym[:, 0]), np.interp(s, cum, xym[:, 1])
            dx = np.gradient(x, s) if len(s) > 1 else np.zeros_like(x)
            dy = np.gradient(y, s) if len(s) > 1 else np.zeros_like(y)
            norm = np.hypot(dx, dy)
            frames.append(pd.DataFrame({"route": route, "part": k_part, "grid": k, "m": mk, "s": s,
                                        "x": x, "y": y, "tx": dx / norm, "ty": dy / norm}))
    return pd.concat(frames, ignore_index=True)
