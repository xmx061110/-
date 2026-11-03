import json
import threading
import unittest
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from zhice.agent_server import handler_for

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT/'data/warehouse.sqlite'

@unittest.skipUnless(DB.exists(), '需要真实数据库')
class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        options={'months':[{'value':'2018-01'}], 'categories':[], 'sellers':[]}
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(ROOT,DB,options))
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.url=f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join()

    def post(self,path,value):
        req=urllib.request.Request(self.url+path,data=json.dumps(value).encode(),headers={'Content-Type':'application/json'})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=60) as res:return json.load(res)

    def test_real_selection_and_sql_evidence(self):
        result=self.post('/api/verify',{'month':'2018-01'})
        self.assertEqual(result['evidence']['status'],'passed')
        self.assertEqual(result['evidence']['filters']['month'],'2018-01')
        self.assertIn('raw_',result['evidence']['sql']['reference'])
        self.assertIn('原始表独立复核：通过',result['answer'])

    @unittest.skipUnless((ROOT/'data/lade/warehouse.sqlite').exists(), '需要LaDe数据库')
    def test_original_four_agent_tools_http_and_saved_record(self):
        result=self.post('/api/four-agents',{'city':'hz','day':'07-15','courier_id':'6060',
            'start_time':'09:00','duration_minutes':60,'budget_points':20,'goal':'stability','mode':'tools'})
        self.assertEqual(result['status'],'passed');self.assertEqual(result['result']['model_calls'],0)
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(self.url+'/api/four-agents/runs/'+result['run_id']) as response:
            saved=json.load(response)
        self.assertEqual(result,saved);self.assertEqual(saved['result']['proposal']['policy'],'stable')

    def test_four_agent_rejects_missing_model_and_unknown_fields(self):
        with patch.dict('os.environ',{'ZHICE_MODEL_URL':'','ZHICE_MODEL_KEY':'','ZHICE_MODEL_NAME':''}):
            with self.assertRaises(urllib.error.HTTPError) as caught:self.post('/api/four-agents',{'city':'hz','day':'07-15'})
            self.assertEqual(caught.exception.code,400)
        with self.assertRaises(urllib.error.HTTPError) as caught:self.post('/api/four-agents',{'city':'hz','day':'07-15','mode':'tools','unknown':1})
        self.assertEqual(caught.exception.code,400)

    @unittest.skipUnless((ROOT/'data/lade/warehouse.sqlite').exists(), '需要LaDe数据库')
    def test_four_agent_stream_matches_actual_roles_and_persisted_result(self):
        request={'city':'jl','day':'05-01','courier_id':'14171','start_time':'08:25',
            'duration_minutes':60,'budget_points':0,'goal':'stability','mode':'tools'}
        req=urllib.request.Request(self.url+'/api/four-agents/stream',data=json.dumps(request).encode(),
            headers={'Content-Type':'application/json'})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=60) as response:
            self.assertIn('application/x-ndjson',response.headers['Content-Type'])
            events=[json.loads(line) for line in response if line.strip()]
        self.assertEqual([x['type'] for x in events],['stage_started','stage_completed']*4+['verification_started','result'])
        for index in range(4):
            self.assertEqual(events[index*2]['index'],index)
            self.assertEqual(events[index*2+1]['stage']['role'],events[index*2]['role'])
        result=events[-1]['record'];self.assertEqual(result['result']['status'],'passed')
        self.assertEqual(result['result']['model_calls'],0)
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(self.url+'/api/four-agents/runs/'+result['run_id']) as response:
            self.assertEqual(json.load(response),result)

    def test_model_decision_does_not_call_query(self):
        with patch.dict('os.environ',{'ZHICE_MODEL_URL':'https://example.com/v1/chat/completions','ZHICE_MODEL_NAME':'test','ZHICE_MODEL_KEY':'test'}), patch('zhice.agent_server.ask_model',return_value={'status':'tool_call','arguments':{'month':'2018-01'}}):
            result=self.post('/api/interpret',{'question':'2018年1月订单多少'})
        self.assertEqual(result['arguments'],{'month':'2018-01'})
        self.assertNotIn('evidence',result)

    def test_false_filter_is_rejected_instead_of_querying_everything(self):
        for value in (False, 0, []):
            with self.subTest(value=value), self.assertRaises(urllib.error.HTTPError) as caught:
                self.post('/api/verify', {'month': value})
            self.assertEqual(caught.exception.code, 400)

    def test_triage_endpoint_returns_verified_priority(self):
        result=self.post('/api/triage',{'month':'2018-01'})
        self.assertEqual(result['status'],'passed')
        self.assertGreater(result['checked_sellers'],0)
        self.assertIn('raw_',result['sql']['reference'])

    def test_budget_endpoint_is_feasible_and_rejects_invalid_input(self):
        result=self.post('/api/budget',{'day':'2018-07-01','budget_cents':30000})
        self.assertLessEqual(result['optimized']['cost_cents'],30000)
        self.assertTrue(all(x['priority_units']<=result['optimized']['priority_units'] for x in result['baselines']))
        for data in ({'day':'bad','budget_cents':30000},{'day':'2018-07-01','budget_cents':True}):
            with self.subTest(data=data),self.assertRaises(urllib.error.HTTPError) as caught:
                self.post('/api/budget',data)
            self.assertEqual(caught.exception.code,400)

    def test_triage_rejects_extra_filter_and_invalid_month(self):
        for value in ({'month':[]},{'month':'2018-01','seller':'ignored'}):
            with self.subTest(value=value),self.assertRaises(urllib.error.HTTPError) as caught:
                self.post('/api/triage',value)
            self.assertEqual(caught.exception.code,400)

    def test_feedback_demo_exposes_trace_and_rejects_custom_events(self):
        result=self.post('/api/feedback-demo',{})
        self.assertTrue(all(result['expected_checks'].values()))
        self.assertEqual(result['final_plan']['spent_cents'],1000)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post('/api/feedback-demo',{'events':[]})
        self.assertEqual(caught.exception.code,400)

    def test_rolling_endpoint_verifies_selected_scope_and_rejects_bad_inputs(self):
        for day, budget in [('2018-07-09',10000),('2018-08-10',20000),('2018-07-09',0),('2000-01-01',10000)]:
            r=self.post('/api/rolling',{'day':day,'budget_cents':budget})
            self.assertEqual(r['day'],day)
            self.assertEqual(r['verification']['status'],'passed')
            self.assertTrue(all(row['spent_cents']<=budget for row in r['results']))
        for data in ({'day':'bad','budget_cents':10000},{'day':'2018-07-09','budget_cents':True},
                     {'day':'2018-07-09','budget_cents':10000,'seed':1}):
            with self.subTest(data=data),self.assertRaises(urllib.error.HTTPError) as caught:
                self.post('/api/rolling',data)
            self.assertEqual(caught.exception.code,400)

    @unittest.skipUnless((ROOT/'data/lade/warehouse.sqlite').exists(),'需要LaDe数据库')
    def test_lade_real_database_scope_metrics_and_replay_endpoint(self):
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(self.url+'/api/lade/catalog?city=jl',timeout=30) as response:
            c=json.load(response)
        day=c['day'];courier=c['couriers'][0]['courier_id']
        r=self.post('/api/lade/replay',{'city':'jl','day':day,'courier_id':courier,'budget_points':50})
        self.assertEqual(r['verification']['raw_source_check'],'passed')
        self.assertEqual(r['historical_metrics']['status'],'passed')
        self.assertTrue(all(x['spent_cents']<=5000 for x in r['results']))
        m=self.post('/api/lade/metrics',{'city':'jl','day':day,'courier_id':courier})
        self.assertEqual(m['metrics'],r['historical_metrics']['metrics'])
        for value in ({'city':[],'day':day},{'city':'jl','day':'2026-05-01'},
                      {'city':'jl','day':day,'budget_points':True},{'city':'jl','day':day,'limit':999}):
            with self.subTest(value=value),self.assertRaises(urllib.error.HTTPError) as caught:
                self.post('/api/lade/replay',value)
            self.assertEqual(caught.exception.code,400)

    @unittest.skipUnless((ROOT/'reports/delivery-risk-evaluation.json').exists(), '先运行预测训练')
    def test_historical_risk_results_load(self):
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(self.url+'/api/risk',timeout=30) as res:
            result=json.load(res)
        self.assertEqual(result['report']['test']['purchase_range'],'2018-07-01 至 2018-08-31')
        self.assertEqual(len(result['top_orders']),20)

if __name__ == '__main__': unittest.main()
