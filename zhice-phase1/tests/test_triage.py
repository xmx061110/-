import unittest
from pathlib import Path
from zhice.triage import build_triage

DB=Path(__file__).resolve().parents[1]/'data/warehouse.sqlite'

@unittest.skipUnless(DB.exists(),'需要真实数据库')
class TriageTests(unittest.TestCase):
    def test_priority_is_verified_and_sample_floor_applies(self):
        r=build_triage(DB,'2018-01',min_orders=30,limit=5)
        self.assertEqual(r['status'],'passed')
        self.assertGreater(r['checked_sellers'],0)
        self.assertLessEqual(len(r['priority']),5)
        keys=[]
        for item in r['priority']:
            m=item['metrics'];self.assertGreaterEqual(m['delivery_eligible_order_count'],30)
            self.assertFalse(item['causal_claim'])
            keys.append((-m['late_order_count'],-m['delivery_late_rate'],item['seller']))
        self.assertEqual(keys,sorted(keys))
        self.assertIn('raw_',r['sql']['reference'])

    def test_empty_scope_is_not_a_successful_intervention(self):
        r=build_triage(DB,'2099-01')
        self.assertEqual(r['priority'],[])
        self.assertEqual(r['scope_metrics']['order_count'],0)

if __name__=='__main__':unittest.main()
