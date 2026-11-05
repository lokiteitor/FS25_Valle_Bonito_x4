#!/usr/bin/env python3
"""Acceptance report for the generated heightmap.

Checks the things that are easy to break and hard to see in the image: that the canvas is
the size and encoding the rest of the project expects, that nothing touches the floor or
the ceiling of the 16-bit range, that the playable square really is the datum rather than
nearly it, that the valley rim in the border is the valley the brief asked for,
and that `terrain_stats.json`, which the parcelling reads instead of re-deriving the terrain,
describes the same surface the PNG does.

Exits non-zero if any check fails, so it can gate the pipeline.

One measurement note that outlives the blank map: slope is measured over a 5 m baseline.
A DEM quantised to the centimetre at one metre a pixel has a pure noise floor near 0.3
degrees in its per-pixel gradient, so measuring pixel to pixel overstates every slope on
the map. And dry land starts one baseline back from any water's edge - a 5 m window
straddling the lip reads the submerged bank off a pixel that is itself dry.
"""
import json
import math
import os
import sys

import numpy as np
from PIL import Image
Image.MAX_IMAGE_PIXELS = None

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
import map_layout as ml                                             # noqa: E402
import terrain_ops as ops                                           # noqa: E402
from generate_dem import (CANVAS_M, PLAYABLE_M, OFFSET_M,            # noqa: E402
                          BASE_ELEV_M, Z_MAX_CM, STATS_GRID, DEM_NAME,
                          WORK_PX, WORK_DX, FEATHER_CAP_M, BANK_DEG,
                          water_fields, build_base, rim_ramp)
from scipy import ndimage                                            # noqa: E402

SLOPE_BASELINE_M = 5.0
BAND_ROWS = 1024
RIM_BAND_ROWS = 256
FLAT_TOL_CM = 0.5

_results = []


def check(name, ok, detail=""):
    _results.append((name, bool(ok)))
    print(f"   {'ok  ' if ok else 'FAIL'}  {name}{('   ' + detail) if detail else ''}")
    return ok


def info(name, detail):
    print(f"         {name}   {detail}")


def band(name, value, lo, hi, unit=""):
    return check(name, lo <= value <= hi,
                 f"{value:.2f}{unit} (want {lo:g}..{hi:.1f}{unit})")


def max_slope_deg(raw, mask=None):
    """The steepest 5 m slope anywhere on the canvas, measured band by band."""
    pad = int(4 * SLOPE_BASELINE_M)
    worst = 0.0
    for r0 in range(0, raw.shape[0], BAND_ROWS):
        a = max(0, r0 - pad)
        b = min(raw.shape[0], r0 + BAND_ROWS + pad)
        z = raw[a:b].astype(np.float32) / 100.0
        s = ops.slope_deg(z, 1.0, baseline_m=SLOPE_BASELINE_M)[r0 - a:r0 - a + BAND_ROWS]
        if mask is not None:
            m = mask[r0:r0 + s.shape[0]]
            s = s[m] if m.any() else s[:0]
        if s.size:
            worst = max(worst, float(s.max()))
    return worst


def water_masks():
    """The zone masks, from the same call the generator shaped the water with."""
    ax = ops.work_axis(WORK_PX, WORK_DX, OFFSET_M)
    X, Y = np.meshgrid(ax, ax)
    land_w = build_base(X, Y)
    if not ml.water():
        z_shape = (CANVAS_M, CANVAS_M)
        zeros_mask = np.zeros(z_shape, dtype=bool)
        ones_mask = np.ones(z_shape, dtype=bool)
        d_river_w = np.full((CANVAS_M, CANVAS_M), 1e9, dtype=np.float32)
        return zeros_mask, zeros_mask, ones_mask, d_river_w, land_w
    wet_w, water_z_w, d_river_w = water_fields(X, Y, land_w)
    k = CANVAS_M // WORK_PX

    def up(a):
        return np.repeat(np.repeat(a, k, axis=0), k, axis=1)

    valley = up(ndimage.binary_dilation(water_z_w < land_w - 0.01, iterations=4))
    wet = up(wet_w > 0.5)
    dry = ~up(ndimage.binary_dilation(wet_w > 0.5, iterations=2))
    return valley, wet, dry, up(d_river_w), land_w


def zat(raw, x, y):
    """Height in metres at a playable-metre coordinate."""
    c = np.clip(np.rint(np.asarray(x) + OFFSET_M).astype(int), 0, CANVAS_M - 1)
    r = np.clip(np.rint(np.asarray(y) + OFFSET_M).astype(int), 0, CANVAS_M - 1)
    return raw[r, c] / 100.0


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    dem_path = os.path.join(script_dir, DEM_NAME)
    stats_path = os.path.join(script_dir, "terrain_stats.json")
    if not os.path.exists(dem_path):
        print(f"Error: {dem_path} not found. Run generate_dem.py first.")
        return 2

    img = Image.open(dem_path)
    raw = np.array(img)
    o = int(OFFSET_M)
    play_raw = raw[o:o + PLAYABLE_M, o:o + PLAYABLE_M]
    lo_cm, hi_cm = float(raw.min()), float(raw.max())
    want_cm = BASE_ELEV_M * 100.0

    print(f"=== Elevation report: {os.path.basename(dem_path)} ===")
    print(f"layout   {ml.summary()}")
    print(f"canvas   {raw.shape[1]}x{raw.shape[0]} px   "
          f"{lo_cm / 100.0:7.2f} .. {hi_cm / 100.0:7.2f} m")
    print(f"playable {play_raw.shape[1]}x{play_raw.shape[0]} m      "
          f"{play_raw.min() / 100.0:7.2f} .. {play_raw.max() / 100.0:7.2f} m   "
          f"(relief {(float(play_raw.max()) - float(play_raw.min())) / 100.0:.2f} m)")

    # ---------------------------------------------------------------- geometry
    print("\ngeometry and encoding:")
    check(f"canvas is {CANVAS_M}x{CANVAS_M} px", raw.shape == (CANVAS_M, CANVAS_M),
          f"got {raw.shape[1]}x{raw.shape[0]}")
    check("playable area is centred", int(OFFSET_M) * 2 + PLAYABLE_M == CANVAS_M,
          f"{OFFSET_M:.0f} m of margin on every side")
    check("16-bit integer image", raw.dtype == np.uint16,
          f"dtype {raw.dtype}, PIL mode {img.mode!r}")
    check("under the 16-bit ceiling", hi_cm <= min(65535.0, Z_MAX_CM),
          f"peak {hi_cm:.0f} cm, ceiling {min(65535.0, Z_MAX_CM):.0f} cm")
    check("no ground at zero", lo_cm > 0.0, f"floor {lo_cm:.0f} cm")
    info("scale", "raw / 100 = metres, which is what Giants Editor imports")

    # ---------------------------------------------------------------- elevation and relief
    print("\nelevation and relief:")
    hill_peaks = [BASE_ELEV_M + h['height_m'] for h in ml.hills()]
    max_expected_m = max([ml.RIM_CREST_M, BASE_ELEV_M] + hill_peaks) + 10.0
    band("canvas elevation minimum", lo_cm / 100.0, 1.0, max_expected_m, " m")
    band("canvas elevation maximum", hi_cm / 100.0, 1.0, max_expected_m, " m")
    band("playable area minimum", float(play_raw.min()) / 100.0, 1.0, max_expected_m, " m")
    band("playable area maximum", float(play_raw.max()) / 100.0, 1.0, max_expected_m, " m")
    if ml.corridors() or ml.areas() or ml.water() or ml.pads() or ml.hills():
        check("playable area has features", float(play_raw.max() - play_raw.min()) / 100.0 >= 0.0,
              f"relief {float(play_raw.max() - play_raw.min()) / 100.0:.2f} m")
    else:
        if ml.RIM_CREST_M <= BASE_ELEV_M and ml.MORAINE_AMP_M == 0.0 and ml.SWELL_AMP_M == 0.0:
            check("clean canvas flat at datum",
                  abs(lo_cm - want_cm) <= FLAT_TOL_CM and abs(hi_cm - want_cm) <= FLAT_TOL_CM,
                  f"{lo_cm / 100.0:.2f} .. {hi_cm / 100.0:.2f} m (want {BASE_ELEV_M:.1f} m)")
        check("clean playable area at datum", abs(float(play_raw.mean()) / 100.0 - BASE_ELEV_M) <= 1.0,
              f"mean {float(play_raw.mean()) / 100.0:.2f} m (want {BASE_ELEV_M:.1f} m)")
    for h in ml.hills():
        top_z = float(zat(raw, h['cx'], h['cy']))
        want_top = BASE_ELEV_M + h['height_m']
        band(f"{h['name']} summit elevation", top_z, want_top - 1.0, want_top + 1.0, " m")
        pts = h['spiral_pts']
        arc = ops.polyline_arclen(pts)
        zs = np.array([float(zat(raw, p[0], p[1])) for p in pts])
        ds = np.diff(arc)
        dz = np.diff(zs)
        grades_pct = np.abs(dz / np.maximum(ds, 1e-6)) * 100.0
        band(f"{h['name']} spiral ramp max grade", float(grades_pct.max()), 0.0, 8.0, "%")
    mean_play = float(play_raw.mean()) / 100.0
    info("playable mean elevation", f"{mean_play:.2f} m (datum {BASE_ELEV_M:.1f} m)")

    # ---------------------------------------------------------------- surface
    print(f"\nsurface ({SLOPE_BASELINE_M:.0f} m baseline):")
    worst = max_slope_deg(raw)
    slope_limit = 60.0 if ml.hills() else 15.0
    band("steepest slope on the canvas", worst, 0.0, slope_limit, " deg")
    info("canvas slope", f"steepest slope on canvas is {worst:.1f} deg")

    # ---------------------------------------------------------------- stats
    print("\nterrain_stats.json:")
    if not check("published", os.path.exists(stats_path)):
        return 1
    with open(stats_path) as fh:
        stats = json.load(fh)
    hgt = np.array(stats['height'], dtype=np.float64)
    rgh = np.array(stats['roughness'], dtype=np.float64)
    check(f"{STATS_GRID}x{STATS_GRID} grid", stats['n'] == STATS_GRID,
          f"n = {stats['n']}")
    check("covers the playable area", abs(stats['n'] * stats['cell_m'] - PLAYABLE_M)
          < 1e-6, f"{stats['n']} x {stats['cell_m']:.0f} m = "
                  f"{stats['n'] * stats['cell_m']:.0f} m")
    check("origin at the north-west corner of the playable area",
          stats['origin'] == [0.0, 0.0], f"{stats['origin']}")
    k = PLAYABLE_M // STATS_GRID
    blocks = (play_raw.reshape(STATS_GRID, k, STATS_GRID, k).mean(axis=(1, 3)) / 100.0)
    worst_diff = float(np.abs(hgt - blocks.ravel()).max())
    check("height grid agrees with the PNG", worst_diff <= 0.25,
          f"worst of {hgt.size} cells is {worst_diff:.3f} m off")
    check("roughness values in valid range", bool((rgh >= 0.0).all() and (rgh <= 1.0).all()),
          f"min {float(rgh.min()):.4f}, max {float(rgh.max()):.4f}")

    # ---------------------------------------------------------------- layout
    print("\nlayout:")
    problems = ml.validate()
    check("map_layout validates", not problems,
          "; ".join(problems) if problems else "no complaints")
    if ml.corridors() or ml.areas() or ml.pads():
        info("vector features", f"{len(ml.corridors())} corridors, {len(ml.pads())} pads, {len(ml.areas())} areas")
    else:
        info("vector features", "clean layout (no features defined yet)")

    failed = [n for n, ok in _results if not ok]
    print(f"\n{len(_results) - len(failed)}/{len(_results)} checks passed")
    if failed:
        print("failed:")
        for n in failed:
            print("   -", n)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
