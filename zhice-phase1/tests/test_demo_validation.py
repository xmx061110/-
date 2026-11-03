import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import pandas as pd

from test_metrics import fixtures
from zhice.demo_validation import compare_results,verify_all_groups,verify_selection
from zhice.pipeline import build_order_fact


class DemoValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp_root=Path(__file__).resolve().parents[1]/'work'
        self.temp_root.mkdir(exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=self.temp_root)
        self.db=Path(self.tmp.name)/'demo.sqlite'
        data=fixtures()
        products=pd.DataFrame([('p1','cat1'),('p2','cat2')],columns=['product_id','product_category_name'])
        items=data['order_items'].copy()
        items['price']=items.price_cents.map(lambda x:f'{x//100}.{x%100:02d}')
        items['freight_value']='1.00'
        raw_items=items[['order_id','order_item_id','product_id','seller_id','shipping_limit_date','price','freight_value']]
        payments=data['order_payments'].copy()
        payments['payment_type']='credit_card';payments['payment_installments']=2
        payments['payment_value']=payments.payment_value_cents.map(lambda x:f'{x//100}.{x%100:02d}')
        raw_payments=payments[['order_id','payment_sequential','payment_type','payment_installments','payment_value']]
        with closing(sqlite3.connect(self.db)) as con:
            build_order_fact(data).to_sql('fact_orders',con,index=False)
            data['order_items'].to_sql('clean_order_items',con,index=False)
            products.to_sql('clean_products',con,index=False)
            data['orders'].to_sql('raw_orders',con,index=False)
            raw_items.to_sql('raw_order_items',con,index=False)
            products.to_sql('raw_products',con,index=False)
            pd.concat([raw_payments,raw_payments.iloc[:1]]).to_sql('raw_order_payments',con,index=False)
            data['order_reviews'].to_sql('raw_order_reviews',con,index=False)

    def tearDown(self):
        assert Path(self.tmp.name).resolve().is_relative_to(self.temp_root.resolve())
        self.tmp.cleanup()

    def execute(self,sql):
        with closing(sqlite3.connect(self.db)) as con:
            con.execute(sql);con.commit()

    def test_two_paths_match_and_duplicate_payment_not_counted(self):
        result=verify_selection(self.db)
        self.assertEqual(result['status'],'passed')
        self.assertEqual(result['program']['sales_gmv_cents'],10000)
        self.assertEqual(result['reference']['delivered_observed_payment_cents'],6300)

    def test_empty_intersection_is_verified_zero(self):
        result=verify_selection(self.db,category='cat2',seller='s1')
        self.assertEqual(result['status'],'passed');self.assertTrue(result['empty'])
        self.assertIsNone(result['program']['low_score_rate'])

    def test_month_filter(self):
        self.assertEqual(verify_selection(self.db,month='2018-01')['program']['order_count'],5)
        self.assertTrue(verify_selection(self.db,month='2018-02')['empty'])

    def test_no_review_count_zero_but_rate_null(self):
        self.execute('UPDATE fact_orders SET review_score=NULL')
        self.execute('DELETE FROM raw_order_reviews')
        result=verify_selection(self.db,seller='s2')
        self.assertEqual(result['status'],'passed')
        self.assertEqual(result['program']['reviewed_delivered_order_count'],0)
        self.assertIsNone(result['program']['low_score_rate'])

    def test_changed_review_in_fact_really_fails(self):
        self.execute("UPDATE fact_orders SET review_score=1 WHERE order_id='A'")
        result=verify_selection(self.db)
        self.assertEqual(result['status'],'failed')
        self.assertTrue(any(not row['passed'] and row['key']=='low_score_order_count' for row in result['checks']))

    def test_one_cent_changed_really_fails(self):
        self.execute("UPDATE clean_order_items SET price_cents=price_cents+1 WHERE order_id='C'")
        result=verify_selection(self.db)
        self.assertEqual(result['status'],'failed')
        self.assertTrue(any(not row['passed'] and row['key']=='sales_gmv_cents' for row in result['checks']))

    def test_sql_is_standalone_without_custom_functions(self):
        result=verify_selection(self.db,month='2018-01',category='cat1',seller='s1')
        with closing(sqlite3.connect(self.db)) as con:
            con.row_factory=sqlite3.Row
            a=dict(con.execute(result['sql']['program']).fetchone())
            b=dict(con.execute(result['sql']['reference']).fetchone())
        self.assertTrue(all(r['passed'] for r in compare_results(a,b)))

    def test_ambiguous_review_tie_blocks_pass(self):
        self.execute("INSERT INTO raw_order_reviews SELECT review_id,order_id,2,review_comment_title,review_comment_message,review_creation_date,review_answer_timestamp,99 FROM raw_order_reviews WHERE review_id='ra2'")
        result=verify_selection(self.db)
        self.assertEqual(result['ambiguous_review_tie_groups'],1)
        self.assertEqual(result['status'],'failed')

    def test_all_groups_checked(self):
        result=verify_all_groups(self.db)
        self.assertEqual(result['status'],'passed')
        self.assertEqual(len(result['details']),4)

    def test_float_tolerance_does_not_hide_wrong_rates(self):
        self.assertTrue(next(r for r in compare_results({'low_score_rate':0.1},{'low_score_rate':0.1+1e-13}) if r['key']=='low_score_rate')['passed'])
        self.assertFalse(next(r for r in compare_results({'low_score_rate':0.1},{'low_score_rate':0.2}) if r['key']=='low_score_rate')['passed'])
