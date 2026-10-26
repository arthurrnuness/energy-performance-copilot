"""Read-only data-gap diagnosis. No missing measurements are replaced by estimates."""
from datetime import date, datetime, time, timedelta, timezone
from math import isfinite
from zoneinfo import ZoneInfo

TZ = ZoneInfo('America/Fortaleza')


def summarize_series(series, start, end):
    if series is None:
        return {'status': 'series_absent', 'valid_points': 0}
    if series.get('truncated'):
        return {'status': 'truncated', 'valid_points': 0}
    points, invalid = {}, 0
    for point in series.get('points', []):
        try:
            stamp = datetime.fromisoformat(point['ts'])
            if stamp.tzinfo is None:
                raise ValueError('timezone missing')
            if not start <= stamp < end:
                continue
            if point.get('value') is None:
                invalid += 1
                continue
            value = float(point['value'])
            if not isfinite(value) or value < 0:
                invalid += 1
                continue
            if stamp in points:
                invalid += 1
                continue
            points[stamp] = value
        except (ValueError, TypeError, KeyError):
            invalid += 1
    ordered = sorted(points.items())
    return {'status': 'available' if ordered and not invalid else 'invalid_readings' if invalid else 'empty',
            'valid_points': len(ordered), 'invalid_points': invalid,
            'unit': series.get('unit'), 'route': series.get('resolved_route'),
            'first_ts': ordered[0][0].isoformat() if ordered else None,
            'last_ts': ordered[-1][0].isoformat() if ordered else None,
            'minimum': min(points.values()) if ordered else None,
            'maximum': max(points.values()) if ordered else None,
            'last_value': ordered[-1][1] if ordered else None,
            'note': 'Availability only; does not establish complete or fresh telemetry.'}


def classify_inverter(tag_exists, raw, daily, power, cumulative):
    """Pure classifier: diagnostic observations, not electrical root causes."""
    if not tag_exists:
        return 'missing_tag'
    if any(s.get('status') in ('api_error', 'truncated', 'invalid_readings', 'invalid_daily_series')
           for s in (raw, daily)):
        return 'unusable_or_unverified_energy'
    raw_ok, daily_ok = raw.get('status') == 'available', daily.get('status') == 'available'
    if raw_ok and not daily_ok:
        return 'aggregation_gap_suspected'
    if daily_ok and not raw_ok:
        return 'raw_history_gap'
    if not raw_ok and not daily_ok:
        if any(s.get('status') == 'available' for s in (power, cumulative)):
            return 'derived_energy_gap'
        return 'telemetry_gap_suspected'
    if abs(raw['maximum'] - daily['kwh']) > 0.1:
        return 'aggregation_value_mismatch'
    return 'no_energy_gap_observed'


RECOMMENDATIONS = {
    'missing_tag': 'Check tag provisioning and source-to-tag mapping.',
    'unusable_or_unverified_energy': 'Check API errors, units, timestamps, truncation and invalid measurements.',
    'aggregation_gap_suspected': 'Compare the raw daily-energy counter with the daily aggregation job and its timezone.',
    'aggregation_value_mismatch': 'Check aggregation boundaries and processing lag; do not substitute a value automatically.',
    'raw_history_gap': 'Check history retention and the short-window API route.',
    'derived_energy_gap': 'Check corrected daily-energy processing; other telemetry is available.',
    'telemetry_gap_suspected': 'Check collection and device communication; empty history does not establish inverter failure.',
    'no_energy_gap_observed': 'Continue monitoring; available readings alone do not establish equipment health.',
}


class LiveBackend:
    """Lazy imports let offline demos and tests run without credentials."""
    def load_context(self, sap_id, day):
        from postgres_connection import get_postgres_connection
        with get_postgres_connection() as c:
            with c.cursor() as q:
                q.execute('SELECT plant_id,plant_name FROM plants WHERE sap_id=%s', (sap_id,))
                rows = q.fetchall()
                if len(rows) != 1:
                    raise ValueError('SAP ID must resolve to one registered plant')
                plant, name = rows[0]
                q.execute('SELECT aioti_plant_id,device_id FROM aioti_device_mapping WHERE plant_id=%s ORDER BY device_id', (plant,))
                mappings = q.fetchall()
                q.execute('SELECT actual_kwh,synced_at FROM daily_generation WHERE plant_id=%s AND generation_date=%s', (plant,day))
                stored = q.fetchone()
                q.execute('SELECT forecast_mwh FROM monthly_forecast WHERE plant_id=%s AND month_num=%s', (plant,day.month))
                forecast = q.fetchone()
        value = stored[0] if stored else None
        return {'plant': name, 'sap_id': sap_id, 'day': day.isoformat(),
                'mappings': [{'site':s,'device':d} for s,d in mappings],
                'railway': {'status':'available' if value is not None and isfinite(float(value)) and value >= 0 else 'missing_or_invalid',
                            'kwh':float(value) if value is not None and isfinite(float(value)) and value >= 0 else None,
                            'synced_at':stored[1].isoformat() if stored and stored[1] else None},
                'forecast_mwh':float(forecast[0]) if forecast else None}

    def tags(self, site):
        from aioti_connection import aioti_get
        result = aioti_get('/datastudio/tags', params={'plant_id':site,'device_type':'inverter','limit':5000})
        if len(result.get('items', [])) >= 5000:
            raise ValueError('Potentially truncated catalog')
        return result.get('items', [])

    def series(self, site, paths, start, end, aggregation='max'):
        from aioti_connection import aioti_get, aioti_post
        if not paths:
            return {}
        result = {}
        for offset in range(0,len(paths),50):
            batch = paths[offset:offset+50]
            selection = aioti_post('/datastudio/selection', {
                'power_plant_id':site,'start_ts':start.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S'),
                'end_ts':end.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S'),
                'timezone':'UTC','selection_name':f'Gap diagnostic {site} {start.date()}',
                'historico_aggregation_default':aggregation,
                'items':[{'pathname':p,'aggregation_override':aggregation} for p in batch]})
            data = aioti_get('/datastudio/series', params={'selection_id':selection['selection_id']})
            if data.get('errors'):
                raise ValueError(str(data['errors']))
            for series in data.get('series', []):
                path = series.get('pathname')
                if path not in batch or path in result:
                    raise ValueError('Unexpected or duplicate series')
                result[path] = series
        return result

    def fabric(self, sap_id, day):
        from contextlib import closing
        from fabric_connection import get_fabric_connection
        with closing(get_fabric_connection()) as c:
            with closing(c.cursor()) as q:
                q.execute('''SELECT valor_consumo_total FROM ouro.telemedicao_geracao_diaria
                             WHERE id_usina_sap=? AND data_medicao>=? AND data_medicao<?''',
                          (sap_id,day,day+timedelta(days=1)))
                rows=q.fetchall()
        if not rows:
            return {'status':'missing','kwh':None}
        if len(rows) != 1 or rows[0][0] is None or not isfinite(float(rows[0][0])) or rows[0][0] < 0:
            return {'status':'invalid_or_duplicate','kwh':None}
        return {'status':'available','kwh':float(rows[0][0])}


def investigate_data_gaps(sap_id, day, check_fabric=False, backend=None):
    """Compare tag catalog, raw history, daily aggregates, and Railway/Fabric."""
    backend = backend or LiveBackend()
    day = date.fromisoformat(day) if isinstance(day,str) else day
    if type(day) is not date or day >= datetime.now(TZ).date():
        raise ValueError('Use a completed calendar day')
    start = datetime.combine(day,time.min,tzinfo=TZ)
    end = start+timedelta(days=1)
    context = backend.load_context(sap_id,day)
    evidence, findings = [], []

    def record(kind, scope, observed):
        identifier = f'E{len(evidence)+1}'
        evidence.append({'id':identifier,'kind':kind,'scope':scope,'observed':observed})
        return identifier

    railway_id=record('railway_daily_generation',sap_id,context['railway'])
    forecast_id=record('forecast_reference',sap_id,{'forecast_mwh':context.get('forecast_mwh')})
    upstream={'status':'not_checked'}
    if check_fabric:
        try: upstream=backend.fabric(sap_id,day)
        except Exception as exc: upstream={'status':'api_error','error_type':type(exc).__name__}
    upstream_id=record('fabric_source',sap_id,upstream)
    stored=context['railway']
    source_class = ('fabric_source_gap' if upstream['status']=='missing' else
                    'fabric_source_invalid' if upstream['status']=='invalid_or_duplicate' else
                    'sync_gap' if upstream['status']=='available' and stored['status']!='available' else
                    'sync_value_mismatch' if upstream['status']=='available' and abs(upstream['kwh']-stored['kwh'])>0.01 else
                    'source_check_failed' if upstream['status']=='api_error' else
                    'railway_gap_upstream_unchecked' if stored['status']!='available' else 'no_fabric_gap_observed')
    findings.append({'scope':sap_id,'classification':source_class,
                     'evidence_ids':[railway_id,upstream_id],
                     'recommended_check':'Inspect the Fabric pipeline before sync when source data is absent; compare source and copy when stale. Upstream not_checked means no conclusion about Fabric itself.'})
    if not context['mappings']:
        identifier=record('mapping',sap_id,{'status':'missing'})
        findings.append({'scope':sap_id,'classification':'missing_mapping','evidence_ids':[identifier],
                         'recommended_check':'Validate inverter-to-SAP assignments before reconciliation.'})
    by_site={}
    for mapping in context['mappings']:
        by_site.setdefault(mapping['site'],[]).append(mapping['device'])
    for site,devices in by_site.items():
        try:
            tags=backend.tags(site)
            catalog={t['pathname']:t for t in tags}
        except Exception as exc:
            identifier=record('tag_catalog',str(site),{'status':'api_error','error_type':type(exc).__name__})
            findings.append({'scope':str(site),'classification':'catalog_check_failed','evidence_ids':[identifier],
                             'recommended_check':'Restore API access; do not infer missing tags from a failed request.'})
            continue
        expected={d:{name:f'INV_{d}.{name}' for name in ('daily_energy_real','cumulative_energy','active_power')} for d in devices}
        paths=[p for value in expected.values() for p in value.values() if p in catalog]
        try:
            raw=backend.series(site,paths,start,end)
            raw_error=None
        except Exception as exc:
            raw={};raw_error={'status':'api_error','valid_points':0,'error_type':type(exc).__name__}
        daily_paths=[p['daily_energy_real'] for p in expected.values() if p['daily_energy_real'] in catalog]
        try:
            daily=backend.series(site,daily_paths,start-timedelta(days=40),end)
            daily_error=None
        except Exception as exc:
            daily={};daily_error={'status':'api_error','error_type':type(exc).__name__}
        for device,names in expected.items():
            scope=f'{site}/INV_{device}'
            tag_id=record('tag_catalog',scope,{'present':{k:p in catalog for k,p in names.items()}})
            summaries={}
            for name,path in names.items():
                value=raw_error.copy() if raw_error else summarize_series(raw.get(path),start,end)
                expected_unit='kW' if name=='active_power' else 'kWh'
                if value['status']=='available' and value.get('unit') != expected_unit:
                    value['status']='invalid_readings'
                summaries[name]=value
            raw_id=record('raw_history',scope,summaries)
            aggregate={'status':'empty','kwh':None}
            if daily_error:
                aggregate=daily_error.copy()
            elif names['daily_energy_real'] in daily:
                try:
                    from copilot.daily_energy import parse_daily_series
                    values=parse_daily_series(daily[names['daily_energy_real']],day,day)
                    value=values.get(day)
                    aggregate={'status':'available' if value is not None else 'empty','kwh':value}
                except ValueError as exc:
                    aggregate={'status':'invalid_daily_series','kwh':None,'reason':str(exc)}
            aggregate_id=record('daily_aggregate',scope,aggregate)
            classification=classify_inverter(names['daily_energy_real'] in catalog,
                summaries['daily_energy_real'],aggregate,summaries['active_power'],summaries['cumulative_energy'])
            findings.append({'scope':scope,'classification':classification,
                             'evidence_ids':[tag_id,raw_id,aggregate_id],
                             'recommended_check':RECOMMENDATIONS[classification]})
    return {'sap_id':sap_id,'plant':context['plant'],'day':day.isoformat(),
            'generated_at':datetime.now(TZ).isoformat(),'findings':findings,'evidence':evidence,
            'limitations':['No estimated generation is substituted.',
                          'Available or zero-valued telemetry alone does not prove equipment health.',
                          'Suspected processing/telemetry gaps are not confirmed root causes.'],
            'forecast_evidence_id':forecast_id}


if __name__=='__main__':
    import argparse,json
    from pathlib import Path
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sap',required=True)
    parser.add_argument('--date',required=True)
    parser.add_argument('--check-fabric',action='store_true')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    result=investigate_data_gaps(args.sap,args.date,args.check_fabric)
    text=json.dumps(result,indent=2,ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(text+'\n')
        print(f'Saved {args.output}')
    else:print(text)
