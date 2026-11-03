"""Coarse offline place suggestions. No network requests or precise-address claims."""
import json
import math
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).with_name('geodata')


def km(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    h = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 12742 * math.asin(math.sqrt(min(1, h)))


@lru_cache(maxsize=1)
def gazetteer():
    countries = []
    names = {}
    for country in json.loads((DATA / 'countries.json').read_text(encoding='utf-8')):
        names[country['code']] = country['name']
        for polygon in country['polygons']:
            ring = polygon[0]
            bounds = (min(p[0] for p in ring), min(p[1] for p in ring),
                      max(p[0] for p in ring), max(p[1] for p in ring))
            countries.append((bounds, polygon, country['code']))
    cells = defaultdict(list)
    for city in json.loads((DATA / 'cities.json').read_text(encoding='utf-8')):
        cells[(math.floor(city[0]), math.floor(city[1]))].append(city)
    return countries, names, cells


def in_ring(x, y, ring):
    inside = False
    x1, y1 = ring[-1]
    for x2, y2 in ring:
        if (y1 > y) != (y2 > y) and x < (x2-x1)*(y-y1)/(y2-y1)+x1:
            inside = not inside
        x1, y1 = x2, y2
    return inside


def locate(lat, lon):
    # A ~1 km cache is sufficient for album titles; not for street addresses.
    return _locate(round(lat, 2), round(lon, 2))


@lru_cache(maxsize=20000)
def _locate(lat, lon):
    countries, names, cells = gazetteer()
    code = None
    for (west, south, east, north), rings, candidate in countries:
        if west <= lon <= east and south <= lat <= north and in_ring(lon, lat, rings[0]):
            if not any(in_ring(lon, lat, hole) for hole in rings[1:]):
                code = candidate
                break
    candidates = []
    width = min(20, math.ceil(1 / max(.05, math.cos(math.radians(lat)))))
    for y in range(math.floor(lat)-1, math.floor(lat)+2):
        for x in range(math.floor(lon)-width, math.floor(lon)+width+1):
            for city in cells.get((y, (x+180) % 360-180), []):
                d = km((lat, lon), city[:2])
                if d <= 70:
                    candidates.append((city, d))
    if not code and candidates:
        nearest, d = min(candidates, key=lambda item: item[1])
        if d <= 20:  # coastal/island photographs outside the simplified shoreline
            code = nearest[3]
    candidates = [(c, d) for c, d in candidates if c[3] == code]
    # Prefer the regional city to a nearby suburb, but not a distant metropolis.
    city, d = max(candidates, key=lambda item: math.sqrt(max(1, item[0][4]))/(item[1]+15)) if candidates else (None, None)
    return dict(code=code, country=names.get(code), city=city[2] if city else None,
                city_distance=round(d) if d is not None else None)
