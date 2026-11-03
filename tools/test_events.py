"""Run: python -m unittest discover -s tools -p test_events.py"""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone

from mediaviewer import events, places


class Connection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def photo(i, hour=0, lat=None, lon=None, source='exif'):
    return dict(id=i, path=f'{i}.jpg', ts=1704067200 + hour * 3600 if hour is not None else None,
                ts_source=source, lat=lat, lon=lon)


class FakeLibrary:
    def __init__(self, path):
        self.path = path
        with self.connect() as c:
            c.executescript('''CREATE TABLE media(id INTEGER PRIMARY KEY, path TEXT, ts REAL,
                ts_source TEXT, lat REAL, lon REAL, parent_id INTEGER, analyzed INTEGER DEFAULT 1);
                CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT);''')

    def connect(self):
        c = sqlite3.connect(self.path, factory=Connection)
        c.row_factory = sqlite3.Row
        return c

    def add(self, *rows):
        with self.connect() as c:
            c.executemany('INSERT INTO media(id,path,ts,ts_source,lat,lon) VALUES(:id,:path,:ts,:ts_source,:lat,:lon)', rows)


class GroupingTests(unittest.TestCase):
    def test_time_and_location(self):
        groups = events.automatic([photo(1, 1, 0, 0), photo(2, 2), photo(3, 3, 1, 0), photo(4, 20)], 'events')
        self.assertEqual([[r['id'] for r in g] for g in groups], [[1, 2], [3], [4]])

    def test_overnight_trip_and_long_break(self):
        rows = [photo(1, 20), photo(2, 33), photo(3, 56), photo(4, 300)]
        self.assertEqual(len(events.automatic(rows, 'events')), 4)
        self.assertEqual([len(g) for g in events.automatic(rows, 'trips')], [3, 1])

    def test_long_trip_does_not_split_at_three_weeks(self):
        rows = [photo(i + 1, i * 24, 35.68, 139.76) for i in range(30)]
        self.assertEqual([len(g) for g in events.automatic(rows, 'trips')], [30])
        self.assertIsNone(events.infer_home(rows))

    def test_missing_dates_mtime_and_bad_gps(self):
        groups = events.build([photo(1, None), photo(2, 0, source='mtime'), photo(3, 4)], [], 'events')
        self.assertEqual(groups[-1]['name'], 'Без даты съёмки')
        self.assertEqual(set(groups[-1]['ids']), {1, 2})
        self.assertIsNone(events.distance(photo(1, lat=100, lon=0), photo(2, lat=0, lon=0)))
        self.assertEqual(events.distance(photo(1, lat=0, lon=0), photo(2, lat=0, lon=0)), 0)

    def test_deterministic_and_exhaustive(self):
        rows = [photo(1, 0), photo(2, 1), photo(3, 30), photo(4, None)]
        self.assertEqual(events.build(rows, [], 'events'), events.build(rows[::-1], [], 'events'))
        self.assertEqual(sorted(i for g in events.build(rows, [], 'events') for i in g['ids']), [1, 2, 3, 4])

    def home_rows(self):
        return [photo(100+i, -24*(7*i+1), 54.51, 36.25) for i in range(16)]

    def test_destination_road_no_gps_and_quiet_days(self):
        rows = self.home_rows() + [photo(1, 0, 54.51, 36.25), photo(2, 2),
            photo(3, 10, 52, 38), photo(4, 40, 43.59, 39.72),
            photo(5, 150, 43.59, 39.72), photo(6, 160), photo(7, 175, 50, 38),
            photo(8, 185), photo(9, 190, 54.51, 36.25)]
        trips = [g for g in events.build(rows, [], 'trips') if g['is_trip']]
        self.assertEqual(len(trips), 1)
        self.assertEqual(set(trips[0]['ids']), set(range(2, 9)))
        self.assertEqual(trips[0]['name'], 'Сочи')

    def test_return_home_splits_repeated_destination(self):
        rows = self.home_rows() + [photo(1, 0, 54.51, 36.25), photo(2, 30, 43.59, 39.72),
            photo(3, 50, 43.59, 39.72), photo(4, 80, 54.51, 36.25),
            photo(5, 100, 43.59, 39.72), photo(6, 120, 43.59, 39.72), photo(7, 150, 54.51, 36.25)]
        trips = [g for g in events.build(rows, [], 'trips') if g['is_trip']]
        self.assertEqual([len(g['ids']) for g in trips], [2, 2])

    def test_nearby_overnight_event_and_optional_annual_date(self):
        rows = self.home_rows() + [photo(1, 0, 54.51, 36.25), photo(2, 24, 54.72, 37.18),
            photo(3, 48, 54.72, 37.18), photo(4, 80, 54.51, 36.25)]
        groups = events.build(rows, [], 'events')
        outing = next(g for g in groups if 2 in g['ids'])
        self.assertEqual(outing['ids'], [2, 3])
        self.assertFalse(outing['is_trip'])
        self.assertFalse(outing['occasion'])
        configured = events.build(rows, [], 'events', dict(occasion_day='01-03', occasion_name='Годовщина'))
        self.assertEqual(next(g for g in configured if 2 in g['ids'])['name'], 'Годовщина · 2024')
        self.assertIsNone(events.occasion_name(1704067200, 1704070800, {}, True))

    def test_offline_country_and_city_names(self):
        self.assertEqual(places.locate(35.68, 139.76)['country'], 'Япония')
        self.assertEqual(places.locate(13.75, 100.50)['country'], 'Таиланд')
        self.assertEqual(places.locate(43.59, 39.72)['city'], 'Сочи')

    def test_memorable_date_across_new_year_and_no_personal_default(self):
        start = datetime(2023, 12, 30, tzinfo=timezone.utc).timestamp()
        settings = dict(occasion_day='01-01', occasion_name='Новый год')
        self.assertEqual(events.occasion_name(start, start+3600, settings, True), 'Новый год · 2024')
        self.assertIsNone(events.occasion_name(start, start+3600, settings, False))
        self.assertIsNone(events.occasion_name(start, start+3600, {}, True))


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lib = FakeLibrary(Path(self.tmp.name) / 'events.db')
        self.lib.add(photo(1, 1), photo(2, 2), photo(3, 3), photo(4, 30))

    def tearDown(self):
        self.tmp.cleanup()

    def groups(self, mode='events'):
        return events.listing(self.lib, mode)

    def edit(self, action, groups, ids=(), name='Наш отпуск', target=None):
        return events.edit(self.lib, 'events', action, groups, ids, name, target)

    def test_split_rename_rescan_move_merge_reset(self):
        original = next(g for g in self.groups() if 1 in g['ids'])
        result = self.edit('split', [original['id']], [2, 3])
        new = result['active']
        self.assertEqual(len(self.groups()), 3)
        self.edit('rename', [new], name='Поездка <лето>')
        with self.lib.connect() as c:
            c.execute('UPDATE media SET id=22, ts=ts+100000 WHERE path="2.jpg"')
        renamed = next(g for g in self.groups() if g['id'] == new)
        self.assertEqual(renamed['name'], 'Поездка <лето>')
        self.assertEqual(set(renamed['ids']), {22, 3})
        other = next(g for g in self.groups() if 4 in g['ids'])
        moved = self.edit('move', [new], [3], target=other['id'])['active']
        self.assertEqual(set(next(g for g in self.groups() if g['id'] == moved)['ids']), {3, 4})
        merged = self.edit('merge', [g['id'] for g in self.groups()])['active']
        self.assertEqual(len(self.groups()), 1)
        self.assertEqual(set(self.groups()[0]['ids']), {1, 22, 3, 4})
        self.edit('reset', [merged])
        self.assertTrue(all(not g['manual'] for g in self.groups()))

    def test_stale_request_and_atomic_validation(self):
        original = next(g for g in self.groups() if 1 in g['ids'])
        with self.assertRaises(ValueError):
            self.edit('move', [original['id']], [1], target='missing')
        self.assertEqual(next(g for g in self.groups() if 1 in g['ids']), original)
        self.lib.add(photo(5, 4))
        with self.assertRaises(ValueError):
            self.edit('rename', [original['id']])

    def test_new_files_mode_isolation_and_deleted_members(self):
        original = next(g for g in self.groups() if 1 in g['ids'])
        key = self.edit('rename', [original['id']])['active']
        self.lib.add(photo(5, 4))
        self.assertEqual(set(next(g for g in self.groups() if g['id'] == key)['ids']), {1, 2, 3})
        self.assertTrue(all(not g['manual'] for g in self.groups('trips')))
        with self.lib.connect() as c:
            c.execute('DELETE FROM media WHERE id IN (1,2,3)')
        self.assertFalse(any(g['id'] == key for g in self.groups()))

    def test_preferences_persist_and_manual_names_win(self):
        with self.lib.connect() as c:
            self.assertEqual(events.preferences(c)['occasion_day'], '')
        values = dict(min_count=20, occasion_day='01-01', occasion_name='Праздник')
        events.save_preferences(self.lib, values)
        with self.lib.connect() as c:
            self.assertEqual(events.preferences(c), values)
        original = next(g for g in self.groups() if 1 in g['ids'])
        self.assertEqual(original['name'], 'Праздник · 2024')
        key = self.edit('rename', [original['id']], name='Наше событие')['active']
        self.assertEqual(next(g for g in self.groups() if g['id'] == key)['name'], 'Наше событие')


if __name__ == '__main__':
    unittest.main()
