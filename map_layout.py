#!/usr/bin/env python3
"""The layout of the map: where everything is, in playable metres.

This is the single source of truth shared by both halves of the pipeline:
- The DEM generator sculpts terrain around the geometry defined here.
- The OSM generator writes out the same geometry as vector data for Giants Editor.

Neither half may define geometry of its own.

Coordinates are playable metres:
- x east, y south from the north edge, so the centre of the playable area is (2048, 2048).
- Playable area: 4096 x 4096 m.
- Canvas area: 8192 x 8192 m (2048 m margin on all sides).
- Standard library only: nothing here imports numpy or external dependencies.
"""
import json
import math
import os
import random

# ==================================================================================
# 1. Map Coordinates & Canvas Dimensions
# ==================================================================================
# The projection anchor (geographic centre of the map: Tamuín, San Luis Potosí, México)
LAT_CENTER = 22.00556000
LON_CENTER = -98.78389000

# Map dimensions (in metres)
PLAYABLE_M = 4096.0
HALF_M = PLAYABLE_M / 2.0
CANVAS_M = 8192.0
OFFSET_M = (CANVAS_M - PLAYABLE_M) / 2.0      # 2048 m margin on each side

# Extension for alignments leaving the canvas
EXTEND_M = 300.0
EDGE_MIN = -OFFSET_M - EXTEND_M               # -2348 m
EDGE_MAX = PLAYABLE_M + OFFSET_M + EXTEND_M   # 6444 m

# A clean strip inside the playable boundary: no fields/buildings in this margin
EDGE_CLEAR_M = 25.0

# Base elevation datum (16-bit DEM units: raw / 100 = metres)
BASE_ELEV_M = 25.0

# ==================================================================================
# 2. Iowa Geomorphology & Non-Playable Border (Des Moines Lobe / NW Iowa Plains)
# ==================================================================================
# Playable area relief (Clay County, Iowa glacial till plain & loess swells)
IOWA_TILT_M = 11.0            # regional WSW -> ENE uplift across playable area (m)
IOWA_MORAINE_AMP_M = 6.5      # NW-SE anisotropic glacial moraine undulations (m)
IOWA_MORAINE_LAM_M = 1200.0   # primary moraine wavelength (scaled to x4)
IOWA_LOESS_AMP_M = 2.8        # intermediate loess swells amplitude (m)
IOWA_LOESS_LAM_M = 480.0      # intermediate loess wavelength (m)
IOWA_SWALE_AMP_M = 0.9        # gentle prairie swale / pothole relief (m)
IOWA_SWALE_LAM_M = 190.0      # swale wavelength (m)

# Non-playable perimeter moraine horizon
RIM_APRON_M = 100.0           # flat transition apron beyond boundary
RIM_BACK_M = 150.0            # shoulder inside canvas edge
RIM_CREST_M = 54.0            # moraine ridge crest on horizon (m)
RIM_SADDLE_M = 36.0           # low moraine saddle (m)
RIM_MOUTH_M = 28.0            # valley mouth sill elevation (m)
RIM_MOUTH_VAR_M = 2.5         # natural sill variance (m)
RIM_RIDGE_LOBES = 9           # perimeter lobes
RIM_RIDGE_BEAT = 15           # secondary harmonic
RIM_SPUR_LAM_M = 900.0        # spur wavelength
RIM_SPUR_AMP = 0.25           # spur modulation amplitude
RIM_ROUGH_LAM_M = 400.0       # secondary wavelength
RIM_ROUGH_AMP = 0.15          # secondary roughness amplitude
RIM_WEST_SMOOTH = 0.25        # keep western lake exit low and open

# Glacial moraine / border relief characteristics (Des Moines Lobe NW-SE grain)
MORAINE_GRAIN_DEG = -35.0
MORAINE_STRETCH = 2.6
MORAINE_AMP_M = 6.0
MORAINE_LAM_M = 1400.0
SWELL_AMP_M = 2.5
SWELL_LAM_M = 500.0

RIM_HEIGHT_M = RIM_CREST_M - BASE_ELEV_M
RIM_RAMP_M = OFFSET_M - RIM_APRON_M - RIM_BACK_M
RIM_PERIM_M = 4.0 * PLAYABLE_M
RIM_RIDGE_LAM_M = RIM_PERIM_M / RIM_RIDGE_LOBES
RIM_MAX_FLANK_DEG = 6.0

# Notch dimensions for rivers cutting through rim (if any)
RIVER_NOTCH_HALF_M = 120.0
RIVER_NOTCH_FEATHER_M = 200.0

# ==================================================================================
# 3. Projection & Units
# ==================================================================================
M_PER_DEG = 111111.0
M_PER_DEG_LON = M_PER_DEG * math.cos(math.radians(LAT_CENTER))
SEED = 20250902

# Platform and corridor standards
TOWN_PAD_FEATHER_M = 25.0
TOWN_DRAIN_GRADE = 0.003
YARD_FEATHER_M = 20.0
YARD_DRAIN_GRADE = 0.003
BANK_DEG = 4.0

HIGHWAY_CLASS = {
    'primary': 'primary',
    'secondary': 'secondary',
    'tertiary': 'tertiary',
    'section': 'secondary',
    'track': 'track',
    'street': 'tertiary'
}

# Closed vocabulary of OSM tags recognized by FS25 pipelines and renderers
RENDERED_TAGS = (
    ('natural', 'water'),
    ('water', None),
    ('natural', 'wood'),
    ('landuse', 'forest'),
    ('landuse', 'farmyard'),
    ('landuse', 'farmland'),
    ('highway', None),
    ('railway', None),
)


# ==================================================================================
# 4. Pure Python Spatial Primitives
# ==================================================================================
def local_to_global(x, y):
    """Playable metres -> (lat, lon). y grows southwards, so it subtracts."""
    return (LAT_CENTER - (y - HALF_M) / M_PER_DEG,
            LON_CENTER + (x - HALF_M) / M_PER_DEG_LON)


def global_to_local(lat, lon):
    """(lat, lon) -> playable metres. The inverse of local_to_global."""
    return (HALF_M + (lon - LON_CENTER) * M_PER_DEG_LON,
            HALF_M - (lat - LAT_CENTER) * M_PER_DEG)


def bounds():
    """The four values of the OSM <bounds> element: (minlat, minlon, maxlat, maxlon)."""
    minlat, minlon = local_to_global(0.0, PLAYABLE_M)
    maxlat, maxlon = local_to_global(PLAYABLE_M, 0.0)
    return minlat, minlon, maxlat, maxlon


def to_canvas(x, y):
    """Playable metres -> canvas metres (origin at NW canvas corner)."""
    return x + OFFSET_M, y + OFFSET_M


def from_canvas(xc, yc):
    """Canvas metres -> playable metres."""
    return xc - OFFSET_M, yc - OFFSET_M


def polyline_length(pts):
    """Total length of a polyline in metres."""
    return sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def ring_area_ha(ring):
    """Shoelace formula for polygon area in hectares."""
    pts = ring[:-1] if len(ring) > 2 and math.dist(ring[0], ring[-1]) < 1e-9 else ring
    if len(pts) < 3:
        return 0.0
    twice = sum(pts[i][0] * pts[(i + 1) % len(pts)][1] -
                pts[(i + 1) % len(pts)][0] * pts[i][1] for i in range(len(pts)))
    return abs(twice) / 2.0 / 10000.0


def ring_perimeter(ring):
    pts = ring if math.dist(ring[0], ring[-1]) < 1e-9 else list(ring) + [ring[0]]
    return polyline_length(pts)


def close_ring(ring):
    """Ensure ring explicitly closes on its first node."""
    if not ring:
        return []
    if math.dist(ring[0], ring[-1]) < 1e-9:
        return list(ring)
    return list(ring) + [ring[0]]


def rect_ring(x0, y0, x1, y1):
    """Axis-aligned rectangle as a closed ring."""
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]


def clip_ring_to_rect(ring, x0, y0, x1, y1):
    """Sutherland-Hodgman polygon clipping against an axis-aligned bounding box."""
    poly = ring[:-1] if len(ring) > 1 and math.dist(ring[0], ring[-1]) < 1e-9 else list(ring)
    for keep, cut in ((lambda p: p[0] >= x0, lambda a, b: _cut_x(a, b, x0)),
                      (lambda p: p[0] <= x1, lambda a, b: _cut_x(a, b, x1)),
                      (lambda p: p[1] >= y0, lambda a, b: _cut_y(a, b, y0)),
                      (lambda p: p[1] <= y1, lambda a, b: _cut_y(a, b, y1))):
        out = []
        for i, b_pt in enumerate(poly):
            a_pt = poly[i - 1]
            if keep(b_pt):
                if not keep(a_pt):
                    out.append(cut(a_pt, b_pt))
                out.append(b_pt)
            elif keep(a_pt):
                out.append(cut(a_pt, b_pt))
        poly = out
        if not poly:
            return []
    return poly + [poly[0]]


def _cut_x(a, b, x):
    denom = b[0] - a[0]
    t = (x - a[0]) / denom if abs(denom) > 1e-12 else 0.0
    return (x, a[1] + t * (b[1] - a[1]))


def _cut_y(a, b, y):
    denom = b[1] - a[1]
    t = (y - a[1]) / denom if abs(denom) > 1e-12 else 0.0
    return (a[0] + t * (b[0] - a[0]), y)


def ellipse_ring(cx, cy, a, b, rot_deg=0.0, n=48, harmonics=()):
    """Sample an ellipse or harmonically perturbed ellipse."""
    rot = math.radians(rot_deg)
    c, s = math.cos(rot), math.sin(rot)
    out = []
    for i in range(n):
        th = 2.0 * math.pi * i / n
        m = 1.0
        for amp, k, ph in harmonics:
            m += amp * math.sin(k * th + ph)
        u, v = a * m * math.cos(th), b * m * math.sin(th)
        out.append((cx + u * c - v * s, cy + u * s + v * c))
    out.append(out[0])
    return out


def point_in_ring(pt, ring):
    """Ray casting point-in-polygon test."""
    x, y = pt
    pts = ring[:-1] if math.dist(ring[0], ring[-1]) < 1e-9 else ring
    inside = False
    n = len(pts)
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        if (y0 > y) != (y1 > y):
            xx = x0 + (y - y0) * (x1 - x0) / ((y1 - y0) or 1e-12)
            if x < xx:
                inside = not inside
    return inside


def segs_cross(a, b, c, d):
    """True if segment ab properly crosses segment cd."""
    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    d1, d2 = orient(c, d, a), orient(c, d, b)
    d3, d4 = orient(a, b, c), orient(a, b, d)
    return ((d1 > 1e-9 and d2 < -1e-9) or (d1 < -1e-9 and d2 > 1e-9)) and \
           ((d3 > 1e-9 and d4 < -1e-9) or (d3 < -1e-9 and d4 > 1e-9))


def rings_overlap(p, q):
    """True if polygons p and q overlap in area."""
    if any(point_in_ring(v, q) for v in p[:-1]) or any(point_in_ring(v, p) for v in q[:-1]):
        return True
    return any(segs_cross(p[i], p[i + 1], q[j], q[j + 1])
               for i in range(len(p) - 1) for j in range(len(q) - 1))


def ring_is_simple(ring):
    """True if a closed ring has no self-intersections."""
    pts = ring[:-1] if math.dist(ring[0], ring[-1]) < 1e-9 else list(ring)
    n = len(pts)
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue
            c, d = pts[j], pts[(j + 1) % n]
            if max(a[0], b[0]) < min(c[0], d[0]) or max(c[0], d[0]) < min(a[0], b[0]):
                continue
            if max(a[1], b[1]) < min(c[1], d[1]) or max(c[1], d[1]) < min(a[1], b[1]):
                continue
            if segs_cross(a, b, c, d):
                return False
    return True


def seg_point_dist(p, a, b):
    px, py = p
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    ll = dx * dx + dy * dy
    if ll < 1e-12:
        return math.dist(p, a)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / ll))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def dist_to_polyline(p, pts):
    return min(seg_point_dist(p, pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def densify(pts, step):
    """Resample polyline keeping vertices spaced roughly by step."""
    out = [pts[0]]
    carry = 0.0
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        seg = math.dist(a, b)
        if seg < 1e-9:
            continue
        d = step - carry
        while d < seg:
            t = d / seg
            out.append((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])))
            d += step
        carry = seg - (d - step)
    out.append(pts[-1])
    return out


def offset_polyline(pts, dist):
    """Offset polyline sideways by dist (positive = left of travel)."""
    out = []
    n = len(pts)
    for i in range(n):
        a = pts[max(0, i - 1)]
        b = pts[min(n - 1, i + 1)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        ll = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / ll, dx / ll
        out.append((pts[i][0] + dist * nx, pts[i][1] + dist * ny))
    return out


def buffer_ring(pts, half_w):
    left = offset_polyline(pts, half_w)
    right = offset_polyline(pts, -half_w)
    return close_ring(left + right[::-1])


def clip_polyline(pts, x0, y0, x1, y1):
    runs, cur = [], []
    for p in pts:
        if x0 <= p[0] <= x1 and y0 <= p[1] <= y1:
            cur.append(p)
        elif cur:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    return [r for r in runs if len(r) >= 2]


def playable_sdf(x, y):
    """Signed distance to playable boundary: negative inside, positive outside."""
    dx = max(-x, x - PLAYABLE_M)
    dy = max(-y, y - PLAYABLE_M)
    if dx <= 0.0 and dy <= 0.0:
        return max(dx, dy)
    return math.hypot(max(dx, 0.0), max(dy, 0.0))


def wood_tags(leaf_type='mixed'):
    return {'natural': 'wood', 'landuse': 'farmyard', 'leaf_type': leaf_type}


# ==================================================================================
# 5. Core Feature Registries
# ==================================================================================
CORRIDORS = []
WATER = []
PADS = []
AREAS = []
FIELDS = []
SHELTERBELTS = []
HILLS = []


# ==================================================================================
# 6. Builder Helper Functions
# ==================================================================================
def add_corridor(cid, name, kind, axis, half_width_m=3.0, feather_m=6.0,
                 grade_max=0.08, bridge_spans=None, tags=None):
    record = {
        'id': cid,
        'name': name,
        'kind': kind,
        'axis': [(float(x), float(y)) for x, y in axis],
        'half_width_m': float(half_width_m),
        'feather_m': float(feather_m),
        'grade_max': float(grade_max),
        'bridge_spans': list(bridge_spans or []),
        'tags': dict(tags or {'highway': HIGHWAY_CLASS.get(kind, 'tertiary')})
    }
    CORRIDORS.append(record)
    return record


def add_water(wid, name, kind='lake', ring=None, axis=None, water_half_w=20.0,
              depth_m=15.0, shape=None, tags=None):
    t = dict(tags or {'natural': 'water', 'water': kind})
    record = {
        'id': wid,
        'name': name,
        'kind': kind,
        'depth_m': float(depth_m),
        'tags': t
    }
    if shape:
        record['shape'] = dict(shape)
        if ring is None:
            raw_ring = ellipse_ring(
                shape['cx'], shape['cy'], shape['a'], shape['b'],
                rot_deg=shape.get('rot_deg', 0.0),
                n=shape.get('n', 240),
                harmonics=shape.get('harmonics', ())
            )
            ring = clip_ring_to_rect(raw_ring, 0.0, 0.0, PLAYABLE_M, PLAYABLE_M)
    if ring:
        record['ring'] = close_ring([(float(x), float(y)) for x, y in ring])
    if axis:
        record['axis'] = [(float(x), float(y)) for x, y in axis]
        record['water_half_w'] = float(water_half_w)
    WATER.append(record)
    return record


def add_pad(pid, name, kind, centre, size, ring=None, feather_m=20.0,
            drain_grade=0.003, level=True, tags=None):
    cx, cy = float(centre[0]), float(centre[1])
    w, h = float(size[0]), float(size[1])
    r = ring if ring else rect_ring(cx - w / 2.0, cy - h / 2.0, cx + w / 2.0, cy + h / 2.0)
    record = {
        'id': pid,
        'name': name,
        'kind': kind,
        'centre': (cx, cy),
        'size': (w, h),
        'ring': close_ring([(float(x), float(y)) for x, y in r]),
        'feather_m': float(feather_m),
        'drain_grade': float(drain_grade),
        'level': bool(level),
        'tags': dict({'landuse': 'farmyard'} if tags is None else tags)
    }
    PADS.append(record)
    return record


def add_area(aid, name, kind, ring, tags=None, is_border=False):
    record = {
        'id': aid,
        'name': name,
        'kind': kind,
        'ring': close_ring([(float(x), float(y)) for x, y in ring]),
        'tags': dict(tags or {}),
        'is_border': bool(is_border),
    }
    AREAS.append(record)
    return record


def add_field(fid, name, ring, tags=None):
    t = dict(tags or {'landuse': 'farmland'})
    record = add_area(fid, name, 'farmland', ring, tags=t)
    FIELDS.append(record)
    return record


def add_wood(wid, name, ring, leaf_type='mixed', tags=None, is_border=False):
    t = dict(tags or wood_tags(leaf_type))
    return add_area(wid, name, 'wood', ring, tags=t, is_border=is_border)


def add_hill(hid, name, cx, cy, a, b, height_m=200.0, rot_deg=0.0,
             harmonics=(), spiral_turns=2.0, theta_start=0.5 * math.pi,
             q_start=0.95, q_top=0.08, shelf_frac=0.08):
    rot = math.radians(rot_deg)
    cos_r, sin_r = math.cos(rot), math.sin(rot)
    n_pts = 500
    spiral_pts = []
    spiral_u = []
    for i in range(n_pts):
        u = i / (n_pts - 1)
        nu = spiral_turns * u
        th = theta_start + 2.0 * math.pi * (nu - 0.5)
        q_s = q_start * (1.0 - u) + q_top * u
        m = 1.0
        for amp, k, ph in harmonics:
            m += amp * math.sin(k * th + ph)
        u_loc = a * q_s * m * math.cos(th)
        v_loc = b * q_s * m * math.sin(th)
        x = cx + u_loc * cos_r - v_loc * sin_r
        y = cy + u_loc * sin_r + v_loc * cos_r
        spiral_pts.append((float(x), float(y)))
        spiral_u.append(float(u))

    record = {
        'id': hid,
        'name': name,
        'cx': float(cx),
        'cy': float(cy),
        'a': float(a),
        'b': float(b),
        'height_m': float(height_m),
        'rot_deg': float(rot_deg),
        'harmonics': tuple(harmonics),
        'spiral_turns': float(spiral_turns),
        'theta_start': float(theta_start),
        'q_start': float(q_start),
        'q_top': float(q_top),
        'shelf_frac': float(shelf_frac),
        'spiral_pts': spiral_pts,
        'spiral_u': spiral_u,
    }
    HILLS.append(record)
    return record


# ==================================================================================
# 6b. Map Features in x4 Playable Area (4096 x 4096 m)
# ==================================================================================
# Natural lake extending from playable area (x=[0, 500], y=[1500, 3000]) westward into border
LAKE_WEST_SHAPE = {
    'cx': -90.0,
    'cy': 2250.0,
    'a': 480.0,
    'b': 670.0,
    'rot_deg': -3.0,
    'n': 240,
    'harmonics': (
        (0.11, 2, 0.45),
        (0.09, 3, -0.85),
        (0.06, 4, 1.65),
        (0.05, 5, -2.10),
        (0.035, 7, 0.80),
    ),
}

add_water(
    wid='lake_west',
    name='Lago Valle Bonito',
    kind='lake',
    depth_m=15.0,
    shape=LAKE_WEST_SHAPE,
)

# Natural 200 m spiral hill in x=[2500, 3500], y=[500, 1500]
HILL_SPIRAL_HARMONICS = (
    (0.03, 3, 0.50),
    (0.02, 5, -1.20),
    (0.015, 7, 2.00),
)

add_hill(
    hid='hill_spiral',
    name='Colina Espiral',
    cx=3000.0,
    cy=1000.0,
    a=465.0,
    b=465.0,
    height_m=200.0,
    rot_deg=15.0,
    harmonics=HILL_SPIRAL_HARMONICS,
    spiral_turns=4.0,
    theta_start=0.5 * math.pi,
    q_start=0.95,
    q_top=0.08,
    shelf_frac=0.08,
)

add_wood(
    wid='wood_hill_spiral',
    name='Bosque Colina Espiral',
    ring=ellipse_ring(
        3000.0, 1000.0, 465.0, 465.0,
        rot_deg=15.0, n=180, harmonics=HILL_SPIRAL_HARMONICS
    ),
    leaf_type='needleleaved',
)

# Primary roads along y = 400 and x = 3600
add_corridor(
    cid='road_primary_y400',
    name='Ruta Principal Norte (Y=400)',
    kind='primary',
    axis=[(0.0, 400.0), (PLAYABLE_M, 400.0)],
    half_width_m=5.0,
    feather_m=10.0,
    grade_max=0.06,
    tags={'highway': 'primary', 'name': 'Ruta Principal Norte'},
)

add_corridor(
    cid='road_primary_x3600',
    name='Ruta Principal Este (X=3600)',
    kind='primary',
    axis=[(3600.0, 0.0), (3600.0, PLAYABLE_M)],
    half_width_m=5.0,
    feather_m=10.0,
    grade_max=0.06,
    tags={'highway': 'primary', 'name': 'Ruta Principal Este'},
)

# Dirt roads (terracerías) along x = 500 and y = 3500
add_corridor(
    cid='track_x500_n',
    name='Terracería Oeste Norte (X=500)',
    kind='track',
    axis=[(500.0, 0.0), (500.0, 400.0)],
    half_width_m=3.0,
    feather_m=6.0,
    grade_max=0.08,
    tags={'highway': 'track', 'surface': 'dirt', 'name': 'Terracería Oeste Norte'},
)

add_corridor(
    cid='track_x500_s',
    name='Terracería Oeste (X=500)',
    kind='track',
    axis=[(500.0, 400.0), (500.0, PLAYABLE_M)],
    half_width_m=3.0,
    feather_m=6.0,
    grade_max=0.08,
    tags={'highway': 'track', 'surface': 'dirt', 'name': 'Terracería Oeste'},
)

add_corridor(
    cid='track_y3500',
    name='Terracería Sur (Y=3500)',
    kind='track',
    axis=[(0.0, 3500.0), (PLAYABLE_M, 3500.0)],
    half_width_m=3.0,
    feather_m=6.0,
    grade_max=0.08,
    tags={'highway': 'track', 'surface': 'dirt', 'name': 'Terracería Sur'},
)

# Pueblo Valle Bonito (NW): 2 cuadras / franjas lineales estilo ruso (derevnya)
# Dos calles Norte-Sur a X=750 y X=1050, enlazadas al norte con Ruta Y=400 y al sur por conector Y=850
add_corridor(
    cid='road_town_nw_c1',
    name='Calle Norte-Sur 1 (Valle Bonito)',
    kind='secondary',
    axis=[(750.0, 400.0), (750.0, 850.0)],
    half_width_m=3.5,
    feather_m=6.0,
    grade_max=0.05,
    tags={'highway': 'secondary', 'name': 'Calle Norte-Sur 1'},
)

add_corridor(
    cid='road_town_nw_c2',
    name='Calle Norte-Sur 2 (Valle Bonito)',
    kind='secondary',
    axis=[(1050.0, 400.0), (1050.0, 850.0)],
    half_width_m=3.5,
    feather_m=6.0,
    grade_max=0.05,
    tags={'highway': 'secondary', 'name': 'Calle Norte-Sur 2'},
)

add_corridor(
    cid='road_town_nw_conn_s',
    name='Calle Conectora Sur (Valle Bonito)',
    kind='secondary',
    axis=[(500.0, 850.0), (1050.0, 850.0)],
    half_width_m=3.5,
    feather_m=6.0,
    grade_max=0.05,
    tags={'highway': 'secondary', 'name': 'Calle Conectora Sur'},
)

# Pads residenciales a lo largo de las 2 calles (patios y casas)
add_pad(
    pid='pad_town_nw_c1',
    name='Pueblo Valle Bonito - Sector Oeste',
    kind='village',
    centre=(750.0, 625.0),
    size=(70.0, 410.0),
    feather_m=15.0,
    drain_grade=0.0,
    level=True,
    tags={'landuse': 'farmyard'},
)

add_pad(
    pid='pad_town_nw_c2',
    name='Pueblo Valle Bonito - Sector Este',
    kind='village',
    centre=(1050.0, 625.0),
    size=(70.0, 410.0),
    feather_m=15.0,
    drain_grade=0.0,
    level=True,
    tags={'landuse': 'farmyard'},
)

# 24 Huertos traseros pequeños (~0.7 ha c/u) estilo ruso detrás de las casas
for _k in range(6):
    _yk0 = 425.0 + _k * 70.0 + 3.0
    _yk1 = _yk0 + 64.0
    add_field(
        fid=f'field_vb_w{_k+1}',
        name=f'Huerto Valle Bonito O{_k+1}',
        ring=rect_ring(604.0, _yk0, 711.0, _yk1),
        tags={'landuse': 'farmland'},
    )
    add_field(
        fid=f'field_vb_c1_{_k+1}',
        name=f'Huerto Valle Bonito C1-{_k+1}',
        ring=rect_ring(789.0, _yk0, 896.0, _yk1),
        tags={'landuse': 'farmland'},
    )
    add_field(
        fid=f'field_vb_c2_{_k+1}',
        name=f'Huerto Valle Bonito C2-{_k+1}',
        ring=rect_ring(904.0, _yk0, 1011.0, _yk1),
        tags={'landuse': 'farmland'},
    )
    add_field(
        fid=f'field_vb_e{_k+1}',
        name=f'Huerto Valle Bonito E{_k+1}',
        ring=rect_ring(1089.0, _yk0, 1196.0, _yk1),
        tags={'landuse': 'farmland'},
    )

# Zona Industrial Norte: parque industrial con cuadrículas de 100 x 100 metros (2 filas x 5 columnas = 10 ha)
# Ocupa el área X=600..1100, Y=200..400 conectado directamente a Ruta Y=400
_IND_N_X0 = 600.0
_IND_N_Y0 = 200.0
_IND_N_COLS = 5
_IND_N_ROWS = 2
_IND_N_BLOCK_M = 100.0
_IND_N_SPAN_X_M = _IND_N_COLS * _IND_N_BLOCK_M   # 500.0 m
_IND_N_SPAN_Y_M = _IND_N_ROWS * _IND_N_BLOCK_M   # 200.0 m

add_pad(
    pid='pad_industrial_north',
    name='Zona Industrial Norte',
    kind='industry',
    centre=(_IND_N_X0 + _IND_N_SPAN_X_M / 2.0, _IND_N_Y0 + _IND_N_SPAN_Y_M / 2.0),
    size=(_IND_N_SPAN_X_M, _IND_N_SPAN_Y_M),
    feather_m=20.0,
    drain_grade=0.0,
    level=True,
    tags={'landuse': 'farmyard', 'building': 'industrial'},
)

# Calles internas Norte-Sur de la cuadrícula industrial norte (conectadas a Ruta Y=400)
for _k in range(_IND_N_COLS + 1):
    _sx = _IND_N_X0 + _k * _IND_N_BLOCK_M
    add_corridor(
        cid=f'road_ind_n_v{_k}',
        name=f'Calle Industrial N-S {_k+1}',
        kind='secondary',
        axis=[(_sx, _IND_N_Y0), (_sx, 400.0)],
        half_width_m=3.5,
        feather_m=6.0,
        grade_max=0.05,
        tags={'highway': 'secondary', 'name': f'Calle Industrial N-S {_k+1}'},
    )

# Calles internas Este-Oeste de la cuadrícula industrial norte
for _j in range(_IND_N_ROWS):
    _sy = _IND_N_Y0 + _j * _IND_N_BLOCK_M
    add_corridor(
        cid=f'road_ind_n_h{_j}',
        name=f'Calle Industrial E-O {_j+1}',
        kind='secondary',
        axis=[(_IND_N_X0, _sy), (_IND_N_X0 + _IND_N_SPAN_X_M, _sy)],
        half_width_m=3.5,
        feather_m=6.0,
        grade_max=0.05,
        tags={'highway': 'secondary', 'name': f'Calle Industrial E-O {_j+1}'},
    )

# Section Roads (all marked as terracerías between fields)
_PLSS_SEC_X = (1380.0, 2425.0, 3012.5)
_PLSS_SEC_Y = (950.0, 1525.0, 2200.0, 2750.0)
_ALL_ROAD_X = (500.0, 1380.0, 2425.0, 3012.5, 3600.0)
_ALL_ROAD_Y = (400.0, 950.0, 1525.0, 2200.0, 2750.0, 3500.0)

add_corridor(
    cid='road_sec_x1380',
    name='Terracería Sección X=1380',
    kind='track',
    axis=[(1380.0, 400.0), (1380.0, 3500.0)],
    half_width_m=3.0,
    feather_m=6.0,
    grade_max=0.08,
    tags={'highway': 'track', 'surface': 'dirt', 'name': 'Terracería Sección X=1380'},
)
add_corridor(
    cid='road_sec_x2425',
    name='Terracería Sección X=2425',
    kind='track',
    axis=[(2425.0, 400.0), (2425.0, 3500.0)],
    half_width_m=3.0,
    feather_m=6.0,
    grade_max=0.08,
    tags={'highway': 'track', 'surface': 'dirt', 'name': 'Terracería Sección X=2425'},
)
add_corridor(
    cid='road_sec_x3012',
    name='Terracería Sección X=3012',
    kind='track',
    axis=[(3012.5, 1525.0), (3012.5, 3500.0)],
    half_width_m=3.0,
    feather_m=6.0,
    grade_max=0.08,
    tags={'highway': 'track', 'surface': 'dirt', 'name': 'Terracería Sección X=3012'},
)
# Note: Terracería Sección Y=950 starts at x=1380 (outside the town) so it does NOT cross through the town
add_corridor(
    cid='road_sec_y950',
    name='Terracería Sección Y=950',
    kind='track',
    axis=[(1380.0, 950.0), (2425.0, 950.0)],
    half_width_m=3.0,
    feather_m=6.0,
    grade_max=0.08,
    tags={'highway': 'track', 'surface': 'dirt', 'name': 'Terracería Sección Y=950'},
)
# Terracería Sección Y=950 Tramo Oeste (acceso norte a parcelas 23 y 24)
add_corridor(
    cid='road_sec_y950_w',
    name='Terracería Sección Y=950 Oeste',
    kind='track',
    axis=[(564.0, 950.0), (1380.0, 950.0)],
    half_width_m=3.0,
    feather_m=6.0,
    grade_max=0.08,
    tags={'highway': 'track', 'surface': 'dirt', 'name': 'Terracería Sección Y=950 Oeste'},
)
# Caminos internos de acceso para las parcelas 23 y 24
add_corridor(
    cid='track_p23_24_ns',
    name='Camino Central Parcelas 23-24',
    kind='track',
    axis=[(960.0, 950.0), (960.0, 1525.0)],
    half_width_m=2.5,
    feather_m=5.0,
    grade_max=0.08,
    tags={'highway': 'track', 'surface': 'dirt', 'name': 'Camino Central Parcelas 23-24'},
)
add_corridor(
    cid='track_p23_w_ns',
    name='Camino Acceso Parcelas Cuadradas',
    kind='track',
    axis=[(760.0, 950.0), (760.0, 1525.0)],
    half_width_m=2.5,
    feather_m=5.0,
    grade_max=0.08,
    tags={'highway': 'track', 'surface': 'dirt', 'name': 'Camino Acceso Parcelas Cuadradas'},
)
add_corridor(
    cid='track_p23_24_h1',
    name='Camino Interparcelario 1',
    kind='track',
    axis=[(564.0, 1140.0), (1380.0, 1140.0)],
    half_width_m=2.5,
    feather_m=5.0,
    grade_max=0.08,
    tags={'highway': 'track', 'surface': 'dirt', 'name': 'Camino Interparcelario 1'},
)
add_corridor(
    cid='track_p23_24_h2',
    name='Camino Interparcelario 2',
    kind='track',
    axis=[(564.0, 1330.0), (1380.0, 1330.0)],
    half_width_m=2.5,
    feather_m=5.0,
    grade_max=0.08,
    tags={'highway': 'track', 'surface': 'dirt', 'name': 'Camino Interparcelario 2'},
)
add_corridor(
    cid='road_sec_y1525',
    name='Terracería Sección Y=1525',
    kind='track',
    axis=[(500.0, 1525.0), (3600.0, 1525.0)],
    half_width_m=3.0,
    feather_m=6.0,
    grade_max=0.08,
    tags={'highway': 'track', 'surface': 'dirt', 'name': 'Terracería Sección Y=1525'},
)
add_corridor(
    cid='road_sec_y2200',
    name='Terracería Sección Y=2200',
    kind='track',
    axis=[(500.0, 2200.0), (3600.0, 2200.0)],
    half_width_m=3.0,
    feather_m=6.0,
    grade_max=0.08,
    tags={'highway': 'track', 'surface': 'dirt', 'name': 'Terracería Sección Y=2200'},
)
add_corridor(
    cid='road_sec_y2750',
    name='Terracería Sección Y=2750',
    kind='track',
    axis=[(500.0, 2750.0), (3600.0, 2750.0)],
    half_width_m=3.0,
    feather_m=6.0,
    grade_max=0.08,
    tags={'highway': 'track', 'surface': 'dirt', 'name': 'Terracería Sección Y=2750'},
)

# Granjas (Granja Principal Sureste = 500 x 500 m, plus Granjas 1, 3, 4 = 387.3 x 387.3 m / 15 ha)
# Granja 2 reemplazada por Pueblo Este (grid 4x4 de 100x100m)
_FY15_SIDE_M = math.sqrt(15.0 * 10000.0)  # 387.3 m

# Granja Principal Sureste (SE corner, interior of (x=3600, y=3500))
_FARMYARD_SE_SIDE = 500.0
add_pad(
    pid='pad_farmyard_se',
    name='Granja Principal Sureste',
    kind='farmyard',
    centre=(3600.0 - _FARMYARD_SE_SIDE / 2.0, 3500.0 - _FARMYARD_SE_SIDE / 2.0),
    size=(_FARMYARD_SE_SIDE, _FARMYARD_SE_SIDE),
    feather_m=20.0,
    drain_grade=0.0,
    level=True,
    tags={'landuse': 'farmyard'},
)

# Granja 1 (along Ruta Principal Norte y=400, sitting south, between x=1380 and x=2425)
add_pad(
    pid='pad_farmyard_1',
    name='Granja 1',
    kind='farmyard',
    centre=(1900.0, 400.0 + _FY15_SIDE_M / 2.0),
    size=(_FY15_SIDE_M, _FY15_SIDE_M),
    feather_m=20.0,
    drain_grade=0.0,
    level=True,
    tags={'landuse': 'farmyard'},
)

# Pueblo Este: calle colectora paralela a Ruta 3600 (estilo ruso) con 8 casas y parcelas de 1.5 ha detrás
_TOWN_EAST_X0 = 3600.0
_TOWN_EAST_COLLECTOR_X = 3740.0
_TOWN_EAST_Y0 = 1600.0
_TOWN_EAST_Y1 = 2100.0
_TOWN_EAST_SPAN_Y_M = _TOWN_EAST_Y1 - _TOWN_EAST_Y0                   # 500.0 m

# Calle Colectora Norte-Sur paralela a Ruta 3600
add_corridor(
    cid='road_town_east_collector',
    name='Calle Colectora Pueblo Este',
    kind='secondary',
    axis=[(_TOWN_EAST_COLLECTOR_X, _TOWN_EAST_Y0), (_TOWN_EAST_COLLECTOR_X, _TOWN_EAST_Y1)],
    half_width_m=3.5,
    feather_m=6.0,
    grade_max=0.05,
    tags={'highway': 'secondary', 'name': 'Calle Colectora Pueblo Este'},
)

# 3 Enlaces perpendiculares a la Ruta Principal Este (X=3600)
add_corridor(
    cid='road_town_east_link_n',
    name='Acceso Norte Pueblo Este',
    kind='secondary',
    axis=[(_TOWN_EAST_X0, _TOWN_EAST_Y0), (_TOWN_EAST_COLLECTOR_X, _TOWN_EAST_Y0)],
    half_width_m=3.5,
    feather_m=6.0,
    grade_max=0.05,
    tags={'highway': 'secondary', 'name': 'Acceso Norte Pueblo Este'},
)

add_corridor(
    cid='road_town_east_link_c',
    name='Acceso Central Pueblo Este',
    kind='secondary',
    axis=[(_TOWN_EAST_X0, 1850.0), (_TOWN_EAST_COLLECTOR_X, 1850.0)],
    half_width_m=3.5,
    feather_m=6.0,
    grade_max=0.05,
    tags={'highway': 'secondary', 'name': 'Acceso Central Pueblo Este'},
)

add_corridor(
    cid='road_town_east_link_s',
    name='Acceso Sur Pueblo Este',
    kind='secondary',
    axis=[(_TOWN_EAST_X0, _TOWN_EAST_Y1), (3800.0, _TOWN_EAST_Y1)],
    half_width_m=3.5,
    feather_m=6.0,
    grade_max=0.05,
    tags={'highway': 'secondary', 'name': 'Acceso Sur Pueblo Este'},
)

# Franja residencial de casas y patios al este de la colectora
add_pad(
    pid='pad_town_east',
    name='Pueblo Este',
    kind='village',
    centre=(_TOWN_EAST_COLLECTOR_X + 25.0, (_TOWN_EAST_Y0 + _TOWN_EAST_Y1) / 2.0),
    size=(50.0, _TOWN_EAST_SPAN_Y_M),
    feather_m=15.0,
    drain_grade=0.0,
    level=True,
    tags={'landuse': 'farmyard'},
)

# 8 Parcelas agrícolas traseras de exactamente 1.50 ha cada una (60m ancho x 250m fondo = 15,000 m2)
_PE_SLOT_H = _TOWN_EAST_SPAN_Y_M / 8.0  # 62.5 m
for _k in range(8):
    _pe_y0 = _TOWN_EAST_Y0 + _k * _PE_SLOT_H + 1.25
    _pe_y1 = _pe_y0 + 60.0  # 60.0 m de ancho
    # 60.0 m de ancho x 250.0 m de fondo = 15,000 m2 = 1.50 ha exactas
    add_field(
        fid=f'field_pe_{_k+1}',
        name=f'Parcela Pueblo Este {_k+1} (1.5 ha)',
        ring=rect_ring(3790.0, _pe_y0, 4040.0, _pe_y1),
        tags={'landuse': 'farmland'},
    )

# Zona Industrial Pueblo Este: cuadrícula de 2x2 (100x100m cada bloque = 4.0 ha) en el lado sur del pueblo
_IND_E_X0 = 3600.0
_IND_E_Y0 = 2100.0
_IND_E_COLS = 2
_IND_E_ROWS = 2
_IND_E_BLOCK_M = 100.0
_IND_E_SPAN_X_M = _IND_E_COLS * _IND_E_BLOCK_M   # 200.0 m
_IND_E_SPAN_Y_M = _IND_E_ROWS * _IND_E_BLOCK_M   # 200.0 m

add_pad(
    pid='pad_industrial_east',
    name='Zona Industrial Pueblo Este',
    kind='industry',
    centre=(_IND_E_X0 + _IND_E_SPAN_X_M / 2.0, _IND_E_Y0 + _IND_E_SPAN_Y_M / 2.0),
    size=(_IND_E_SPAN_X_M, _IND_E_SPAN_Y_M),
    feather_m=15.0,
    drain_grade=0.0,
    level=True,
    tags={'landuse': 'farmyard', 'building': 'industrial'},
)

# Calles internas Norte-Sur (X=3700 y X=3800; X=3600 ya es Ruta 3600)
for _k in range(1, _IND_E_COLS + 1):
    _sx = _IND_E_X0 + _k * _IND_E_BLOCK_M
    add_corridor(
        cid=f'road_ind_e_v{_k}',
        name=f'Calle Industrial Este N-S {_k}',
        kind='secondary',
        axis=[(_sx, _IND_E_Y0), (_sx, _IND_E_Y0 + _IND_E_SPAN_Y_M)],
        half_width_m=3.5,
        feather_m=6.0,
        grade_max=0.05,
        tags={'highway': 'secondary', 'name': f'Calle Industrial Este N-S {_k}'},
    )

# Calles internas Este-Oeste (Y=2200 y Y=2300; Y=2100 es Acceso Sur Pueblo Este)
for _j in range(1, _IND_E_ROWS + 1):
    _sy = _IND_E_Y0 + _j * _IND_E_BLOCK_M
    add_corridor(
        cid=f'road_ind_e_h{_j}',
        name=f'Calle Industrial Este E-O {_j}',
        kind='secondary',
        axis=[(_IND_E_X0, _sy), (_IND_E_X0 + _IND_E_SPAN_X_M, _sy)],
        half_width_m=3.5,
        feather_m=6.0,
        grade_max=0.05,
        tags={'highway': 'secondary', 'name': f'Calle Industrial Este E-O {_j}'},
    )

# Reemplazo de Parcelas 23 y 24:
# Sector Oeste (antigua Parcela 23): 6 parcelas cuadradas de ~4 ha (192 x 182 m, ratio ~1.05)
# Sector Este (antigua Parcela 24): 3 parcelas rectangulares de ~8 ha (408 x 182 m, ratio ~2.24)
_P23_P24_ROWS_Y = [
    (954.0, 1136.0),  # Fila 1 (Norte)
    (1144.0, 1326.0), # Fila 2 (Centro)
    (1334.0, 1517.0), # Fila 3 (Sur)
]

# Sector Oeste: 6 parcelas cuadradas
_P23_COLS_X = [
    (564.0, 756.0),  # Columna A
    (764.0, 956.0),  # Columna B
]
_p23_labels = [('1A', '1B'), ('2A', '2B'), ('3A', '3B')]
for _r_idx, (_ry0, _ry1) in enumerate(_P23_P24_ROWS_Y):
    for _c_idx, (_cx0, _cx1) in enumerate(_P23_COLS_X):
        _lbl = _p23_labels[_r_idx][_c_idx]
        add_field(
            fid=f'field_p23_{_lbl.lower()}',
            name=f'Parcela 23-{_lbl} (Cuadrada 4 ha)',
            ring=rect_ring(_cx0, _ry0, _cx1, _ry1),
            tags={'landuse': 'farmland'},
        )

# Sector Este: 3 parcelas rectangulares
for _r_idx, (_ry0, _ry1) in enumerate(_P23_P24_ROWS_Y):
    add_field(
        fid=f'field_p24_{_r_idx+1}',
        name=f'Parcela 24-{_r_idx+1} (Rectangular 8 ha)',
        ring=rect_ring(964.0, _ry0, 1372.0, _ry1),
        tags={'landuse': 'farmland'},
    )

# Subdivisión de Parcelas 34 y 35 en campos rectangulares (4 campos de ~6.75 ha, ratio ~2.70)
# Parcela 34 (Norte, Y=1533..1857.5) dividida en 34-A y 34-B
add_field(
    fid='field_p34_a',
    name='Parcela 34-A (Rectangular)',
    ring=rect_ring(945.0, 1533.0, 1372.0, 1691.0),
    tags={'landuse': 'farmland'},
)
add_field(
    fid='field_p34_b',
    name='Parcela 34-B (Rectangular)',
    ring=rect_ring(945.0, 1699.0, 1372.0, 1857.5),
    tags={'landuse': 'farmland'},
)

# Parcela 35 (Sur, Y=1867.5..2192) dividida en 35-A y 35-B
add_field(
    fid='field_p35_a',
    name='Parcela 35-A (Rectangular)',
    ring=rect_ring(945.0, 1867.5, 1372.0, 2025.5),
    tags={'landuse': 'farmland'},
)
add_field(
    fid='field_p35_b',
    name='Parcela 35-B (Rectangular)',
    ring=rect_ring(945.0, 2033.5, 1372.0, 2192.0),
    tags={'landuse': 'farmland'},
)

# Camino de acceso entre Parcela 34 y Parcela 35 (Y=1862.5)
add_corridor(
    cid='track_sec_p34_p35',
    name='Camino Interparcelario 34-35',
    kind='track',
    axis=[(945.0, 1862.5), (1380.0, 1862.5)],
    half_width_m=2.5,
    feather_m=5.0,
    grade_max=0.08,
    tags={'highway': 'track', 'surface': 'dirt', 'name': 'Camino Interparcelario 34-35'},
)

# Granja 3 (along Terracería Oeste x=500, sitting east, well south of town between Y=1525 and Y=2200)
add_pad(
    pid='pad_farmyard_3',
    name='Granja 3',
    kind='farmyard',
    centre=(500.0 + _FY15_SIDE_M / 2.0, 1850.0),
    size=(_FY15_SIDE_M, _FY15_SIDE_M),
    feather_m=20.0,
    drain_grade=0.0,
    level=True,
    tags={'landuse': 'farmyard'},
)

# Granja 4 (along Terracería Sur y=3500, sitting south)
add_pad(
    pid='pad_farmyard_4',
    name='Granja 4',
    kind='farmyard',
    centre=(1800.0, 3500.0 + _FY15_SIDE_M / 2.0),
    size=(_FY15_SIDE_M, _FY15_SIDE_M),
    feather_m=20.0,
    drain_grade=0.0,
    level=True,
    tags={'landuse': 'farmyard'},
)

# Shelterbelts (rompevientos) along primary and dirt corridors
_BELT_WIDTH_M = 48.0
_BELT_SETBACK_M = 8.0
_BELT_RNG = random.Random(202506)


def _belt_free_runs(rcoord, orient, side_sign, step=20.0, clear_margin=25.0):
    s_vals = []
    s = 50.0
    while s <= PLAYABLE_M - 50.0:
        if orient == 'h':
            bx0, bx1 = s - step / 2.0, s + step / 2.0
            if side_sign < 0:
                by0 = rcoord - _BELT_SETBACK_M - _BELT_WIDTH_M
                by1 = rcoord - _BELT_SETBACK_M
            else:
                by0 = rcoord + _BELT_SETBACK_M
                by1 = rcoord + _BELT_SETBACK_M + _BELT_WIDTH_M
        else:
            by0, by1 = s - step / 2.0, s + step / 2.0
            if side_sign < 0:
                bx0 = rcoord - _BELT_SETBACK_M - _BELT_WIDTH_M
                bx1 = rcoord - _BELT_SETBACK_M
            else:
                bx0 = rcoord + _BELT_SETBACK_M
                bx1 = rcoord + _BELT_SETBACK_M + _BELT_WIDTH_M

        cand = rect_ring(bx0 - clear_margin, by0 - clear_margin,
                         bx1 + clear_margin, by1 + clear_margin)
        clash = False
        for rx in _ALL_ROAD_X:
            if rx != rcoord and (bx0 - 15.0 <= rx <= bx1 + 15.0):
                clash = True
                break
        if not clash:
            for ry in _ALL_ROAD_Y:
                if ry != rcoord and (by0 - 15.0 <= ry <= by1 + 15.0):
                    clash = True
                    break
        if not clash:
            for p in PADS:
                if rings_overlap(cand, p['ring']):
                    clash = True
                    break
        if not clash:
            for a in AREAS:
                if rings_overlap(cand, a['ring']):
                    clash = True
                    break
        if not clash:
            for w in WATER:
                if w.get('ring') and rings_overlap(cand, w['ring']):
                    clash = True
                    break
        if not clash:
            s_vals.append(s)
        s += step

    runs = []
    if not s_vals:
        return runs
    run_start = s_vals[0]
    prev_s = s_vals[0]
    for sv in s_vals[1:]:
        if abs(sv - prev_s - step) < 1.0:
            prev_s = sv
        else:
            if prev_s - run_start >= 120.0:
                runs.append((run_start, prev_s))
            run_start = sv
            prev_s = sv
    if prev_s - run_start >= 120.0:
        runs.append((run_start, prev_s))
    return runs


_belt_idx = 0
for _rcoord, _rorient, _rname in [
    (400.0, 'h', 'y400'),
    (3600.0, 'v', 'x3600'),
    (500.0, 'v', 'x500'),
    (3500.0, 'h', 'y3500'),
]:
    for _side_sign in [-1, 1]:
        _runs = _belt_free_runs(_rcoord, _rorient, _side_sign, step=20.0, clear_margin=15.0)
        for _s0, _s1 in _runs:
            _s0_m, _s1_m = _s0 + 5.0, _s1 - 5.0
            if _s1_m - _s0_m < 80.0:
                continue
            if _rorient == 'h':
                _bx0, _bx1 = _s0_m, _s1_m
                if _side_sign < 0:
                    _by0 = _rcoord - _BELT_SETBACK_M - _BELT_WIDTH_M
                    _by1 = _rcoord - _BELT_SETBACK_M
                else:
                    _by0 = _rcoord + _BELT_SETBACK_M
                    _by1 = _rcoord + _BELT_SETBACK_M + _BELT_WIDTH_M
            else:
                _by0, _by1 = _s0_m, _s1_m
                if _side_sign < 0:
                    _bx0 = _rcoord - _BELT_SETBACK_M - _BELT_WIDTH_M
                    _bx1 = _rcoord - _BELT_SETBACK_M
                else:
                    _bx0 = _rcoord + _BELT_SETBACK_M
                    _bx1 = _rcoord + _BELT_SETBACK_M + _BELT_WIDTH_M

            _belt_ring = rect_ring(_bx0, _by0, _bx1, _by1)
            _belt_idx += 1
            add_wood(
                wid=f'wood_belt_{_belt_idx}',
                name=f'Rompevientos {_belt_idx}',
                ring=_belt_ring,
                leaf_type='broadleaved',
            )

# Boundary forested border (Borde Boscoso)
_BORDER_WIDTH_M = 35.0
_BORDER_BREAKS = [
    ('N', [(400.0, 500.0), (3500.0, 3600.0)]),
    ('S', [(400.0, 500.0), (3500.0, 3600.0)]),
    ('E', [(300.0, 400.0), (3400.0, 3500.0)]),
    ('W', [(300.0, 400.0), (1450.0, 3050.0), (3400.0, 3500.0)]),
]

_b_idx = 0
for _b_side, _b_breaks in _BORDER_BREAKS:
    _segs = []
    _cur = 0.0
    for _br0, _br1 in sorted(_b_breaks):
        if _br0 - _cur >= 10.0:
            _segs.append((_cur, _br0 - 10.0))
        _cur = max(_cur, _br1 + 10.0)
    if PLAYABLE_M - _cur >= 10.0:
        _segs.append((_cur, PLAYABLE_M))

    for _s0, _s1 in _segs:
        if _s1 - _s0 < 20.0:
            continue
        _b_idx += 1
        if _b_side == 'N':
            _ring = rect_ring(_s0, 0.0, _s1, _BORDER_WIDTH_M)
            _sname = f'Borde Boscoso Norte {_b_idx}'
        elif _b_side == 'S':
            _ring = rect_ring(_s0, PLAYABLE_M - _BORDER_WIDTH_M, _s1, PLAYABLE_M)
            _sname = f'Borde Boscoso Sur {_b_idx}'
        elif _b_side == 'E':
            _ring = rect_ring(PLAYABLE_M - _BORDER_WIDTH_M, _s0, PLAYABLE_M, _s1)
            _sname = f'Borde Boscoso Este {_b_idx}'
        else:
            _ring = rect_ring(0.0, _s0, _BORDER_WIDTH_M, _s1)
            _sname = f'Borde Boscoso Oeste {_b_idx}'

        add_wood(
            wid=f'wood_border_{_b_idx}',
            name=_sname,
            ring=_ring,
            leaf_type='broadleaved',
            is_border=True,
        )

# Cortavientos en espacios libres:
# 1. Franja libre al oeste de Granja Principal Sureste y Parcela 65 (a lo largo de Terracería X=3012)
add_wood(
    wid='wood_belt_farm_se',
    name='Cortavientos Granja Principal - Parcela 65',
    ring=rect_ring(3025.0, 2760.0, 3075.0, 3485.0),
    leaf_type='broadleaved',
)

# 2. Espacio libre al lado este de Parcela 72 (al sur de Granja 4)
add_wood(
    wid='wood_belt_field_72',
    name='Cortavientos Parcela 72',
    ring=rect_ring(1908.0, 3895.0, 1988.0, 4055.0),
    leaf_type='broadleaved',
)

# 3. Espacios libres cerca de los pueblos:
# 3A. Franja sur de Pueblo Valle Bonito (entre el pueblo y las parcelas 23/24)
add_wood(
    wid='wood_belt_town_nw_1',
    name='Cortavientos Pueblo Valle Bonito Sur 1',
    ring=rect_ring(570.0, 875.0, 740.0, 925.0),
    leaf_type='broadleaved',
)
add_wood(
    wid='wood_belt_town_nw_2',
    name='Cortavientos Pueblo Valle Bonito Sur 2',
    ring=rect_ring(775.0, 875.0, 945.0, 925.0),
    leaf_type='broadleaved',
)
add_wood(
    wid='wood_belt_town_nw_3',
    name='Cortavientos Pueblo Valle Bonito Sur 3',
    ring=rect_ring(1065.0, 875.0, 1200.0, 925.0),
    leaf_type='broadleaved',
)

# 3B. Pueblo Este: Franja norte (entre Ruta Y=1525 y el pueblo) y Franja sur (al este de zona industrial)
add_wood(
    wid='wood_belt_town_east_n',
    name='Cortavientos Norte Pueblo Este',
    ring=rect_ring(3665.0, 1538.0, 4035.0, 1588.0),
    leaf_type='broadleaved',
)
add_wood(
    wid='wood_belt_town_east_s',
    name='Cortavientos Sur Pueblo Este',
    ring=rect_ring(3815.0, 2115.0, 4035.0, 2185.0),
    leaf_type='broadleaved',
)

# Curved obstacles for field boundary shaping
_CURVED_OBSTACLES = [
    (
        LAKE_WEST_SHAPE['cx'],
        LAKE_WEST_SHAPE['cy'],
        LAKE_WEST_SHAPE['a'],
        LAKE_WEST_SHAPE['b'],
        LAKE_WEST_SHAPE['rot_deg'],
        LAKE_WEST_SHAPE['harmonics'],
        12.0,
    ),
    (
        3000.0,
        1000.0,
        465.0,
        465.0,
        15.0,
        HILL_SPIRAL_HARMONICS,
        12.0,
    ),
]


def _retract_from_shelterbelts(x0, y0, x1, y1):
    cand = rect_ring(x0, y0, x1, y1)
    for a in AREAS:
        if a['kind'] == 'wood' and ('Rompevientos' in a.get('name', '') or 'Cortavientos' in a.get('name', '')):
            if rings_overlap(cand, a['ring']):
                ax0 = min(p[0] for p in a['ring'])
                ax1 = max(p[0] for p in a['ring'])
                ay0 = min(p[1] for p in a['ring'])
                ay1 = max(p[1] for p in a['ring'])
                if ax1 - ax0 > ay1 - ay0:
                    if y0 < ay0 and y1 > ay0:
                        y1 = ay0 - 8.0
                    elif y0 < ay1 and y1 > ay1:
                        y0 = ay1 + 8.0
                else:
                    if x0 < ax0 and x1 > ax0:
                        x1 = ax0 - 8.0
                    elif x0 < ax1 and x1 > ax1:
                        x0 = ax1 + 8.0
    return x0, y0, x1, y1


# Village & Industrial bounds to protect specialized layouts from macro-PLSS slicing
_VILLAGE_OBSTACLES = [
    # Pueblo Valle Bonito: area encompassing 2 street axes and small rear garden plots
    (595.0, 395.0, 1205.0, 875.0),
    # Pueblo Este: area encompassing collector road, house yards and 1.5 ha parcels
    (3595.0, 1595.0, 4045.0, 2105.0),
    # Zona Industrial Norte: cuadrícula 5x2 de 100x100m (X=600..1100, Y=200..400)
    (595.0, 195.0, 1105.0, 405.0),
    # Zona Industrial Pueblo Este: cuadrícula 2x2 de 100x100m (X=3600..3800, Y=2100..2300)
    (3595.0, 2095.0, 3805.0, 2305.0),
    # Sector Parcelas 23 y 24: parcelas cuadradas de 4 ha y rectangulares de 8 ha
    (560.0, 946.0, 1376.0, 1521.0),
    # Sector Parcelas 34 y 35: subdivididas en campos rectangulares
    (940.0, 1528.0, 1377.0, 2197.0),
]


def _subtract_pad_obstacles(boxes, min_dim=90.0, split_gap=8.0):
    cur_boxes = list(boxes)
    all_obs = []
    for p in PADS:
        px0 = min(pt[0] for pt in p['ring']) - split_gap
        px1 = max(pt[0] for pt in p['ring']) + split_gap
        py0 = min(pt[1] for pt in p['ring']) - split_gap
        py1 = max(pt[1] for pt in p['ring']) + split_gap
        all_obs.append((px0, py0, px1, py1))
    for vx0, vy0, vx1, vy1 in _VILLAGE_OBSTACLES:
        all_obs.append((vx0 - split_gap, vy0 - split_gap, vx1 + split_gap, vy1 + split_gap))

    for px0, py0, px1, py1 in all_obs:
        next_boxes = []
        for bx0, by0, bx1, by1 in cur_boxes:
            ox0, oy0 = max(bx0, px0), max(by0, py0)
            ox1, oy1 = min(bx1, px1), min(by1, py1)
            if ox1 <= ox0 or oy1 <= oy0:
                next_boxes.append((bx0, by0, bx1, by1))
                continue
            if bx0 < ox0 and (ox0 - bx0) >= min_dim:
                next_boxes.append((bx0, by0, ox0, by1))
            if bx1 > ox1 and (bx1 - ox1) >= min_dim:
                next_boxes.append((ox1, by0, bx1, by1))
            if by0 < oy0 and (oy0 - by0) >= min_dim:
                next_boxes.append((max(bx0, ox0), by0, min(bx1, ox1), oy0))
            if by1 > oy1 and (by1 - oy1) >= min_dim:
                next_boxes.append((max(bx0, ox0), oy1, min(bx1, ox1), by1))
        cur_boxes = next_boxes
    return cur_boxes


def _harmonic_q(x, y, cx, cy, a, b, rot_deg, harmonics, margin_m=12.0):
    rot = math.radians(rot_deg)
    c, s = math.cos(rot), math.sin(rot)
    u_rot = (x - cx) * c + (y - cy) * s
    v_rot = -(x - cx) * s + (y - cy) * c
    th = math.atan2(v_rot / b, u_rot / a)
    m = 1.0
    for amp, k, ph in harmonics:
        m += amp * math.sin(k * th + ph)
    a_eff = a * m + margin_m
    b_eff = b * m + margin_m
    q = math.hypot(u_rot / a_eff, v_rot / b_eff)
    return q, th, a_eff, b_eff


def _point_at_harmonic_q(q_target, th, cx, cy, a, b, rot_deg, harmonics, margin_m=12.0):
    rot = math.radians(rot_deg)
    c, s = math.cos(rot), math.sin(rot)
    m = 1.0
    for amp, k, ph in harmonics:
        m += amp * math.sin(k * th + ph)
    a_eff = (a * m + margin_m) * q_target
    b_eff = (b * m + margin_m) * q_target
    u_loc = a_eff * math.cos(th)
    v_loc = b_eff * math.sin(th)
    x = cx + u_loc * c - v_loc * s
    y = cy + u_loc * s + v_loc * c
    return x, y


def _box_touches_curve(x0, y0, x1, y1):
    for cx, cy, a, b, rot_deg, harm, marg in _CURVED_OBSTACLES:
        corner_qs = [
            _harmonic_q(sx, sy, cx, cy, a, b, rot_deg, harm, marg)[0]
            for sx in (x0, x1)
            for sy in (y0, y1)
        ]
        if min(corner_qs) <= 1.0 <= max(corner_qs):
            return True
    return False


def _plss_subdivide_section(x0, y0, x1, y1, gap=10.0):
    w, h = x1 - x0, y1 - y0
    qx = [(x0, x1)]
    if w > 650.0:
        mx = 0.5 * (x0 + x1)
        qx = [(x0, mx - 0.5 * gap), (mx + 0.5 * gap, x1)]
    qy = [(y0, y1)]
    if h > 650.0:
        my = 0.5 * (y0 + y1)
        qy = [(y0, my - 0.5 * gap), (my + 0.5 * gap, y1)]

    quarters = [(ax0, ay0, ax1, ay1) for ax0, ax1 in qx for ay0, ay1 in qy]
    parcels = []
    for qx0, qy0, qx1, qy1 in quarters:
        qw, qh = qx1 - qx0, qy1 - qy0
        touches_curve = _box_touches_curve(qx0, qy0, qx1, qy1)
        if touches_curve and qw >= 320.0 and qh >= 320.0:
            csx = [(qx0, 0.5 * (qx0 + qx1) - 0.5 * gap), (0.5 * (qx0 + qx1) + 0.5 * gap, qx1)]
            csy = [(qy0, 0.5 * (qy0 + qy1) - 0.5 * gap), (0.5 * (qy0 + qy1) + 0.5 * gap, qy1)]
            for cx0, cx1 in csx:
                for cy0, cy1 in csy:
                    parcels.append((cx0, cy0, cx1, cy1))
        else:
            parcels.append((qx0, qy0, qx1, qy1))
    return parcels


def _trim_box_to_curved_obstacles(x0, y0, x1, y1, min_dim=50.0, min_area_ha=1.4):
    """Trims box to clear curved obstacles (lake and spiral hill forest), preserving pure orthogonal rectangular shapes."""
    r_margin = rect_ring(x0 - 6.0, y0 - 6.0, x1 + 6.0, y1 + 6.0)
    has_overlap = False
    for w in WATER:
        if w.get('ring') and rings_overlap(r_margin, w['ring']):
            has_overlap = True
            break
    if not has_overlap:
        for a in AREAS:
            if a['id'] == 'wood_hill_spiral' and rings_overlap(r_margin, a['ring']):
                has_overlap = True
                break

    if not has_overlap:
        return rect_ring(x0, y0, x1, y1)

    w = x1 - x0
    h = y1 - y0
    best = None
    best_area = 0.0

    def _box_ok(bx0, by0, bx1, by1):
        cand = rect_ring(bx0 - 6.0, by0 - 6.0, bx1 + 6.0, by1 + 6.0)
        for w in WATER:
            if w.get('ring') and rings_overlap(cand, w['ring']):
                return False
        for a in AREAS:
            if a['id'] == 'wood_hill_spiral' and rings_overlap(cand, a['ring']):
                return False
        return True

    # 1D shrinks from each edge (x0, x1, y0, y1)
    for step in range(1, 40):
        nx0 = x0 + step * (w / 40.0)
        if x1 - nx0 < min_dim:
            break
        if _box_ok(nx0, y0, x1, y1):
            area = (x1 - nx0) * h / 10000.0
            if area >= min_area_ha and area > best_area:
                best = (nx0, y0, x1, y1)
                best_area = area
            break

    for step in range(1, 40):
        nx1 = x1 - step * (w / 40.0)
        if nx1 - x0 < min_dim:
            break
        if _box_ok(x0, y0, nx1, y1):
            area = (nx1 - x0) * h / 10000.0
            if area >= min_area_ha and area > best_area:
                best = (x0, y0, nx1, y1)
                best_area = area
            break

    for step in range(1, 40):
        ny0 = y0 + step * (h / 40.0)
        if y1 - ny0 < min_dim:
            break
        if _box_ok(x0, ny0, x1, y1):
            area = w * (y1 - ny0) / 10000.0
            if area >= min_area_ha and area > best_area:
                best = (x0, ny0, x1, y1)
                best_area = area
            break

    for step in range(1, 40):
        ny1 = y1 - step * (h / 40.0)
        if ny1 - y0 < min_dim:
            break
        if _box_ok(x0, y0, x1, ny1):
            area = w * (ny1 - y0) / 10000.0
            if area >= min_area_ha and area > best_area:
                best = (x0, y0, x1, ny1)
                best_area = area
            break

    # 2D corner shrinks
    for corner in [('x0', 'y0'), ('x1', 'y0'), ('x0', 'y1'), ('x1', 'y1')]:
        cx_dir, cy_dir = corner
        for s1 in range(2, 25, 2):
            for s2 in range(2, 25, 2):
                nx0 = x0 + s1 * (w / 30.0) if cx_dir == 'x0' else x0
                nx1 = x1 - s1 * (w / 30.0) if cx_dir == 'x1' else x1
                ny0 = y0 + s2 * (h / 30.0) if cy_dir == 'y0' else y0
                ny1 = y1 - s2 * (h / 30.0) if cy_dir == 'y1' else y1
                if nx1 - nx0 < min_dim or ny1 - ny0 < min_dim:
                    continue
                area = (nx1 - nx0) * (ny1 - ny0) / 10000.0
                if area <= best_area or area < min_area_ha:
                    continue
                if _box_ok(nx0, ny0, nx1, ny1):
                    best = (nx0, ny0, nx1, ny1)
                    best_area = area

    if best:
        return rect_ring(best[0], best[1], best[2], best[3])
    return None


# Macro PLSS sections bounded by clean strip and road network
_X_GRID = [EDGE_CLEAR_M, 500.0, 1380.0, 2425.0, 3012.5, 3600.0, PLAYABLE_M - EDGE_CLEAR_M]
_Y_GRID = [EDGE_CLEAR_M, 400.0, 950.0, 1525.0, 2200.0, 2750.0, 3500.0, PLAYABLE_M - EDGE_CLEAR_M]

_field_idx = 0
for _iy in range(len(_Y_GRID) - 1):
    for _ix in range(len(_X_GRID) - 1):
        _sx0, _sx1 = _X_GRID[_ix] + 8.0, _X_GRID[_ix + 1] - 8.0
        _sy0, _sy1 = _Y_GRID[_iy] + 8.0, _Y_GRID[_iy + 1] - 8.0
        for _px0, _py0, _px1, _py1 in _plss_subdivide_section(_sx0, _sy0, _sx1, _sy1, gap=10.0):
            for _cx0, _cy0, _cx1, _cy1 in _subtract_pad_obstacles([(_px0, _py0, _px1, _py1)], min_dim=80.0):
                _cx0, _cy0, _cx1, _cy1 = _retract_from_shelterbelts(_cx0, _cy0, _cx1, _cy1)
                if (_cx1 - _cx0) < 70.0 or (_cy1 - _cy0) < 70.0 or (_cx1 - _cx0) * (_cy1 - _cy0) / 10000.0 < 2.5:
                    continue
                _fring = _trim_box_to_curved_obstacles(_cx0, _cy0, _cx1, _cy1)
                if _fring is not None and ring_area_ha(_fring) >= 2.5:
                    _field_idx += 1
                    add_field(
                        fid=f'field_plss_{_field_idx}',
                        name=f'Parcela {_field_idx}',
                        ring=_fring,
                        tags={'landuse': 'farmland'},
                    )


# ==================================================================================
# 7. Registry Queries & Roughness Stats
# ==================================================================================
def corridors():
    return list(CORRIDORS)


def water():
    return list(WATER)


def water_axes():
    return [(w['axis'], w['name']) for w in WATER if w.get('axis')]


def pads():
    return list(PADS)


def areas():
    return list(AREAS)


def hills():
    return list(HILLS)


def load_roughness(path=None):
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                'dem_generator', 'terrain_stats.json')
    if not os.path.exists(path):
        return None
    with open(path) as fh:
        data = json.load(fh)
    n = data['n']
    cell = data['cell_m']
    x0, y0 = data['origin'][0], data['origin'][1]
    rough = data['roughness']

    def lookup(x, y):
        c = int((x - x0) // cell)
        r = int((y - y0) // cell)
        c = 0 if c < 0 else (n - 1 if c >= n else c)
        r = 0 if r < 0 else (n - 1 if r >= n else r)
        return rough[r * n + c]
    return lookup


# ==================================================================================
# 8. Validation & Integrity Checks
# ==================================================================================
def validate():
    bad = []

    if RIM_RAMP_M <= 0.0:
        bad.append(f"rim: apron ({RIM_APRON_M:.0f} m) and shoulder "
                   f"({RIM_BACK_M:.0f} m) leave no ramp room")

    all_records = CORRIDORS + WATER + PADS + AREAS + HILLS
    ids = [r['id'] for r in all_records]
    seen = set()
    for i in ids:
        if i in seen:
            bad.append(f"{i}: duplicate record id")
        seen.add(i)

    for c in CORRIDORS:
        if c['kind'] not in HIGHWAY_CLASS and c['kind'] != 'rail':
            bad.append(f"{c['id']}: unknown corridor class {c['kind']!r}")
        if len(c['axis']) < 2:
            bad.append(f"{c['id']}: alignment needs >= 2 points")
        for x, y in c['axis']:
            if not (-OFFSET_M - EXTEND_M <= x <= PLAYABLE_M + OFFSET_M + EXTEND_M
                    and -OFFSET_M - EXTEND_M <= y <= PLAYABLE_M + OFFSET_M + EXTEND_M):
                bad.append(f"{c['id']}: point ({x:.1f}, {y:.1f}) outside canvas bounds")
                break

    for p in PADS:
        ring = p.get('ring', [])
        if len(ring) < 4 or math.dist(ring[0], ring[-1]) > 1e-6:
            bad.append(f"{p['id']}: pad ring does not close on first point")

    for w in WATER:
        if not w.get('tags'):
            bad.append(f"{w['id']}: untagged water body")
        elif not any(k in w['tags'] and (v is None or w['tags'][k] == v) for k, v in RENDERED_TAGS):
            bad.append(f"{w['id']}: tagged {w['tags']} - no renderer draws that")
        if w.get('ring'):
            r = w['ring']
            if len(r) < 4 or math.dist(r[0], r[-1]) > 1e-6:
                bad.append(f"{w['id']}: ring does not close on first point")
            for x, y in r:
                if not (-0.5 <= x <= PLAYABLE_M + 0.5 and -0.5 <= y <= PLAYABLE_M + 0.5):
                    bad.append(f"{w['id']}: point ({x:.1f}, {y:.1f}) outside playable bounds")
                    break

    for a in AREAS:
        if not a.get('tags'):
            bad.append(f"{a['id']}: untagged area")
        elif not any(k in a['tags'] and (v is None or a['tags'][k] == v) for k, v in RENDERED_TAGS):
            bad.append(f"{a['id']}: tagged {a['tags']} - no renderer draws that")
        r = a.get('ring', [])
        if len(r) < 4 or math.dist(r[0], r[-1]) > 1e-6:
            bad.append(f"{a['id']}: ring does not close on first point")
        for x, y in r:
            if not (-0.5 <= x <= PLAYABLE_M + 0.5 and -0.5 <= y <= PLAYABLE_M + 0.5):
                bad.append(f"{a['id']}: point ({x:.1f}, {y:.1f}) outside playable bounds")
                break

    return bad


def summary():
    if not (CORRIDORS or WATER or PADS or AREAS or HILLS):
        return (f"{PLAYABLE_M:.0f} m playable on a {CANVAS_M:.0f} m canvas, clean layout (no features), "
                f"flat canvas at base datum {BASE_ELEV_M:.1f} m")
    woods = [a for a in AREAS if a.get('kind') == 'wood']
    water_str = f", {len(WATER)} water body" if len(WATER) == 1 else (f", {len(WATER)} water bodies" if len(WATER) > 1 else "")
    hill_str = f", {len(HILLS)} hill" if len(HILLS) == 1 else (f", {len(HILLS)} hills" if len(HILLS) > 1 else "")
    return (f"{PLAYABLE_M:.0f} m playable on a {CANVAS_M:.0f} m canvas, "
            f"{len(CORRIDORS)} roads, {len(PADS)} pads, {len(FIELDS)} fields, "
            f"{len(woods)} woods{water_str}{hill_str}, "
            f"base datum {BASE_ELEV_M:.1f} m")


if __name__ == '__main__':
    print("=== map_layout self-check ===")
    print("  ", summary())
    sw = global_to_local(*local_to_global(0.0, PLAYABLE_M))
    ne = global_to_local(*local_to_global(PLAYABLE_M, 0.0))
    print(f"   centre {LAT_CENTER:.4f}, {LON_CENTER:.4f}; projection round-trips "
          f"corners to ({sw[0]:.3f}, {sw[1]:.3f}) and ({ne[0]:.3f}, {ne[1]:.3f})")
    print(f"   canvas metres run {-OFFSET_M:.0f} .. {PLAYABLE_M + OFFSET_M:.0f}, "
          f"alignments out to {EDGE_MIN:.0f} .. {EDGE_MAX:.0f}")
    problems = validate()
    if problems:
        print("\n   PROBLEMS:")
        for p in problems:
            print("    -", p)
    else:
        print("\n   layout is clean and sound")
