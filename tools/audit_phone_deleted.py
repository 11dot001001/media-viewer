"""Read-only phone search using fingerprints retained after local deletion."""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import sqlite3
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mediaviewer.phonefind import PhoneFinder, MONTH_DIR
from mediaviewer.phone import WpdDevice


class JournalSource:
    def __init__(self, path):
        self.path = path
        self.status = {"version": 0}

    def thumb(self, mid):
        with sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True) as c:
            row = c.execute('SELECT thumb FROM deleted WHERE id=?', (mid,)).fetchone()
            return row[0] if row else None


class JournalFinder(PhoneFinder):
    def __init__(self, journal, rows, data):
        super().__init__(JournalSource(journal), data, wide=True)
        self.rows = rows
        self.last_progress = 0

    def _set(self, **kw):
        super()._set(**kw)
        now = time.monotonic()
        if now - self.last_progress >= 10:
            print(json.dumps(self.status, ensure_ascii=False), flush=True)
            self.last_progress = now

    def _items(self):
        return self.rows

    def _months(self):
        months = set()
        for r in self.rows:
            if r['ts'] is not None and r['ts_source'] != 'mtime':
                date = dt.datetime(1970, 1, 1) + dt.timedelta(seconds=r['ts'])
                months.update((date + dt.timedelta(days=d)).strftime('%Y%m') for d in (-1, 0, 1))
        return months

    def search(self):
        devices = WpdDevice.list_devices()
        if len(devices) != 1:
            raise RuntimeError('Expected exactly one connected phone')
        d = devices[0]
        self.device = WpdDevice(d['path'], d['name'], d.get('serial'))
        months = self._months()
        self._set(device=self.device.name, scope='months', months=sorted(months), wide=False)
        # Enumerate the relevant months completely: do not assume chronological ordering.
        def include(rel):
            match = MONTH_DIR.match(rel.rsplit('/', 1)[-1])
            return not match or match.group(1) in months
        files = self.device.list_files(
            progress=lambda n, total: self._set(phase='list', done=n, total=total),
            folder_filter=include)
        by_size = {}
        for f in files:
            by_size.setdefault(f[1], []).append(f)
        acc = self._acc()
        self._verify(self.rows, lambda r: by_size.get(r['size'], []), lambda _: [], acc)
        self._publish(self.rows, acc)
        self._set(phase='done', message='Сверка завершена')
        return len(files)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', required=True, help='Filename date prefix, e.g. 2026-03-29')
    parser.add_argument('--data', type=Path, default=Path(__file__).resolve().parents[1] / 'data')
    args = parser.parse_args()
    dt.date.fromisoformat(args.date)
    journal = args.data / 'journal.db'
    with sqlite3.connect(journal.resolve().as_uri() + '?mode=ro', uri=True) as c:
        c.row_factory = sqlite3.Row
        rows = [dict(r) for r in c.execute('SELECT id,name,path,source,size,ts,ts_source,sha1 FROM deleted WHERE name LIKE ? ORDER BY id DESC', (args.date + '%',))]
    unique = {}
    for r in rows:
        if r['sha1']:
            unique.setdefault(r['sha1'], r)
    finder = JournalFinder(journal, list(unique.values()), args.data)
    count = finder.search()
    results = []
    for r in unique.values():
        mid = r['id']
        status = 'exact_match' if finder.found.get(mid) else ('read_error' if mid in finder.unreadable else 'no_exact_match')
        results.append(dict(r, result=status, phone_path=finder.found.get(mid)))
    report = {'date': args.date, 'files_enumerated': count, 'status': finder.status,
              'without_fingerprint': [r for r in rows if not r['sha1']], 'results': results}
    output = args.data / ('phone-audit-' + args.date + '.json')
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'report': str(output), 'files_enumerated': count, **finder.status}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
