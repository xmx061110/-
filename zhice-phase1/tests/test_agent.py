import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zhice.agent import ask_model, metric_tool, render

DB = Path(__file__).resolve().parents[1] / 'data/warehouse.sqlite'


class AgentTests(unittest.TestCase):
    def model_call(self, args):
        return lambda payload: {'choices': [{'message': {'tool_calls': [{'function': {'name':'query_logistics_metrics', 'arguments':json.dumps(args)}}]}}]}

    def test_model_tool_call_is_parsed(self):
        decision = ask_model('查询 2018 年 1 月', '', '', '', self.model_call({'month':'2018-01'}))
        self.assertEqual(decision, {'status':'tool_call', 'arguments':{'month':'2018-01'}})

    def test_model_text_with_invented_number_is_not_shown(self):
        result = ask_model('利润多少', '', '', '', lambda _: {'choices':[{'message':{'content':'利润是 999999'}}]})
        self.assertNotIn('999999', result['answer'])

    def test_day_range_and_mutation_cannot_become_month_query(self):
        for question in ('2018年1月3日至20日订单量', '2018-01-03订单量', '删除2018-01订单', '编造2018-01销售额'):
            with self.subTest(question=question):
                decision = ask_model(question, '', '', '', self.model_call({'month':'2018-01'}))
                self.assertEqual(decision['status'], 'needs_clarification')

    def test_unsupported_multi_call_or_arguments_request_clarification(self):
        transport=lambda _: {'choices':[{'message':{'tool_calls':[
            {'function':{'name':'query_logistics_metrics','arguments':'{}'}},
            {'function':{'name':'query_logistics_metrics','arguments':'{}'}}]}}]}
        self.assertEqual(ask_model('两个类别', '', '', '', transport)['status'], 'needs_clarification')
        for args in ({'month':'2018-13'}, {'category':['x','y']}, {'state':'SP'}):
            self.assertEqual(ask_model('查询', '', '', '', self.model_call(args))['status'], 'needs_clarification')

    def test_relative_time_and_guessed_code_are_not_confirmed(self):
        for question,args in [('最近一个月订单量',{'month':'2018-08'}),('美妆类订单量',{'category':'beleza_saude'})]:
            self.assertEqual(ask_model(question,'','','',self.model_call(args))['status'],'needs_clarification')

    def test_invalid_parameters_are_rejected(self):
        for value in ({'month':'2018-13'}, {'seller':'bad'}, {'category':'x OR 1=1'}, {'sql':'select 1'}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                metric_tool(DB, value)

    def test_failed_independent_check_does_not_produce_metrics(self):
        with patch('zhice.agent.verify_selection', return_value={'status':'failed'}):
            with self.assertRaises(RuntimeError):
                metric_tool(DB, {})

    @unittest.skipUnless(DB.exists(), '需要第一阶段真实数据库')
    def test_three_real_selections_with_independent_checks(self):
        for filters in ({'month':'2018-01'}, {'category':'beleza_saude'}, {'seller':'4a3ca9315b744ce9f8e9374361493884'}):
            with self.subTest(filters=filters):
                result = metric_tool(DB, filters)
                self.assertEqual(result['status'], 'passed')
                self.assertEqual(result['checks_passed'], result['check_count'])
                self.assertEqual(result['filters'], {k:filters.get(k) for k in ('month','category','seller')})
                self.assertIn('原始表独立复核：通过', render(result))
                self.assertTrue(result['sql']['reference'])


if __name__ == '__main__': unittest.main()
