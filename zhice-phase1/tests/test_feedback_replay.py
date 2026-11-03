from copy import deepcopy
import unittest

from zhice.feedback_replay import demo, replay


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        self.data = demo()['input']
        self.cutoff = '2026-11-01T10:05:00+08:00'

    def run_case(self, events=None, cutoff=None):
        return replay(self.data['items'], 5000,
                      self.data['events'] if events is None else events,
                      cutoff or self.cutoff)

    def test_feedback_changes_plan_and_locks_failed_spending(self):
        r = self.run_case()
        self.assertEqual(r['initial_plan']['selected_ids'], ['B', 'C'])
        self.assertEqual(r['trace'][0]['selected_ids'], ['A', 'B'])
        self.assertEqual(r['final_plan']['selected_ids'], ['C'])
        for step in r['trace']:
            self.assertLessEqual(step['spent_cents'] + step['planned_cents'], 5000)
        self.assertEqual(r['final_plan']['spent_cents'], 1000)
        self.assertEqual(r['action_outcomes'], {'A': 'failure'})

    def test_no_future_received_event_leaks_into_earlier_plan(self):
        r = self.run_case(cutoff='2026-11-01T10:00:00+08:00')
        self.assertEqual(r['initial_plan'], r['final_plan'])
        self.assertEqual(len(r['ignored_events']), 4)

    def test_duplicate_event_is_idempotent_and_input_order_does_not_matter(self):
        events = self.data['events']
        r = self.run_case(list(reversed(events)) + [deepcopy(events[1])])
        self.assertEqual(r['final_plan'], self.run_case()['final_plan'])
        self.assertEqual(r['committed_costs_cents'], {'A': 1000})

    def test_conflicting_duplicate_is_rejected(self):
        e = deepcopy(self.data['events'][0]); e['priority_units'] = 1000
        with self.assertRaises(ValueError):
            self.run_case(self.data['events'] + [e])

    def test_late_old_observation_cannot_reopen_delivered_order(self):
        e = deepcopy(self.data['events'][2])
        e.update(event_id='stale', occurred_at='2026-11-01T10:02:00+08:00',
                 received_at=self.cutoff, status='exception', priority_units=999)
        r = self.run_case(self.data['events'] + [e])
        self.assertEqual(r['order_states']['B']['status'], 'delivered')
        self.assertEqual(r['final_plan']['selected_ids'], ['C'])

    def test_repeat_commit_and_unknown_order_are_rejected(self):
        for extra in ({'type': 'commit', 'order_id': 'A'},
                      {'type': 'commit', 'order_id': 'unknown'},
                      {'type': 'observation', 'order_id': 'B', 'status': 'pending'}):
            e = dict(self.data['events'][3], event_id='invalid',
                     occurred_at=self.cutoff, received_at=self.cutoff)
            e.pop('outcome'); e.update(extra)
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.run_case(self.data['events'] + [e])

    def test_timezone_and_event_clock_validation(self):
        with self.assertRaises(ValueError):
            self.run_case(cutoff='2026-11-01T10:05:00')
        e = deepcopy(self.data['events'][0]); e['occurred_at'] = self.cutoff
        with self.assertRaises(ValueError):
            self.run_case([e])

    def test_function_does_not_modify_inputs(self):
        before = deepcopy(self.data)
        self.run_case()
        self.assertEqual(self.data, before)


if __name__ == '__main__':
    unittest.main()
