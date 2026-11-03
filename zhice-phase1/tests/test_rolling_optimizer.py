from copy import deepcopy
import itertools
import unittest

from zhice.rolling_optimizer import POLICIES, compare, fingerprint, make_stream, run_policy
from zhice.rolling_verification import verify_comparison


def observation(key, priority, *, step=1, occurred=None, status='exception', eid=None):
    return {'event_id': eid or f'{step}-{key}', 'order_id': key, 'step': step,
            'occurred_step': step if occurred is None else occurred,
            'status': status, 'priority_units': priority}


class RollingTests(unittest.TestCase):
    def stream(self, events=(), steps=2):
        return {'items': [{'order_id': key, 'cost_cents': cost, 'priority_units': value}
                          for key, cost, value in [('A', 1000, 100), ('B', 1000, 90), ('C', 1000, 80)]],
                'budget_cents': 2000, 'steps': steps, 'events': list(events)}

    def test_immediate_reacts_while_static_keeps_initial_remaining_plan(self):
        stream = self.stream([observation('C', 200)])
        self.assertEqual([x['order_id'] for x in run_policy(stream, 'static')['actions']], ['A', 'B'])
        self.assertEqual([x['order_id'] for x in run_policy(stream, 'immediate')['actions']], ['A', 'C'])

    def test_stable_hysteresis_rejects_small_gain_but_accepts_large_gain(self):
        params = {'switch_penalty_units': 2, 'min_gain_bps': 500, 'cooldown_steps': 1}
        small = run_policy(self.stream([observation('C', 94)]), 'stable', **params)
        big = run_policy(self.stream([observation('C', 200)]), 'stable', **params)
        self.assertEqual(small['actions'][-1]['order_id'], 'B')
        self.assertEqual(small['rejected_replans'], 1)
        self.assertEqual(big['actions'][-1]['order_id'], 'C')

    def test_late_observation_does_not_overwrite_more_recent_delivery(self):
        stream = self.stream([observation('B', 90, step=1, status='delivered'),
                              observation('B', 999, step=2, occurred=1, eid='older')], steps=3)
        for policy in POLICIES:
            r = run_policy(stream, policy)
            self.assertNotIn('B', [x['order_id'] for x in r['actions']])
            self.assertEqual(r['stale_events_ignored'], 1)

    def test_future_received_observation_cannot_change_past_actions(self):
        base = self.stream(steps=2)
        future = deepcopy(base); future['events'] = [observation('C', 999999, step=3)]
        for policy in POLICIES:
            self.assertEqual(run_policy(base, policy)['actions'], run_policy(future, policy)['actions'])

    def test_all_policies_use_same_stream_and_do_not_mutate_it(self):
        stream = make_stream(self.stream()['items'], 2000, 42, steps=5)
        saved = deepcopy(stream); r = compare(stream)
        self.assertEqual(stream, saved)
        self.assertEqual({x['stream_sha256'] for x in r['results']}, {fingerprint(stream)})

    def test_per_step_optimum_is_independently_checkable(self):
        stream = self.stream([observation('C', 200)])
        result = run_policy(stream, 'immediate')
        # 第二轮剩1000分，B=90、C=200，穷举可行子集而非调用同一个求解器。
        remaining = [('B', 1000, 90), ('C', 1000, 200)]
        best = max(sum(x[2] for x in subset) for n in range(3)
                   for subset in itertools.combinations(remaining, n)
                   if sum(x[1] for x in subset) <= 1000)
        self.assertEqual(result['actions'][1]['priority_units'], best)

    def test_many_streams_never_overspend_repeat_or_execute_terminal_order(self):
        items = [{'order_id': str(i), 'cost_cents': (i % 4 + 1) * 100,
                  'priority_units': (i + 1) * 100} for i in range(12)]
        for seed in range(20):
            stream = make_stream(items, 1400, seed, steps=10)
            for policy in POLICIES:
                r = run_policy(stream, policy)
                ids = [x['order_id'] for x in r['actions']]
                self.assertEqual(len(ids), len(set(ids)))
                self.assertEqual(sum(x['cost_cents'] for x in r['actions']), r['spent_cents'])
                self.assertLessEqual(r['spent_cents'], 1400)
                for action in r['actions']:
                    visible = [e for e in stream['events'] if e['order_id'] == action['order_id'] and e['step'] <= action['step']]
                    if visible:
                        latest = max(visible, key=lambda e: e['occurred_step'])
                        self.assertNotIn(latest['status'], {'delivered', 'canceled'})

    def test_duplicate_event_idempotence_and_conflict(self):
        e = observation('C', 200)
        a = run_policy(self.stream([e]), 'immediate')
        b = run_policy(self.stream([e, deepcopy(e)]), 'immediate')
        self.assertEqual(a['actions'], b['actions'])
        bad = dict(e, priority_units=300)
        with self.assertRaises(ValueError): run_policy(self.stream([e, bad]), 'immediate')

    def test_budget_zero_empty_queue_and_invalid_parameters(self):
        stream = self.stream(); stream['budget_cents'] = 0
        for policy in POLICIES:
            self.assertEqual(run_policy(stream, policy)['actions'], [])
        with self.assertRaises(ValueError): run_policy(stream, 'stable', min_gain_bps=True)
        with self.assertRaises(ValueError): run_policy(stream, 'unknown')

    def test_live_independent_verifier_checks_and_rejects_tampering(self):
        stream = self.stream([observation('C', 200)])
        result = compare(stream)
        self.assertEqual(verify_comparison(stream, result)['status'], 'passed')
        for field in ('cost_cents', 'priority_units'):
            bad = deepcopy(result)
            bad['results'][0]['trace'][0]['executed'][field] += 1
            with self.subTest(field=field), self.assertRaises(ValueError):
                verify_comparison(stream, bad)
        bad = deepcopy(result); bad['results'][0]['spent_cents'] += 1
        with self.assertRaises(ValueError): verify_comparison(stream, bad)

    def test_live_verifier_rejects_early_future_score_and_missing_policy(self):
        stream = self.stream([observation('A', 999, step=2)], steps=2)
        result = compare(stream)
        bad = deepcopy(result)
        bad['results'][0]['trace'][0]['executed']['priority_units'] = 999
        with self.assertRaises(ValueError): verify_comparison(stream, bad)
        bad = deepcopy(result); bad['results'].pop()
        with self.assertRaises(ValueError): verify_comparison(stream, bad)

    def test_future_tasks_are_never_available_before_real_release(self):
        stream = self.stream(steps=3)
        stream['items'][0]['available_step'] = 2
        stream['items'][0]['priority_units'] = 999999
        for policy in POLICIES:
            r = run_policy(stream, policy)
            self.assertTrue(all(x['step'] >= 2 for x in r['actions'] if x['order_id'] == 'A'))
        verify_comparison(stream, compare(stream))
        stream['events'] = [observation('A', 999999, step=1)]
        with self.assertRaises(ValueError): run_policy(stream, 'immediate')

    def test_real_pickup_completion_is_terminal(self):
        stream = self.stream([observation('B', 0, status='completed')])
        for policy in POLICIES:
            self.assertNotIn('B', [x['order_id'] for x in run_policy(stream, policy)['actions']])
        verify_comparison(stream, compare(stream))


if __name__ == '__main__':
    unittest.main()
