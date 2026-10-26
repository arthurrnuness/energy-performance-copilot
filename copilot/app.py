"""Local portfolio demo. Historical replay by default; live tools require opt-in."""
from datetime import date
import json
import os
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI,HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel,Field
from copilot.agent import run_investigation
from copilot.evaluation import ReplayTools,load_cases

ROOT=Path(__file__).resolve().parents[1]
app=FastAPI(title='Energy Performance Copilot',version='0.1.0')
app.mount('/static',StaticFiles(directory=ROOT/'web'),name='static')


class ReplayRequest(BaseModel):
    case_id:str=Field(min_length=1,max_length=100)
    mode:Literal['baseline','agent']='baseline'


class LiveRequest(BaseModel):
    sap_id:str=Field(pattern=r'^[A-Z0-9]{2,20}$')
    day:date
    mode:Literal['baseline','agent']='baseline'


def save_run(report):
    folder=ROOT/'.copilot_runs';folder.mkdir(exist_ok=True)
    identifier=uuid4().hex
    (folder/(identifier+'.json')).write_text(json.dumps(report,indent=2,default=str)+'\n')
    return {'run_id':identifier,**report}


@app.get('/')
def home():return FileResponse(ROOT/'web'/'index.html')


@app.get('/api/health')
def health():return {'status':'ok','default_mode':'historical_replay','live_enabled':os.getenv('COPILOT_ENABLE_LIVE')=='1'}


@app.get('/api/cases')
def cases():
    return [{'id':c['id'],'title':c['title'],'day':c['diagnostic']['day'],
             'review_status':c['review_status']} for c in load_cases()]


@app.get('/api/portfolio')
def portfolio():return json.loads((ROOT/'evals'/'demo_portfolio.json').read_text())


@app.post('/api/investigate')
def investigate(request:ReplayRequest):
    case=next((c for c in load_cases() if c['id']==request.case_id),None)
    if case is None:raise HTTPException(404,'Unknown historical case')
    return save_run(run_investigation(mode=request.mode,diagnostic=case['diagnostic'],tools=ReplayTools(case)))


@app.post('/api/live')
def live(request:LiveRequest):
    if os.getenv('COPILOT_ENABLE_LIVE')!='1':raise HTTPException(403,'Live mode is disabled; use recorded cases or explicitly enable it locally.')
    try:return save_run(run_investigation(request.sap_id,request.day.isoformat(),request.mode))
    except Exception as exc:raise HTTPException(502,f'Live investigation failed ({type(exc).__name__}); inspect credentials and connectivity locally.') from exc
