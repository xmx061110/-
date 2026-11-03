import unittest
import numpy as np
import pandas as pd
from zhice.risk_baseline import NUMERIC,CATEGORICAL,encode,metrics

class RiskTests(unittest.TestCase):
    def test_future_fields_excluded(self):
        forbidden={'order_delivered_customer_date','order_delivered_carrier_date','review_score','order_status','delivery_late'}
        self.assertFalse(forbidden.intersection(NUMERIC+CATEGORICAL))

    def test_rank_metrics(self):
        frame=pd.DataFrame({'delivery_late':[0,0,1,1]})
        result=metrics(frame,[.1,.2,.8,.9])
        self.assertEqual(result['roc_auc'],1.0)
        self.assertEqual(result['average_precision'],1.0)
        self.assertEqual(result['top10_late_count'],1)

    def test_unseen_category_is_all_zero_and_does_not_use_test_categories(self):
        def row(cat):
            return {**{k:1 for k in NUMERIC},**{k:cat for k in CATEGORICAL}}
        train=pd.DataFrame([row('a'),row('a')]);test=pd.DataFrame([row('future')])
        (_,x),encoding=encode(train,[train,test])
        self.assertEqual(encoding['categories']['category'],['a'])
        self.assertTrue(np.all(x[0,-3:]==0))

if __name__=='__main__':unittest.main()
