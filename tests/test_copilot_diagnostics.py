"""Synthetic unit inputs below test edge cases; they are not production measurements."""
from datetime import date,datetime,timedelta
from copilot.diagnostics import classify_inverter,investigate_data_gaps,summarize_series,TZ


def test_classifier_distinguishes_gap_types():
    available={'status':'available','maximum':10,'kwh':10}
    empty={'status':'empty'}
    assert classify_inverter(False,empty,empty,empty,empty)=='missing_tag'
    assert classify_inverter(True,available,empty,empty,empty)=='aggregation_gap_suspected'
    assert classify_inverter(True,empty,empty,available,empty)=='derived_energy_gap'
    assert classify_inverter(True,empty,empty,empty,empty)=='telemetry_gap_suspected'
    assert classify_inverter(True,empty,available,empty,empty)=='raw_history_gap'
    assert classify_inverter(True,available,{**available,'kwh':20},empty,empty)=='aggregation_value_mismatch'
    assert classify_inverter(True,available,available,empty,empty)=='no_energy_gap_observed'
    assert classify_inverter(True,{'status':'api_error'},empty,empty,empty)=='unusable_or_unverified_energy'


def test_zero_is_not_missing_and_end_boundary_is_excluded():
    start=datetime(2026,10,20,tzinfo=TZ);end=start+timedelta(days=1)
    r=summarize_series({'unit':'kWh','points':[{'ts':start.isoformat(),'value':0},{'ts':end.isoformat(),'value':999}]},start,end)
    assert r['status']=='available' and r['maximum']==0 and r['valid_points']==1
    for bad in [float('nan'),float('inf'),-1]:
        r=summarize_series({'points':[{'ts':start.isoformat(),'value':bad}]},start,end)
        assert r['status']=='invalid_readings'


class FakeBackend:
    def __init__(self,stored=None,source=5,raw_value=10,daily_value=None):
        self.stored,self.source,self.raw_value,self.daily_value=stored,source,raw_value,daily_value
    def load_context(self,sap,day):
        return {'sap_id':sap,'plant':'Synthetic unit test','mappings':[{'site':1,'device':10}],
                'railway':{'status':'available' if self.stored is not None else 'missing_or_invalid','kwh':self.stored},'forecast_mwh':1}
    def fabric(self,sap,day):return {'status':'available' if self.source is not None else 'missing','kwh':self.source}
    def tags(self,site):return [{'pathname':'INV_10.'+p,'unit':'kW' if p=='active_power' else 'kWh'} for p in ('daily_energy_real','active_power','cumulative_energy')]
    def series(self,site,paths,start,end,aggregation='max'):
        long=(end-start).days>2
        value=self.daily_value if long else self.raw_value
        return {p:{'unit':'kW' if 'active_power' in p else 'kWh','resolved_route':'hist_daily' if long else 'hist_5min',
                   'aggregation_resolved':'max','points':[] if value is None else [{'ts':datetime(2026,10,20,tzinfo=TZ).isoformat(),'value':value}]} for p in paths}


def test_fabric_sync_gap_is_distinct_from_source_gap_and_no_estimate():
    r=investigate_data_gaps('TEST','2026-10-20',True,FakeBackend())
    labels={f['classification'] for f in r['findings']}
    assert 'sync_gap' in labels and 'aggregation_gap_suspected' in labels
    assert next(e for e in r['evidence'] if e['kind']=='daily_aggregate')['observed']['kwh'] is None
    r=investigate_data_gaps('TEST','2026-10-20',True,FakeBackend(source=None))
    assert r['findings'][0]['classification']=='fabric_source_gap'


def test_upstream_is_not_claimed_missing_when_unchecked():
    r=investigate_data_gaps('TEST','2026-10-20',False,FakeBackend())
    assert r['findings'][0]['classification']=='railway_gap_upstream_unchecked'


def test_api_failure_not_mislabeled_empty():
    class Broken(FakeBackend):
        def series(self,*args,**kwargs):raise TimeoutError('test')
    r=investigate_data_gaps('TEST','2026-10-20',False,Broken())
    assert r['findings'][1]['classification']=='unusable_or_unverified_energy'


def test_tag_catalog_failure_not_mislabeled_missing_tags():
    class Broken(FakeBackend):
        def tags(self,*args):raise TimeoutError('test')
    r=investigate_data_gaps('TEST','2026-10-20',False,Broken())
    assert r['findings'][1]['classification']=='catalog_check_failed'
