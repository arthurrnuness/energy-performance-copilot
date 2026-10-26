from fastapi.testclient import TestClient
import copilot.app as module
from copilot.evaluation import load_cases,evaluate


def test_demo_endpoints_and_live_disabled(tmp_path,monkeypatch):
    monkeypatch.delenv('COPILOT_ENABLE_LIVE',raising=False)
    monkeypatch.setattr(module,'ROOT',tmp_path)
    client=TestClient(module.app)
    assert client.get('/api/health').json()['default_mode']=='historical_replay'
    assert client.post('/api/live',json={'sap_id':'DEMO','day':'2026-10-20'}).status_code==403
    assert client.post('/api/investigate',json={'case_id':'../../.env'}).status_code==404
    cases=client.get('/api/cases').json()
    assert cases
    r=client.post('/api/investigate',json={'case_id':cases[0]['id']})
    assert r.status_code==200 and r.json()['assessment_origin']=='python_baseline'
    assert len(list((tmp_path/'.copilot_runs').glob('*.json')))==1


def test_evaluation_reports_review_limitations():
    cases=load_cases()
    r=evaluate(cases)
    assert r['case_count']>=5 and r['expert_reviewed_cases']==0
    assert r['semantic_support_accuracy'] is None
    assert all(x['citation_and_metric_validation_passed'] for x in r['results'])
