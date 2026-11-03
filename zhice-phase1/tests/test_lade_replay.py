from contextlib import closing
from copy import deepcopy
from pathlib import Path
import sqlite3
import tempfile
import unittest

from test_lade_db import fixture_entry, pickup_row
from zhice.lade_db import build_database, time_seconds
from zhice.lade_query import verified_metrics
from zhice.lade_replay import make_real_stream, scenario, verify_raw_replay
from zhice.rolling_optimizer import compare


class LaDeReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        raw = self.root / 'raw'; raw.mkdir()
        a = pickup_row(); b = pickup_row('B', '09:10:00', '09:15:00', '10:00:00')
        other = pickup_row('OTHER', '09:30:00', '09:40:00', '10:00:00'); other['courier_id'] = 'OTHER'
        entry = fixture_entry(raw, 'pickup', [a, b, other])
        self.db = self.root / 'warehouse.sqlite'
        build_database(raw, [entry], self.db, 'fixture')

    def tearDown(self):
        self.temp.cleanup()

    def test_raw_sql_reproduces_metrics_and_denominator(self):
        r = verified_metrics(self.db, 'pickup', 'jl', '05-01', 'C')
        self.assertEqual(r['metrics']['eligible'], 2)
        self.assertEqual(r['metrics']['pickup_late_count'], 1)
        self.assertEqual(r['metrics']['pickup_late_rate'], .5)
        self.assertIn('raw_pickup', r['sql']['raw_reference'])
        with closing(sqlite3.connect(self.db)) as con:
            con.row_factory=sqlite3.Row
            self.assertEqual(dict(con.execute(r['sql']['standalone_raw_reference']).fetchone()),r['reference_counts'])

    def test_real_cohort_release_and_completed_task_constraints(self):
        r = scenario(self.db, 'jl', '05-01', 'C', 50, '09:00', 60)
        self.assertEqual(r['verification']['raw_source_check'], 'passed')
        self.assertEqual(r['candidate_count'], 2)
        second = r['input']['items'][1]
        self.assertEqual(second['available_step'], 2)
        for result in r['results']:
            for trace in result['trace']:
                if trace['step'] < 2:
                    self.assertNotIn(second['order_id'], trace['planned_ids_before_execution'])

    def test_normalized_data_tampering_is_caught_by_raw_sql(self):
        with closing(sqlite3.connect(self.db)) as con:
            con.execute("UPDATE tasks SET window_end_s=window_end_s+3600 WHERE external_id='A'")
            con.commit()
        with self.assertRaises(ValueError): verified_metrics(self.db, 'pickup', 'jl', '05-01', 'C')

    def test_raw_source_verifier_rejects_fake_source_identity(self):
        r = scenario(self.db, 'jl', '05-01', 'C', 50, '09:00', 60)
        stream = deepcopy(r['input']); stream['items'][0]['source_task_id'] = 3
        with self.assertRaises(ValueError): verify_raw_replay(self.db, stream, compare(stream))

    def test_future_completion_change_cannot_change_earlier_actions(self):
        with closing(sqlite3.connect(self.db)) as con:
            con.row_factory = sqlite3.Row
            rows = [dict(row) for row in con.execute("SELECT * FROM tasks WHERE courier_id='C' ORDER BY task_id")]
        changed = deepcopy(rows); changed[0]['finish_s'] = time_seconds('05-01 09:45:00')
        start = time_seconds('05-01 09:00:00')
        old = compare(make_real_stream(rows, start, 13, 50, 'fixture'))
        new = compare(make_real_stream(changed, start, 13, 50, 'fixture'))
        for a, b in zip(old['results'], new['results']):
            self.assertEqual([x for x in a['actions'] if x['step'] < 6], [x for x in b['actions'] if x['step'] < 6])

    def test_zero_budget_no_candidates_and_invalid_parameters(self):
        r = scenario(self.db, 'jl', '05-01', 'C', 0, '09:00', 60)
        self.assertTrue(all(x['executed_count'] == 0 for x in r['results']))
        r = scenario(self.db, 'jl', '05-01', 'C', 50, '11:00', 60)
        self.assertEqual(r['status'], 'no_candidates')
        for kwargs in ({'budget_points': True}, {'duration_minutes': 7}, {'start_time': '25:00'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                scenario(self.db, 'jl', '05-01', 'C', **kwargs)


if __name__ == '__main__':
    unittest.main()
