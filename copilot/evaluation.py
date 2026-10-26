"""Replay historical cases against Python and Ollama; report limitations honestly."""
import argparse
import json
from pathlib import Path
from statistics import mean
from copilot.agent import run_investigation,validate_assessment

ROOT=Path(__file__).resolve().parents[1]


class ReplayTools:
    """Offline tool responses are frozen source observations, never live credentials."""
    def __init__(self,case):self.case=case
    def get_reconciliation(self)->dict:
        """Read the recorded source comparison for this historical case."""
        return self.case.get('tool_results',{}).get('get_reconciliation',{'status':'not_recorded','note':'No fabricated replay measurements.'})
    def get_inverter_performance(self)->dict:
        """Read recorded scoped inverter-power evidence if available."""
        return self.case.get('tool_results',{}).get('get_inverter_performance',{'status':'not_recorded'})
    def get_alarms(self,device_ids:list[int])->dict:
        """Read recorded scoped alarms; unavailable recordings are explicit."""
        if any(d not in self.case.get('device_ids',[]) for d in device_ids) or not device_ids or len(device_ids)>10:
            raise ValueError('Device outside replay scope')
        return self.case.get('tool_results',{}).get('get_alarms',{'status':'not_recorded'})
    def get_strings(self,device_id:int)->dict:
        """Read recorded string evidence; never invent missing measurements."""
        if device_id not in self.case.get('device_ids',[]):raise ValueError('Device outside replay scope')
        return self.case.get('tool_results',{}).get('get_strings',{'status':'not_recorded'})
    def functions(self):return [self.get_reconciliation,self.get_inverter_performance,self.get_alarms,self.get_strings]


def score_case(case,report):
    expected=set(case['expected_classifications'])
    actual={f['classification'] for f in report['diagnostic']['findings']}
    text=json.dumps(report['assessment']).lower()
    try:
        validate_assessment(report['assessment'],report['evidence']);valid=True
    except (ValueError,KeyError,TypeError):valid=False
    bad_tools=sum(t['status']!='ok' for t in report['trace'])
    return {'case_id':case['id'],'review_status':case['review_status'],
            'expected_classifications_present':expected<=actual,
            'citation_and_metric_validation_passed':valid,
            'unsupported_certainty_phrase_detected':any(term in text for term in case.get('forbidden_phrases',[])),
            'tool_calls':len(report['trace']),'tool_errors':bad_tools,
            'tool_success_rate':1-bad_tools/len(report['trace']) if report['trace'] else None,
            'llm_completed':report['assessment_origin']=='llm',
            'status':report['status'],'latency_seconds':report['latency_seconds'],'usage':report['usage'],
            'limitations':'Classification checks reuse Python observations; citation validity is structural, not proof of semantic entailment. Expert review remains required.'}


def evaluate(cases,include_agent=False,model=None,chat_fn=None,progress=None):
    results=[]
    for case in cases:
        for mode in (['baseline','agent'] if include_agent else ['baseline']):
            if progress: progress(f"Starting {case['id']} / {mode}")
            report=run_investigation(mode=mode,diagnostic=case['diagnostic'],tools=ReplayTools(case),model=model,chat_fn=chat_fn)
            if progress: progress(f"Finished {case['id']} / {mode}: {report['status']} in {report['latency_seconds']}s")
            results.append({'mode':mode,**score_case(case,report),'assessment':report['assessment'],'trace':report['trace']})
    return {'dataset':'Synthetic demonstration scenarios; not historical production evidence.',
            'case_count':len(cases),'expert_reviewed_cases':sum(c['review_status']=='expert_reviewed' for c in cases),
            'results':results,'mean_latency_seconds':round(mean(r['latency_seconds'] for r in results),3) if results else None,
            'semantic_support_accuracy':None,'semantic_support_note':'Not measured automatically. Human raters must judge whether each cited source supports its claim.',
            'cost_usd':None,'cost_note':'Local compute cost not instrumented; token usage is recorded per run.'}


def load_cases(path=None):
    folder=Path(path) if path else ROOT/'evals'/'cases'
    return [json.loads(p.read_text()) for p in sorted(folder.glob('*.json'))]


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--agent',action='store_true',help='Run Ollama alongside the Python baseline')
    parser.add_argument('--model')
    parser.add_argument('--output',type=Path,default=ROOT/'evals'/'results.json')
    args=parser.parse_args()
    result=evaluate(load_cases(),args.agent,args.model,progress=lambda message:print(message,flush=True))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(f"Evaluated {result['case_count']} cases; expert-reviewed: {result['expert_reviewed_cases']}. Saved {args.output}")
