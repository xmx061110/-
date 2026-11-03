import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import pandas as pd

from zhice.metrics import get_metrics
from zhice.pipeline import build_order_fact, canonical_reviews, clean_table, money_cents
from zhice.schema import SCHEMA


def fixtures():
    orders=[]
    for oid,status in [("A","delivered"),("B","canceled"),("C","delivered"),("D","processing"),("E","delivered")]:
        row={c:None for c in SCHEMA["orders"]}
        row.update(order_id=oid,customer_id=oid,order_status=status,order_purchase_timestamp="2018-01-01 10:00:00",
                   order_delivered_carrier_date="2018-01-02 10:00:00",order_delivered_customer_date="2018-01-05 23:59:00",
                   order_estimated_delivery_date="2018-01-05 00:00:00")
        orders.append(row)
    orders[2]["order_delivered_customer_date"]="2018-01-06 00:00:01"
    orders[4]["order_delivered_customer_date"]=None
    item_rows=[("A",1,"p1","s1",1000), ("A",2,"p2","s2",2000), ("B",1,"p1","s1",9000),
               ("C",1,"p1","s1",3000),("E",1,"p1","s1",4000)]
    items=pd.DataFrame(item_rows,columns=["order_id","order_item_id","product_id","seller_id","price_cents"])
    items["freight_value_cents"]=100;items["shipping_limit_date"]="2018-01-02 00:00:00"
    payments=pd.DataFrame([("A",1,1000),("A",2,2200),("B",1,9100),("C",1,3100)],columns=["order_id","payment_sequential","payment_value_cents"])
    reviews=[]
    for source,(oid,rid,score,answer) in enumerate([("A","ra",1,"2018-01-07 00:00:00"),("A","ra2",5,"2018-01-08 00:00:00"),
                                                ("C","rc",2,"2018-01-07 00:00:00"),("B","rb",1,"2018-01-07 00:00:00")]):
        row={c:None for c in SCHEMA["order_reviews"]}
        row.update(order_id=oid,review_id=rid,review_score=score,review_answer_timestamp=answer,
                   review_creation_date="2018-01-07 00:00:00",_source_row=source+2)
        reviews.append(row)
    return {"orders":pd.DataFrame(orders),"order_items":items,"order_payments":payments,"order_reviews":pd.DataFrame(reviews)}


class MetricTests(unittest.TestCase):
    def setUp(self):
        self.data=fixtures()
        self.fact=build_order_fact(self.data)
        self.temp_root=Path(__file__).resolve().parents[1]/"work"
        self.temp_root.mkdir(exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=self.temp_root)
        self.db=Path(self.tmp.name)/"test.sqlite"
        with closing(sqlite3.connect(self.db)) as con:
            self.fact.to_sql("fact_orders",con,index=False)
            self.data["order_items"].to_sql("clean_order_items",con,index=False)
            pd.DataFrame([("p1","cat1"),("p2","cat2")],columns=["product_id","product_category_name"]).to_sql("clean_products",con,index=False)

    def tearDown(self):
        assert Path(self.tmp.name).resolve().is_relative_to(self.temp_root.resolve())
        self.tmp.cleanup()

    def summary(self,**kwargs):
        return get_metrics(self.db,**kwargs)[0]

    def test_no_item_payment_join_fanout(self):
        got=self.summary()
        self.assertEqual(got["order_count"],5)
        self.assertEqual(got["sales_gmv_cents"],10000)
        self.assertEqual(got["delivered_observed_payment_cents"],6300)

    def test_canceled_excluded_from_sales(self):
        self.assertEqual(self.summary()["canceled_order_count"],1)
        self.assertEqual(self.summary(category="cat1")["sales_gmv_cents"],8000)

    def test_same_calendar_day_is_on_time(self):
        got=self.summary()
        self.assertEqual(got["delivery_eligible_order_count"],2)
        self.assertEqual(got["late_order_count"],1)
        self.assertEqual(got["delivery_late_rate"],0.5)

    def test_latest_review_and_review_denominator(self):
        got=self.summary()
        self.assertEqual(got["reviewed_delivered_order_count"],2)
        self.assertEqual(got["low_score_order_count"],1)

    def test_missing_payments_remain_unknown(self):
        self.assertEqual(self.summary()["delivered_missing_payment_order_count"],1)
        self.assertTrue(pd.isna(self.fact.loc[self.fact.order_id.eq("E"),"paid_total_cents"].iloc[0]))

    def test_filters_intersect_same_item(self):
        self.assertEqual(get_metrics(self.db,category="cat2",seller="s1"),[])
        self.assertEqual(self.summary(category="cat2",seller="s2")["sales_gmv_cents"],2000)

    def test_group_sales_additive_counts_nonadditive(self):
        rows=get_metrics(self.db,group_by="category")
        self.assertEqual(sum(r["sales_gmv_cents"] for r in rows),10000)
        self.assertEqual(sum(r["order_count"] for r in rows),5) # D has no items; A belongs to two categories.
        self.assertEqual(sum(r["delivered_order_count"] for r in rows),4)
        self.assertEqual(self.summary()["delivered_order_count"],3)
        self.assertIsNone(rows[0]["delivered_observed_payment_cents"])

    def test_multiseller_excluded_from_handoff_proxy(self):
        a=self.fact.loc[self.fact.order_id.eq("A")].iloc[0]
        self.assertEqual(a.seller_count,2)
        self.assertEqual(a.handoff_eligible,0)
        self.assertTrue(pd.isna(a.carrier_late_proxy))

    def test_zero_denominator_is_null(self):
        self.data["order_reviews"]=self.data["order_reviews"].iloc[:0]
        result=build_order_fact(self.data)
        self.assertTrue(result.review_score.isna().all())
        with closing(sqlite3.connect(self.db)) as con:
            con.execute("UPDATE fact_orders SET delivery_eligible=0,delivery_late=NULL,review_score=NULL")
            con.commit()
        self.assertIsNone(self.summary()["delivery_late_rate"])
        self.assertIsNone(self.summary()["low_score_rate"])
        self.assertEqual(self.summary(seller="s2")["reviewed_delivered_order_count"],0)
        self.assertEqual(self.summary(seller="s2")["low_score_order_count"],0)

    def test_conflicting_payment_key_rejected(self):
        raw=pd.DataFrame([("A","1","credit_card","2","10.00"),("A","1","credit_card","2","12.00")],columns=SCHEMA["order_payments"])
        with self.assertRaises(ValueError):
            clean_table("order_payments",raw)

    def test_identical_payment_row_dedup_but_split_preserved(self):
        raw=pd.DataFrame([("A","1","credit_card","2","10.00"),("A","1","credit_card","2","10.00"),
                          ("A","2","voucher","1","10.00")],columns=SCHEMA["order_payments"])
        clean=clean_table("order_payments",raw)
        self.assertEqual(len(clean),2)
        self.assertEqual(int(clean.payment_value_cents.sum()),2000)

    def test_integer_cents_and_invalid_amounts(self):
        self.assertEqual(money_cents("0.29"),29)
        for bad in ["-1.00","0.001","NaN","Infinity","nonsense"]:
            with self.assertRaises(ValueError):money_cents(bad)

    def test_unknown_profit_and_bound_parameters(self):
        self.assertIsNone(self.summary()["profit_brl"])
        self.assertEqual(get_metrics(self.db,category="' OR 1=1 --"),[])
        with self.assertRaises(ValueError):get_metrics(self.db,month="2018-13")

    def test_review_tie_deterministic(self):
        tied=self.data["order_reviews"].iloc[[1]].copy()
        tied["review_score"]=2
        tied["review_comment_message"]="different same-time review"
        tied["_source_row"]=99
        reviews=pd.concat([self.data["order_reviews"],tied],ignore_index=True)
        a=canonical_reviews(reviews)
        b=canonical_reviews(reviews.iloc[::-1])
        pd.testing.assert_frame_equal(a.reset_index(drop=True),b.reset_index(drop=True))


if __name__=="__main__":
    unittest.main()
