from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from test_lade_db import fixture_entry, pickup_row
from zhice.lade_db import build_database
from zhice.four_agents import run, snapshot, validated_request, verify_plan, RunStore, ROLES
from zhice.budget_optimizer import solve


def transport(role, payload):
    data = json.loads(payload['messages'][1]['content'])
    if role == ROLES[0]: output = {'source': 'lade', 'goal': data['confirmed_request']['goal'], 'steps': ['analysis','optimization','review']}
    elif role == ROLES[1]:
        s=data['snapshot']; attention='empty' if not s['active_total'] else 'overdue' if s['overdue_count'] else 'approaching_deadline' if s['deadline_30min_count'] else 'normal'
        output = {'attention': attention, 'evidence_id': data['evidence_id']}
    elif role == ROLES[2]: output = {'policy': 'immediate' if data['goal']=='priority' else 'stable', 'candidate_id': 'exact_budget'}
    else: output = {'approved': True, 'reason_code': 'constraints_checked'}
    return {'choices': [{'message': {'content': json.dumps(output)}}]}


class FourAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        raw=self.root/'raw'; raw.mkdir()
        entry=fixture_entry(raw,'pickup',[pickup_row(),pickup_row('B','09:10:00','09:45:00','10:00:00')])
        self.db=self.root/'warehouse.sqlite'; build_database(raw,[entry],self.db,'fixture')
        self.store=self.root/'runs.sqlite'
        self.request={'city':'jl','day':'05-01','courier_id':'C','start_time':'09:00','duration_minutes':60,'budget_points':20,'goal':'priority','mode':'model'}

    def tearDown(self): self.temp.cleanup()

    def execute(self, **kwargs): return run(self.db,self.store,self.request,transport=transport,**kwargs)

    def test_four_model_roles_order_persistence_and_pre_replay_choice(self):
        record=self.execute(); r=record['result']
        self.assertEqual(r['status'],'passed'); self.assertEqual(r['model_calls'],4)
        self.assertEqual([x['role'] for x in r['stages']],list(ROLES))
        self.assertEqual(record['record_verification'],'passed')
        self.assertEqual(RunStore(self.store).read(record['run_id']),record)
        self.assertEqual(r['snapshot']['candidate_count'],1)
        self.assertEqual(r['proposal']['policy'],'immediate')
        kinds=[x['payload'].get('kind') for x in record['events']]
        self.assertEqual(kinds[-1],'final_result')
        self.assertEqual(record['events'][-2]['payload']['tool'],'historical_replay')

    def test_no_future_completion_arrivals_or_retrospective_rates_sent_to_model(self):
        sent=[]
        def capture(role,payload): sent.append(deepcopy(payload)); return transport(role,payload)
        a=run(self.db,self.store,self.request,transport=capture)
        for payload in sent:
            text=payload['messages'][1]['content']
            for forbidden in ('finish_s','pickup_time','late_rate','retrospective_metrics','source_timeline','historical_metrics'):
                self.assertNotIn(forbidden,text)
        with closing(sqlite3.connect(self.db)) as con,con:
            con.execute("UPDATE raw_pickup SET pickup_time='05-01 09:55:00' WHERE order_id='B'")
            con.execute("UPDATE tasks SET finish_s=finish_s+600 WHERE external_id='B'")
            con.execute("UPDATE raw_pickup SET pickup_time='05-01 09:35:00' WHERE order_id='A'")
            con.execute("UPDATE tasks SET finish_s=finish_s+300 WHERE external_id='A'")
        second=[]
        def capture2(role,payload): second.append(deepcopy(payload)); return transport(role,payload)
        b=run(self.db,self.store,self.request,transport=capture2)
        self.assertEqual(a['result']['status'],'passed');self.assertEqual(b['result']['status'],'passed')
        self.assertEqual(sent,second)

    def test_model_cannot_rewrite_goal_or_add_fake_facts(self):
        def wrong(role,payload): return {'choices':[{'message':{'content':'{"source":"olist","goal":"priority","steps":[]}'}}]}
        r=run(self.db,self.store,self.request,transport=wrong)['result']
        self.assertEqual(r['status'],'failed'); self.assertIsNone(r['proposal']);self.assertEqual(r['model_calls'],1)

    def test_review_rejection_publishes_no_plan_or_replay(self):
        def reject(role,payload):
            return {'choices':[{'message':{'content':'{"approved":false,"reason_code":"manual_review_required"}'}}]} if role==ROLES[3] else transport(role,payload)
        r=run(self.db,self.store,self.request,transport=reject)['result']
        self.assertEqual(r['status'],'review_rejected');self.assertIsNone(r['proposal']);self.assertNotIn('replay',r)

    def test_progress_starts_before_actual_model_call_and_only_completes_valid_output(self):
        events=[]
        def capture(role,payload):
            self.assertEqual(events[-1]['type'],'stage_started')
            self.assertEqual(events[-1]['role'],role)
            return transport(role,payload)
        record=run(self.db,self.store,self.request,transport=capture,progress=events.append)
        self.assertEqual(record['result']['status'],'passed')
        self.assertEqual([x['type'] for x in events],['stage_started','stage_completed']*4+['verification_started'])
        failed=[]
        broken=lambda role,payload: {'choices':[{'message':{'content':'{}'}}]}
        record=run(self.db,self.store,self.request,transport=broken,progress=failed.append)
        self.assertEqual(record['result']['status'],'failed')
        self.assertEqual([x['type'] for x in failed],['stage_started'])

    def test_budget_and_unknown_tasks_fail_independent_review(self):
        req,now=validated_request(self.db,self.request);s,_=snapshot(self.db,req,now);p=solve(s['items'],2000)
        for corrupted in (dict(p,cost_cents=0),dict(p,selected_ids=['future']),dict(p,selected_ids=p['selected_ids']*2)):
            with self.subTest(corrupted=corrupted),self.assertRaises(ValueError): verify_plan(self.db,req,s,corrupted,'immediate')

    def test_tools_mode_explicitly_makes_no_model_calls_zero_budget(self):
        r=run(self.db,self.store,dict(self.request,mode='tools',budget_points=0))['result']
        self.assertEqual(r['status'],'passed');self.assertEqual(r['model_calls'],0)
        self.assertEqual(r['proposal']['initial_plan']['selected_ids'],[])
        self.assertTrue(all(x['method']=='确定性工具演练' for x in r['stages']))

    def test_late_start_with_no_active_task_has_empty_plan(self):
        r=run(self.db,self.store,dict(self.request,mode='tools',start_time='10:00'))['result']
        self.assertEqual(r['status'],'passed');self.assertEqual(r['snapshot']['candidate_count'],0)
        self.assertEqual(r['stages'][1]['output']['attention'],'empty')

    def test_raw_snapshot_mismatch_stops_before_optimization(self):
        with closing(sqlite3.connect(self.db)) as con,con: con.execute('UPDATE tasks SET window_end_s=window_end_s+600 WHERE task_id=1')
        r=self.execute()['result'];self.assertEqual(r['status'],'failed');self.assertEqual(r['model_calls'],1)

    def test_source_snapshot_and_initial_plan_checked_before_publishing(self):
        with patch('zhice.four_agents.solve',return_value={'selected_ids':['fake'],'cost_cents':1,'priority_units':1}):
            r=self.execute()['result']
        self.assertEqual(r['status'],'failed');self.assertEqual(len(r['stages']),3);self.assertIsNone(r['proposal'])

    def test_failed_model_response_persisted_without_exception_body_or_key(self):
        def crash(role,payload): raise RuntimeError('SECRET-MUST-NOT-BE-SAVED')
        r=run(self.db,self.store,self.request,config=('https://example.com','PRIVATE-KEY','test'),transport=crash)
        text=json.dumps(r);self.assertNotIn('PRIVATE-KEY',text);self.assertNotIn('SECRET-MUST',text)
        self.assertEqual(r['result']['status'],'failed')

    def test_run_event_or_result_tampering_rejected(self):
        r=self.execute()
        with closing(sqlite3.connect(self.store)) as con,con: con.execute("UPDATE runs SET result_json='{}' WHERE run_id=?",(r['run_id'],))
        with self.assertRaises(ValueError): RunStore(self.store).read(r['run_id'])
        with closing(sqlite3.connect(self.store)) as con,con: con.execute("UPDATE events SET payload_json='{}' WHERE run_id=? AND seq=1",(r['run_id'],))
        with self.assertRaises(ValueError): RunStore(self.store).read(r['run_id'])

    def test_invalid_inputs_rejected_and_model_not_silently_downgraded(self):
        for values in ({'budget_points':True},{'goal':[]},{'mode':'auto'},{'city':[]},{'day':'11-31'},{'extra':'x'}):
            with self.subTest(values=values),self.assertRaises(ValueError): run(self.db,self.store,dict(self.request,**values),transport=transport)
        with self.assertRaises(ValueError): run(self.db,self.store,self.request)

    def test_stability_goal_selects_stable_without_replay_score_optimization(self):
        r=run(self.db,self.store,dict(self.request,goal='stability'),transport=transport)['result']
        self.assertEqual(r['status'],'passed');self.assertEqual(r['proposal']['policy'],'stable')

    def test_standalone_as_of_sql_runs_without_python_functions(self):
        req,now=validated_request(self.db,self.request);s,e=snapshot(self.db,req,now)
        with closing(sqlite3.connect(self.db)) as con:
            rows=con.execute(e['standalone_raw_reference']).fetchall()
        self.assertEqual(len(rows),s['accepted_count'])
