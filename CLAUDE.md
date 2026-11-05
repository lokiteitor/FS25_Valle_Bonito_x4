# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A generator for a Farming Simulator 25 "x4" map ("Valle Bonito x4", 4096 m playable). It
produces two artefacts a human then imports into Giants Editor: a 16-bit heightmap PNG
(`dem_generator/dem_8k.png`, 8192 px: 2048 m of non-playable border on every side of the
playable square) and an OSM vector file (`osm_generator/map.osm`). There is no application and no
test suite. Verification is two acceptance scripts that exit non-zero, plus
`map_layout.validate()`, which both generators run first and refuse to proceed on.

The user works in Spanish; code and most docstrings are in English. Either language is fine in replies.

## Commands

    python3 map_layout.py                             # layout self-check, clean and sound
    python3 dem_generator/generate_dem.py             # ~20 s -> dem_8k.png, terrain_stats.json, 2 preview PNGs
    python3 dem_generator/measure_elevation.py        # acceptance report on the DEM (20/20 checks), exit 1 on failure
    python3 osm_generator/generate_osm.py             # -> map.osm
    python3 osm_generator/check_osm.py                # inventory + 11 invariants, exit 1 on failure
    python3 osm_generator/visualize_osm.py            # -> map_osm_visual.png (2D render)

Run the DEM before the OSM: `generate_osm.py` reads `dem_generator/terrain_stats.json`,
which the DEM publishes. Scripts work from the repo root or their own directory; each
puts what it needs on `sys.path`. System `python3` (3.14) has numpy, scipy, Pillow,
matplotlib and contourpy. There is no `.venv`.

## Architecture

**`map_layout.py` is the single source of geometry.** The DEM sculpts around it and the
OSM writes it out; neither half may define geometry of its own. A feature in one output
and not the other is invisible in either on its own. Standard library only: nothing in
`osm_generator/` may import numpy, and terrain facts the OSM side needs come through
`terrain_stats.json` via `map_layout.load_roughness()`, never re-derived.

| Feature / Registry | Coordinates / Extent | What it is |
|---|---|---|
| `CORRIDORS` | 23 roads, 42.9 km | 2 arterias primarias asfaltadas (y=400, x=3600), 11 calles secundarias del pueblo (incluyendo Calle Oeste), y 10 terracerías (`highway=track`, `surface=dirt`) entre los campos agrícolas |
| `WATER` | `wid='lake_west'` | Lago Valle Bonito, 39.7 ha cuenca a x=[0, 500], y=[1500, 3000] extendiéndose al borde oeste |
| `HILLS` | `hid='hill_spiral'` | Colina Espiral: 200 m cota en (cx=3000, cy=1000) con rampa espiral continua transitable de 4 giros (pendiente 7.3%) |
| `PADS` | 7 pads, 149 ha | Pueblo Valle Bonito (4x6 manzanas, 864x696 m), Zona Industrial Norte (350x120 m = 4.2 ha), Granja Principal Sureste (25 ha), y Granjas 1..4 (15 ha c/u) |
| `AREAS` | 138 areas | 78 parcelas agrícolas (1118 ha, mediana 15.3 ha, parcelación PLSS) y 60 bosques (205 ha, incluyendo Bosque Colina Espiral y cinturones perimetrales) |

## Coordinates and encoding

- Playable metres: **x east, y south from the north edge**, centre `(2048, 2048)`. Canvas
  is 8192 m with the 4096 m playable square centred, so canvas coordinates run
  `-2048 .. 6144` in the same frame (`OFFSET_M = 2048.0`).
- Projection is equirectangular about `LAT_CENTER = 43.145712, LON_CENTER = -95.145100` at 111111.0 m per degree.
- Heights are 16-bit centimetres: raw 2500 = 25.00 m. `BASE_ELEV_M` (25.0) is the datum.
  The Iowa glacial till plain undulates from 10.0 m (lake basin) to ~35-45 m on uplands,
  with Colina Espiral rising to 225.0 m.
- Everything is 1 m/px on the canvas (`8192 x 8192 px`). Synthesis works on a 4 m internal
  grid (`2048 x 2048 px`) and bicubic resamples to 1 m/px.
- A 25 m clean strip (`EDGE_CLEAR_M = 25.0`) inside the playable boundary: every ring the OSM
  draws is held inside it, and `check_osm.py` fails on anything planted in it.

## OSM tag vocabulary is closed

Emit only what `map_layout.RENDERED_TAGS` lists: `natural=water` (+ `water=*`),
`natural=wood`, `landuse=forest|farmyard|farmland`, `highway=*`, `railway=*`. Both
renderers (`visualize_osm.py`, `create_3d_viewer.py`) silently drop anything else, and
`check_osm.py` fails the build over a way whose only tags are outside the list.
Attribute tags on a drawable ring (`leaf_type`, `building`) are fine. Every wood ring
carries both `natural=wood` and `landuse=farmyard` (`wood_tags()`). Rings must close
on the same node id.

## Key Invariants

1. `EDGE_CLEAR_M`: No pad or area boundary may sit closer than 25 m to playable bounds `[0, 4096]`.
2. Closed areas: Every polygon area starts and ends on the exact same node ID.
3. Node sharing: Intersections between roads and boundaries cleanly share stitch nodes (213 shared junction nodes).
4. DEM limits: Height ranges 10.00 .. 225.00 m, no pixel touches 0 or 65535, spiral ramp grade <= 8.0%.
