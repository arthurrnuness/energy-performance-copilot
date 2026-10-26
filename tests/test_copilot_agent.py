import json
from types import SimpleNamespace as Obj
import pytest
from copilot.agent import run_investigation,validate_assessment,InvestigationTools
from copilot.evaluation import ReplayTools


def diagnostic():
    return {'sap_id':'DEMO','day':'2026-10-20','plant':'Synthetic unit test',
            'evidence':[{'id':'E1','kind':'test','scope':'DEMO','observed':{'kwh':12}}],
            'findings':[{'scope':'DEMO','classification':'derived_energy_gap','evidence_ids':['E1'],'recommended_check':'Check processing.'}]}


def assessment():
    return {'observations':[{'text':'The supplied observation needs review.','evidence_ids':['E1']}],
            'hypotheses':[],'missing_evidence':['Operator confirmation.'],'recommended_checks':['Inspect processing.'],
            'metrics':[{'evidence_id':'E1','path':'kwh','value':12}], 'conclusion':'data_quality_review'}


def response(content='',calls=None):
    return Obj(message=Obj(content=content,tool_calls=calls or []),prompt_eval_count=2,eval_count=3)


def test_numeric_fabrication_unknown_citations_and_nan_rejected():
    for mutate in ('number','citation','nan'):
        a=assessment()
        if mutate=='number':a['metrics'][0]['value']=999
        if mutate=='citation':a['observations'][0]['evidence_ids']=['E999']
        if mutate=='nan':a['metrics'][0]['value']=float('nan')
        with pytest.raises(ValueError):validate_assessment(a,diagnostic()['evidence'])


def test_agent_tool_trace_and_token_usage():
    case={'device_ids':[10],'tool_results':{'get_reconciliation':{'kwh':12}}}
    calls=iter([response(calls=[Obj(function=Obj(name='get_reconciliation',arguments={}))]),response('Ready to summarize.'),response(json.dumps(assessment()))])
    result=run_investigation(mode='agent',diagnostic=diagnostic(),tools=ReplayTools(case),chat_fn=lambda **kw:next(calls))
    assert result['assessment_origin']=='llm' and result['trace'][0]['evidence_id']=='T1'
    assert result['usage']['total_tokens']==15


def test_invalid_model_output_falls_back_without_discarding_evidence():
    bad=assessment();bad['metrics'][0]['value']=999
    r=run_investigation(mode='agent',diagnostic=diagnostic(),tools=ReplayTools({}),chat_fn=lambda **kw:response(json.dumps(bad)))
    assert r['status']=='baseline_fallback' and r['assessment_origin']=='python_baseline'
    assert r['evidence']==diagnostic()['evidence']


def test_budget_and_unknown_tools_do_not_execute_arbitrary_functions():
    call=Obj(function=Obj(name='execute_shell',arguments={'cmd':'anything'}))
    r=run_investigation(mode='agent',max_calls=1,diagnostic=diagnostic(),tools=ReplayTools({}),chat_fn=lambda **kw:response(calls=[call,call]))
    assert r['status']=='baseline_fallback' and len(r['trace'])==1 and r['trace'][0]['status']=='error'


def test_cross_plant_devices_rejected():
    tools=ReplayTools({'device_ids':[10]})
    with pytest.raises(ValueError):tools.get_alarms([999])
    with pytest.raises(ValueError):tools.get_strings(999)
    live=object.__new__(InvestigationTools);live.device_sites={10:1}
    with pytest.raises(ValueError):live._check_devices([999])
    with pytest.raises(ValueError):live._check_devices([True])


def test_baseline_does_not_require_ollama_or_live_tools():
    r=run_investigation(diagnostic=diagnostic())
    assert r['assessment_origin']=='python_baseline' and r['trace']==[] and r['usage']['total_tokens']==0
