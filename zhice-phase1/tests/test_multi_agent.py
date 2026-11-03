import json
import unittest
from unittest.mock import patch
from zhice.multi_agent import run_workflow

EVIDENCE={'status':'passed','filters':{'month':'2018-01','category':None,'seller':None},'checked_sellers':1,
 'priority':[{'seller':'x','metrics':{'late_order_count':3,'delivery_eligible_order_count':40,'multi_seller_order_count':1}}]}

def response(role,payload):
    data={'diagnosis':{'hypotheses':['handoff','carrier']},'recommendation':{'actions':['check_handoff']},'review':{'approved':True}}[role]
    return {'choices':[{'message':{'content':json.dumps(data)}}]}

class WorkflowTest(unittest.TestCase):
    @patch('zhice.multi_agent.build_triage',return_value=EVIDENCE)
    def test_roles_really_invoke_in_order(self,_):
        calls=[]
        def transport(role,payload):calls.append(role);return response(role,payload)
        r=run_workflow(None,'2018-01',None,'','','test',transport)
        self.assertEqual(calls,['diagnosis','recommendation','review'])
        self.assertEqual(r['status'],'passed');self.assertEqual(len(r['stages']),4)

    @patch('zhice.multi_agent.build_triage',return_value=EVIDENCE)
    def test_invalid_output_stops_chain(self,_):
        calls=[]
        def bad(role,payload):
            calls.append(role);return {'choices':[{'message':{'content':'{"hypotheses":["made_up"]}'}}]}
        with self.assertRaises(ValueError):run_workflow(None,'2018-01',None,'','','test',bad)
        self.assertEqual(calls,['diagnosis'])

    @patch('zhice.multi_agent.build_triage',return_value=EVIDENCE)
    def test_rejected_review_cannot_be_passed(self,_):
        def reject(role,payload):
            if role=='review':return {'choices':[{'message':{'content':'{"approved":false}'}}]}
            return response(role,payload)
        self.assertEqual(run_workflow(None,'2018-01',None,'','','test',reject)['status'],'review_rejected')

    @patch('zhice.multi_agent.build_triage')
    def test_empty_never_calls_model(self,build):
        build.return_value={**EVIDENCE,'priority':[]}
        def fail(*args):raise AssertionError('unexpected model call')
        self.assertEqual(run_workflow(None,'2018-01',None,'','','test',fail)['status'],'no_candidates')

if __name__=='__main__':unittest.main()
