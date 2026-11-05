#!/usr/bin/env python3
"""Write `map.osm`: the vector layout of the playable area.

Everything it draws comes out of the registries in `map_layout.py`.
"""
import math
import os
import sys
import xml.dom.minidom as minidom
import xml.etree.ElementTree as ET
from collections import Counter

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
import map_extent as mx                                              # noqa: E402
import map_layout as ml                                             # noqa: E402

OUT_NAME = "map.osm"
STAMP = {'version': '1', 'timestamp': '2026-09-02T12:00:00Z',
         'changeset': '1', 'uid': '1', 'user': 'generator'}


# ==================================================================================
# node and way pools
# ==================================================================================
class Osm:
    def __init__(self):
        self.nodes = {}
        self.node_coords = {}
        self.ways = []
        self._nid = 1
        self._wid = 1

    def node(self, x, y):
        key = (round(float(x), 3), round(float(y), 3))
        if key not in self.nodes:
            self.nodes[key] = self._nid
            self.node_coords[self._nid] = ml.local_to_global(*key)
            self._nid += 1
        return self.nodes[key]

    def way(self, coords, tags):
        pts = [(float(x), float(y)) for x, y in coords]
        w = {'id': self._wid, 'coords': pts, 'tags': tags,
             'node_refs': [self.node(x, y) for x, y in pts]}
        self.ways.append(w)
        self._wid += 1
        return w

    def area(self, ring, tags):
        pts = list(ring)
        if math.dist(pts[0], pts[-1]) > 1e-9:
            pts.append(pts[0])
        w = self.way(pts[:-1], tags)
        w['coords'].append(w['coords'][0])
        w['node_refs'].append(w['node_refs'][0])
        return w


def strip_ring(ring):
    m = ml.EDGE_CLEAR_M
    return ml.clip_ring_to_rect(ring, m, m, ml.PLAYABLE_M - m, ml.PLAYABLE_M - m)


# ==================================================================================
# features
# ==================================================================================
def emit_water(osm):
    n = 0
    for w in ml.water():
        tags = {'natural': 'water', 'water': w.get('kind', 'water'), 'name': w['name']}
        if w.get('ring'):
            osm.area(w['ring'], tags)
            n += 1
            continue
        for run in ml.clip_polyline(w['axis'], -ml.EXTEND_M, -ml.EXTEND_M,
                                    ml.PLAYABLE_M + ml.EXTEND_M,
                                    ml.PLAYABLE_M + ml.EXTEND_M):
            osm.area(ml.clip_ring_to_rect(ml.buffer_ring(run, w['water_half_w']),
                                          0.0, 0.0, ml.PLAYABLE_M, ml.PLAYABLE_M), dict(tags))
            n += 1
    return n


def emit_pads(osm):
    n = 0
    area_ids = {a['id'] for a in ml.areas()}
    for p in ml.pads():
        if not p.get('tags') or p['id'].replace('pad', 'area') in area_ids:
            continue
        osm.area(p['ring'], dict(p['tags'], name=p['name']))
        n += 1
    return n


def emit_areas(osm):
    n = 0
    for a in ml.areas():
        if a.get('is_border'):
            ring = ml.clip_ring_to_rect(a['ring'], 0.0, 0.0, ml.PLAYABLE_M, ml.PLAYABLE_M)
        else:
            ring = strip_ring(a['ring']) if ml.EDGE_CLEAR_M > 0 else a['ring']
        if len(ring) < 4 or ml.ring_area_ha(ring) < 0.15:
            continue
        tags = dict(a['tags'])
        if 'name' not in tags and a.get('name'):
            tags['name'] = a['name']
        osm.area(ring, tags)
        n += 1
    return n


def emit_corridors(osm):
    corr = ml.corridors()
    counts = Counter()
    bridges = 0
    for c in corr:
        tags = dict(c.get('tags', {}))
        if 'highway' not in tags and 'railway' not in tags:
            if c['kind'] == 'rail':
                tags['railway'] = 'rail'
            else:
                tags['highway'] = ml.HIGHWAY_CLASS.get(c['kind'], 'tertiary')
        if 'name' not in tags and c.get('name'):
            tags['name'] = c['name']
        if c.get('bridge_spans'):
            tags['bridge'] = 'yes'
            tags['layer'] = '1'
            bridges += 1
        osm.way(c['axis'], tags)
        counts[c['kind']] += 1
    return counts, bridges


def connect_road_crossings(osm):
    roads = [w for w in osm.ways if 'highway' in w['tags'] or 'railway' in w['tags']]
    added = 0
    for i, a in enumerate(roads):
        for b in roads[i + 1:]:
            for ia in range(len(a['coords']) - 1):
                for ib in range(len(b['coords']) - 1):
                    p = _seg_cross(a['coords'][ia], a['coords'][ia + 1],
                                   b['coords'][ib], b['coords'][ib + 1])
                    if p is None:
                        continue
                    for w in (a, b):
                        nid = osm.node(*p)
                        if nid not in w['node_refs']:
                            k = _insert_index(w['coords'], p)
                            w['coords'].insert(k, p)
                            w['node_refs'].insert(k, nid)
                            added += 1
    return added


def _seg_cross(a, b, c, d):
    r = (b[0] - a[0], b[1] - a[1])
    s = (d[0] - c[0], d[1] - c[1])
    den = r[0] * s[1] - r[1] * s[0]
    if abs(den) < 1e-12:
        return None
    t = ((c[0] - a[0]) * s[1] - (c[1] - a[1]) * s[0]) / den
    u = ((c[0] - a[0]) * r[1] - (c[1] - a[1]) * r[0]) / den
    if -1e-6 <= t <= 1.0 + 1e-6 and -1e-6 <= u <= 1.0 + 1e-6:
        t_c = min(max(t, 0.0), 1.0)
        return (a[0] + t_c * r[0], a[1] + t_c * r[1])
    return None


def _insert_index(coords, p):
    for i in range(len(coords) - 1):
        if ml.seg_point_dist(p, coords[i], coords[i + 1]) < 1e-6:
            return i + 1
    return len(coords)


# ==================================================================================
# writing
# ==================================================================================
def write_osm(osm, path):
    minlat, minlon, maxlat, maxlon = ml.bounds()
    root = ET.Element('osm', version='0.6', generator='FS25 map pipeline')
    root.append(ET.Comment(
        f"\n       Playable area: {ml.PLAYABLE_M:.0f} x {ml.PLAYABLE_M:.0f} m, "
        f"centre {ml.LAT_CENTER:.4f}, {ml.LON_CENTER:.4f}\n"
        "       Local coordinates are playable metres, x east, y south from the north\n"
        f"       edge, so the centre of the map is ({ml.HALF_M:.0f}, {ml.HALF_M:.0f}).\n"
        f"       Projection: equirectangular about the centre, {ml.M_PER_DEG:.1f} m per\n"
        f"       degree of latitude and {ml.M_PER_DEG:.1f} * cos(LAT_CENTER) m per\n"
        "       degree of longitude.\n"
        "       Geometry comes from map_layout.py, shared with the DEM generator.\n"
        f"       Layout: {ml.summary()}\n  "))
    ET.SubElement(root, 'bounds', {
        'minlat': f"{minlat:.10f}", 'minlon': f"{minlon:.10f}",
        'maxlat': f"{maxlat:.10f}", 'maxlon': f"{maxlon:.10f}"})

    for nid in sorted(osm.node_coords):
        lat, lon = osm.node_coords[nid]
        ET.SubElement(root, 'node', {'id': str(nid), 'lat': f"{lat:.10f}",
                                     'lon': f"{lon:.10f}", **STAMP})
    for w in osm.ways:
        elem = ET.SubElement(root, 'way', {'id': str(w['id']), **STAMP})
        for ref in w['node_refs']:
            ET.SubElement(elem, 'nd', ref=str(ref))
        for k, v in w['tags'].items():
            ET.SubElement(elem, 'tag', k=k, v=str(v))

    pretty = minidom.parseString(ET.tostring(root, encoding='utf-8')).toprettyxml(
        indent='  ', encoding='utf-8')
    with open(path, "wb") as fh:
        fh.write(pretty)


def main():
    print("=== Generating OSM data ===")
    print(f"   centre {ml.LAT_CENTER:.4f}, {ml.LON_CENTER:.4f}")
    print(f"   {ml.summary()}")
    problems = ml.validate()
    if problems:
        print("!! layout problems:")
        for p in problems:
            print("   -", p)
        return 1

    osm = Osm()
    print(f"1. Water...            {emit_water(osm)} way(s)")
    print(f"2. Pads...             {emit_pads(osm)} way(s)")
    print(f"3. Areas...            {emit_areas(osm)} way(s)")
    counts, bridges = emit_corridors(osm)
    added = connect_road_crossings(osm)
    print(f"4. Roads and railway...{sum(counts.values()):4d} way(s), {bridges} on "
          f"bridges, {added} junction node(s) stitched in")

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), OUT_NAME)
    write_osm(osm, out)

    root = ET.parse(out).getroot()
    b = root.find('bounds').attrib
    sw = mx.global_to_local(float(b['minlat']), float(b['minlon']))
    ne = mx.global_to_local(float(b['maxlat']), float(b['maxlon']))
    nodes = root.findall('node')
    local = [mx.global_to_local(float(n.get('lat')), float(n.get('lon'))) for n in nodes]
    outside = [p for p in local
               if not (-0.5 <= p[0] <= ml.PLAYABLE_M + 0.5
                       and -0.5 <= p[1] <= ml.PLAYABLE_M + 0.5)]
    print(f"   extent {abs(ne[0] - sw[0]):.3f} x {abs(sw[1] - ne[1]):.3f} m, "
          f"{len(nodes)} nodes, {len(root.findall('way'))} ways")
    if abs(abs(ne[0] - sw[0]) - ml.PLAYABLE_M) > 0.5 or \
            abs(abs(sw[1] - ne[1]) - ml.PLAYABLE_M) > 0.5:
        print(f"!! the bounds do not round-trip to {ml.PLAYABLE_M:.0f} m square")
        return 1
    if outside:
        print(f"!! {len(outside)} nodes outside the playable area, e.g. "
              f"{outside[0][0]:.1f},{outside[0][1]:.1f}")
        return 1
    print(f"[+] Wrote '{out}'.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
