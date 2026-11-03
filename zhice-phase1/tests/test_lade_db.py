import csv
from contextlib import closing
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest

from zhice.lade_db import build_database, md_day, normalized, time_seconds


def fixture_entry(root, kind, rows):
    columns = ['order_id', 'courier_id', 'accept_time', 'ds',
               'pickup_time', 'time_window_start', 'time_window_end'] if kind == 'pickup' else [
                   'order_id', 'courier_id', 'accept_time', 'ds', 'delivery_time']
    name = f'{kind}/{kind}_jl.csv'; path = root / name; path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=columns); w.writeheader(); w.writerows(rows)
    return {'path': name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'bytes': path.stat().st_size,
            'csv': {'rows': len(rows), 'delimiter': 'comma'}}


def pickup_row(key='A', accept='09:00:00', finish='09:30:00', end='09:20:00'):
    return {'order_id': key, 'courier_id': 'C', 'accept_time': '05-01 ' + accept, 'ds': '0501',
            'pickup_time': '05-01 ' + finish, 'time_window_start': '05-01 09:00:00',
            'time_window_end': '05-01 ' + end}


class LaDeDatabaseTests(unittest.TestCase):
    def test_year_free_clock_boundaries_and_invalid_values(self):
        self.assertEqual(time_seconds('05-01 00:00:00'), md_day('05-01'))
        self.assertEqual(time_seconds('05-02 00:00:00') - time_seconds('05-01 23:59:59'), 1)
        self.assertIsNotNone(time_seconds('02-29 12:00:00'))
        for value in ('04-31 00:00:00', '05-01 24:00:00', '2026-05-01 10:00:00', '', 'nan'):
            self.assertIsNone(time_seconds(value))

    def test_future_outcome_is_label_not_a_validity_rewrite(self):
        a = normalized(1, 'pickup', 'jl', pickup_row())
        b = normalized(1, 'pickup', 'jl', pickup_row(finish='09:10:00'))
        self.assertEqual(a[8], b[8])
        self.assertEqual(a[10:12], b[10:12])
        self.assertEqual(a[-1], 1); self.assertEqual(b[-1], 0)

    def test_import_preserves_duplicates_and_flags_quality_without_losing_rows(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); raw = root / 'raw'; raw.mkdir()
            pickup = [pickup_row(), pickup_row('B', '09:10:00', '09:15:00', '10:00:00'),
                      pickup_row('D'), pickup_row('D', finish='09:31:00'), pickup_row('BAD', finish='08:59:00')]
            delivery = [{'order_id': 'Z', 'courier_id': 'C', 'accept_time': '05-01 09:00:00',
                         'delivery_time': '05-01 10:00:00', 'ds': '0501'}]
            entries = [fixture_entry(raw, 'delivery', delivery), fixture_entry(raw, 'pickup', pickup)]
            db = root / 'warehouse.sqlite'; result = build_database(raw, entries, db, 'fixture')
            self.assertEqual(result['status'], 'passed')
            self.assertEqual(result['duplicate_key_groups'], 1)
            with closing(sqlite3.connect(db)) as con:
                self.assertEqual(con.execute('SELECT COUNT(*) FROM tasks').fetchone()[0], 6)
                self.assertEqual(con.execute('SELECT COUNT(*) FROM raw_pickup').fetchone()[0], 5)
                self.assertEqual(con.execute('SELECT SUM(duplicate_key) FROM tasks').fetchone()[0], 2)
                self.assertEqual(con.execute("SELECT late_pickup FROM tasks WHERE external_id='A'").fetchone()[0], 1)
                self.assertIsNone(con.execute("SELECT late_pickup FROM tasks WHERE external_id='Z'").fetchone()[0])
                self.assertEqual(con.execute("SELECT quality_flags & 16 FROM tasks WHERE external_id='BAD'").fetchone()[0], 16)
                self.assertEqual(con.execute("SELECT valid_tasks FROM scope_catalog WHERE kind='pickup'").fetchone()[0], 2)


if __name__ == '__main__':
    unittest.main()
