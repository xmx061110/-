import itertools
import random
import unittest
import csv
import tempfile
from pathlib import Path
from zhice.budget_optimizer import solve,greedy,simulated_candidates

class BudgetTests(unittest.TestCase):
    def test_exact_against_exhaustive_and_unpruned(self):
        rng=random.Random(20261101)
        for _ in range(80):
            items=[{'order_id':str(i),'cost_cents':rng.randint(1,12)*100,'priority_units':rng.randint(0,100)} for i in range(10)]
            budget=rng.randint(0,35)*100+37
            oracle=max(sum(x['priority_units'] for x in subset) for n in range(11) for subset in itertools.combinations(items,n) if sum(x['cost_cents'] for x in subset)<=budget)
            r=solve(items,budget);plain=solve(items,budget,prune=False)
            self.assertEqual(r['priority_units'],oracle);self.assertEqual(r['priority_units'],plain['priority_units'])
            self.assertEqual(len(r['selected_ids']),len(set(r['selected_ids'])))
            self.assertLessEqual(r['cost_cents'],budget)

    def test_density_greedy_can_miss_better_combination(self):
        items=[{'order_id':'a','cost_cents':10,'priority_units':60,'risk_score':.9},
               {'order_id':'b','cost_cents':20,'priority_units':100,'risk_score':.8},
               {'order_id':'c','cost_cents':30,'priority_units':120,'risk_score':.7}]
        self.assertEqual(solve(items,50)['priority_units'],220)
        self.assertEqual(greedy(items,50,'density')['priority_units'],160)

    def test_empty_and_ties(self):
        self.assertEqual(solve([],0)['selected_ids'],[])
        items=[{'order_id':'a','cost_cents':100,'priority_units':1},{'order_id':'b','cost_cents':200,'priority_units':1}]
        self.assertEqual(solve(items,200)['selected_ids'],['a'])

    def test_invalid_or_repeated_input_rejected(self):
        for items,b in [([],True),([], -1),([{'order_id':'a','cost_cents':0,'priority_units':1}],100),
                        ([{'order_id':'a','cost_cents':1,'priority_units':1}]*2,100)]:
            with self.assertRaises(ValueError):solve(items,b)

    def test_future_outcome_cannot_change_selection_input(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'pred.csv'
            def write(label):
                with p.open('w',newline='',encoding='utf8') as f:
                    w=csv.DictWriter(f,fieldnames=['order_id','order_purchase_timestamp','predicted_late_probability','delivery_late'])
                    w.writeheader();w.writerow({'order_id':'a','order_purchase_timestamp':'2018-07-01 10:00:00','predicted_late_probability':'0.2','delivery_late':label})
            write(0);a=simulated_candidates(p,'2018-07-01')
            write(1);b=simulated_candidates(p,'2018-07-01')
            self.assertEqual(a,b)

if __name__=='__main__':unittest.main()
