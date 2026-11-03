"""Build the offline gazetteer from Natural Earth GeoJSON (public domain).

Download ne_50m_admin_0_countries and ne_10m_populated_places GeoJSON from
https://github.com/nvkelso/natural-earth-vector/tree/master/geojson into
data/geo-source/<name>.json, then run this script. No photo data is involved.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
source = ROOT / 'data' / 'geo-source'
destination = ROOT / 'mediaviewer' / 'geodata'
destination.mkdir(exist_ok=True)

countries = []
for f in json.loads((source / 'ne_50m_admin_0_countries.json').read_text(encoding='utf-8'))['features']:
    p, geometry = f['properties'], f['geometry']
    polygons = geometry['coordinates'] if geometry['type'] == 'MultiPolygon' else [geometry['coordinates']]
    countries.append(dict(code=p['ADM0_A3'], name=p['NAME_RU'] or p['NAME'], polygons=polygons))

cities = []
for f in json.loads((source / 'ne_10m_populated_places.json').read_text(encoding='utf-8'))['features']:
    p = f['properties']
    cities.append([p['LATITUDE'], p['LONGITUDE'], p['NAME_RU'] or p['NAME'], p['ADM0_A3'], p['POP_MAX']])

for name, data in [('countries', countries), ('cities', cities)]:
    (destination / (name + '.json')).write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    print(name, len(data))
