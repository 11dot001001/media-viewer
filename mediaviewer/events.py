"""Local, deterministic time/location groups with path-based manual overrides."""
from __future__ import annotations

import hashlib
import json
import math
import uuid
from contextlib import closing
from collections import defaultdict
from datetime import datetime, timezone
from statistics import median

from . import places

HOUR = 3600


def distance(a, b):
    """Great-circle distance in km; missing/invalid GPS never implies a location."""
    coords = [a.get('lat'), a.get('lon'), b.get('lat'), b.get('lon')]
    if any(v is None or not math.isfinite(v) for v in coords):
        return None
    if any(abs(coords[i]) > (90 if i % 2 == 0 else 180) for i in range(4)):
        return None
    lat1, lon1, lat2, lon2 = map(math.radians, coords)
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(min(1, h)))


def infer_home(rows):
    """Count distinct days, not frames: a prolific holiday must not become home."""
    cells = defaultdict(list)
    for r in rows:
        if r['ts'] is not None and r['ts_source'] != 'mtime' and distance(r, r) is not None:
            cells[(round(r['lat'] * 10), round(r['lon'] * 10))].append(r)
    if not cells:
        return None
    def score(items):
        days = {int(r['ts'] // 86400) for r in items}
        return len(days), max(days)-min(days)
    center = max(cells.values(), key=score)
    if score(center)[0] < 10 or score(center)[1] < 45:
        return None  # a folder containing only one holiday is not evidence of home
    return dict(lat=median(r['lat'] for r in center), lon=median(r['lon'] for r in center))


def automatic(rows, mode, home=None):
    dated = sorted((r for r in rows if r['ts'] is not None and r['ts_source'] != 'mtime'),
                   key=lambda r: (r['ts'], r['path']))
    unknown = sorted((r for r in rows if r['ts'] is None or r['ts_source'] == 'mtime'), key=lambda r: r['path'])
    home = home or infer_home(rows)
    groups = []
    assigned = set()
    if home:
        # GPS anchors delimit excursions. Intermediate frames without GPS, road
        # stops and quiet days remain inside the same journey.
        windows = []
        away = []
        previous_home = None

        def finish(next_home=None):
            if len(away) >= 2:
                start = max(away[0]['ts'] - 36*HOUR, (previous_home or -float('inf')) + .001)
                end = min(away[-1]['ts'] + 36*HOUR, (next_home or float('inf')) - .001)
                windows.append((start, end))

        for r in dated:
            d = distance(home, r)
            if d is None:
                continue
            if d <= 15:
                finish(r['ts'])
                away = []
                previous_home = r['ts']
            elif d >= 30:
                if away and (r['ts']-away[-1]['ts'] > 10*24*HOUR or r['ts']-away[0]['ts'] > 90*24*HOUR):
                    finish()
                    away = []
                away.append(r)
        finish()
        # Do not let padding overlap a neighbouring excursion.
        for i in range(len(windows)-1):
            a, b = windows[i], windows[i+1]
            if a[1] >= b[0]:
                middle = (a[1]+b[0])/2
                windows[i], windows[i+1] = (a[0], middle), (middle+.001, b[1])
        cursor = 0
        for start, end in windows:
            members = []
            while cursor < len(dated) and dated[cursor]['ts'] <= end:
                r = dated[cursor]
                if r['ts'] >= start:
                    members.append(r)
                    assigned.add(r['id'])
                cursor += 1
            if members:
                groups.append(members)

    current = []
    last_gps = None
    for r in dated:
        if r['id'] in assigned:
            if current:
                groups.append(current)
                current, last_gps = [], None
            continue
        split = False
        if current:
            gap = r['ts'] - current[-1]['ts']
            span = r['ts'] - current[0]['ts']
            km = distance(last_gps, r) if last_gps else None
            if home or mode == 'events':
                split = gap > 12 * HOUR or span > 36 * HOUR or (km is not None and km > 100)
            else:
                # With no known home use a conservative time-based fallback.
                split = gap > 7 * 24 * HOUR or span > 90 * 24 * HOUR
                if last_gps and distance(r, r) is not None and gap > 36*HOUR:
                    before, after = places.locate(last_gps['lat'], last_gps['lon']), places.locate(r['lat'], r['lon'])
                    split |= bool(before['code'] and after['code'] and before['code'] != after['code'])
        if split:
            groups.append(current)
            current, last_gps = [], None
        current.append(r)
        if distance(r, r) is not None:
            last_gps = r
    if current:
        groups.append(current)
    if unknown:
        groups.append(unknown)
    return groups


def read_rows(c):
    return [dict(r) for r in c.execute(
        'SELECT id, path, ts, ts_source, lat, lon FROM media WHERE parent_id IS NULL AND analyzed=1')]


def read_state(c, mode):
    row = c.execute('SELECT value FROM settings WHERE key=?', ('events:' + mode,)).fetchone()
    return json.loads(row[0]) if row else []


def preferences(c):
    row = c.execute("SELECT value FROM settings WHERE key='event_preferences'").fetchone()
    result = dict(min_count=10, occasion_day='', occasion_name='')
    if row:
        result.update(json.loads(row[0]))
    return result


def save_preferences(library, values):
    with closing(library.connect()) as c, c:
        c.execute("INSERT INTO settings(key,value) VALUES('event_preferences',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (json.dumps(values, ensure_ascii=False),))


def describe(members, home):
    gps = [r for r in members if distance(r, r) is not None]
    away = [r for r in gps if home is None or distance(home, r) >= 30]
    samples = away or gps
    # One vote per location/day prevents burst photography and road stops from
    # outweighing days spent at the destination. Bound geocoding work per group.
    samples = samples[::max(1, len(samples)//160)]
    countries, cities, close_cities = defaultdict(set), defaultdict(set), defaultdict(set)
    country_names = {}
    for r in samples:
        p = places.locate(r['lat'], r['lon'])
        day = int((r['ts'] or 0)//86400)
        if p['code']:
            countries[p['code']].add(day)
            country_names[p['code']] = p['country']
        if p['city']:
            cities[(p['code'], p['city'])].add(day)
            if p['city_distance'] <= 25:
                close_cities[(p['code'], p['city'])].add(day)
    home_code = places.locate(home['lat'], home['lon'])['code'] if home else None
    foreign = {k: v for k, v in countries.items() if k != home_code}
    country = max(foreign or countries, key=lambda k: (len((foreign or countries)[k]), k), default=None)
    city = max((k for k in cities if k[0] == country), key=lambda k: (len(cities[k]), k), default=None)
    far = max((distance(home, r) for r in gps), default=0) if home else 0
    is_trip = far >= 100 and len(away) >= 2 if home else len(gps) >= 2
    name = country_names[country] if country and country != home_code else city[1] if city else None
    if country == home_code and city and len(close_cities[city]) < len(cities[city])/2:
        name = 'Окрестности · ' + city[1]
    return dict(place=name, city=city[1] if city else None, is_trip=is_trip, away=bool(away))


def occasion_name(start, end, settings, away):
    day, title = settings.get('occasion_day'), settings.get('occasion_name')
    if start is None or not day or not title:
        return None
    year = datetime.fromtimestamp(start, timezone.utc).year
    for y in range(year-1, datetime.fromtimestamp(end, timezone.utc).year+2):
        try:
            date = datetime.strptime(f'{y}-{day}', '%Y-%m-%d').replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
        padding = 3*86400 if away else 0
        if start < date + 86400 + padding and end >= date - padding:
            return f'{title} · {y}'
    return None


def build(rows, saved, mode, settings=None):
    settings = settings or {}
    home = infer_home(rows)
    by_path = {r['path']: r for r in rows}
    used = set()
    result = []

    def pack(members, key, name='', manual=False):
        times = [r['ts'] for r in members if r['ts'] is not None and r['ts_source'] != 'mtime']
        start, end = (min(times), max(times)) if times else (None, None)
        multiple_days = start is not None and int(start // 86400) != int(end // 86400)
        description = describe(members, home)
        occasion = occasion_name(start, end, settings, description['away'])
        automatic_name = description['place'] if description['away'] else None
        if mode == 'events' and not description['is_trip'] and automatic_name and not automatic_name.startswith('Окрестности'):
            automatic_name = 'Выезд · ' + automatic_name
        return dict(id=key, name=name or occasion or automatic_name or ('Без даты съёмки' if start is None else
                    'Поездка' if mode == 'trips' and multiple_days else 'Событие'),
                    ids=[r['id'] for r in members], start=start, end=end, manual=manual,
                    gps=sum(distance(r, r) is not None for r in members), **description,
                    occasion=bool(occasion), home_known=home is not None)

    for group in saved:
        members = [by_path[p] for p in group['paths'] if p in by_path and p not in used]
        used.update(r['path'] for r in members)
        if members:
            result.append(pack(members, group['id'], group['name'], True))
    for candidate in automatic(rows, mode, home):
        members = [r for r in candidate if r['path'] not in used]
        if not members:
            continue
        key = 'auto-' + hashlib.sha1('\0'.join(sorted(r['path'] for r in members)).encode()).hexdigest()[:20]
        result.append(pack(members, key))
    return sorted(result, key=lambda g: (g['start'] is not None, g['start'] or 0, g['id']), reverse=True)


def listing(library, mode):
    with closing(library.connect()) as c:
        return build(read_rows(c), read_state(c, mode), mode, preferences(c))


def edit(library, mode, action, group_ids, ids, name, target):
    """Freeze affected groups atomically so splits/moves survive future scans."""
    with closing(library.connect()) as c, c:
        c.execute('BEGIN IMMEDIATE')
        rows = read_rows(c)
        saved = read_state(c, mode)
        settings = preferences(c)
        groups = {g['id']: g for g in build(rows, saved, mode, settings)}
        if not group_ids or any(k not in groups for k in group_ids):
            raise ValueError('Состав событий изменился. Обновите список и повторите.')
        source = groups[group_ids[0]]
        affected = set(group_ids)
        additions = []
        by_id = {r['id']: r['path'] for r in rows}

        def freeze(members, title, key=None):
            key = key if key and not key.startswith('auto-') else uuid.uuid4().hex
            if members:
                additions.append(dict(id=key, name=title, paths=sorted(by_id[i] for i in members)))
            return key

        if action == 'rename':
            key = freeze(source['ids'], name, source['id'])
        elif action == 'merge':
            if len(affected) < 2:
                raise ValueError('Выберите хотя бы два события.')
            key = freeze({i for k in affected for i in groups[k]['ids']}, name)
        elif action in ('split', 'move'):
            selected = set(ids)
            if not selected or not selected <= set(source['ids']):
                raise ValueError('Выберите снимки внутри события.')
            remaining = set(source['ids']) - selected
            if action == 'split' and not remaining:
                raise ValueError('Для разделения оставьте хотя бы один снимок в исходном событии.')
            freeze(remaining, source['name'], source['id'])
            if action == 'move':
                if target not in groups or target == source['id']:
                    raise ValueError('Выберите другое событие.')
                affected.add(target)
                key = freeze(set(groups[target]['ids']) | selected, groups[target]['name'], target)
            else:
                key = freeze(selected, name)
        elif action == 'reset':
            key = None
        else:
            raise ValueError('Неизвестное действие.')
        saved = [g for g in saved if g['id'] not in affected] + additions
        c.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                  ('events:' + mode, json.dumps(saved, ensure_ascii=False)))
        return dict(groups=build(rows, saved, mode, settings), active=key)
