"""Portfolio forecast/Fabric/AIOTI screening, using daily inverter energy."""
import argparse
from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
import json
from math import isfinite
from pathlib import Path
import re
import unicodedata
from zoneinfo import ZoneInfo

from aioti_connection import aioti_get, aioti_post
from generation_performance import get_portfolio_performance
from postgres_connection import get_postgres_connection

TZ = ZoneInfo('America/Fortaleza')


def normalized_name(name):
    value = ''.join(c for c in unicodedata.normalize('NFKD', name.upper()) if not unicodedata.combining(c))
    value = re.sub('[^A-Z0-9]', '', value)
    for number, roman in [('1','I'), ('2','II'), ('3','III')]:
        if value.endswith(number):
            value = value[:-1] + roman
            break
    return value


def load_inventory():
    sites = aioti_get('/plants')
    inventory = {}
    for site in sites:
        site_id = int(site['power_plant_id'])
        catalog = aioti_get('/datastudio/tags', params={
            'plant_id': site_id, 'device_type': 'inverter', 'limit': 5000})
        tags = catalog.get('items', [])
        if len(tags) >= 5000:
            raise ValueError(f'Potentially truncated catalog for site {site_id}')
        inventory[site_id] = {
            'name': site['power_plant_name'],
            'devices': sorted({int(t['device_id']) for t in tags}),
            'daily_paths': {t['pathname'] for t in tags
                            if t.get('point_name') == 'daily_energy_real' and t.get('unit') == 'kWh'},
        }
    return inventory


def discover_mappings(inventory, apply=False):
    """Only match unique names; preserve existing split-site assignments."""
    with get_postgres_connection() as c:
        with c.cursor() as q:
            q.execute('SELECT plant_id,sap_id,plant_name FROM plants')
            plants = q.fetchall()
            q.execute('SELECT aioti_plant_id,device_id,plant_id FROM aioti_device_mapping')
            existing = q.fetchall()
            by_name = defaultdict(list)
            for plant, sap, name in plants:
                by_name[normalized_name(name)].append((plant, sap, name))
            mapped_plants = {p for s,d,p in existing}
            used_devices = {d for s,d,p in existing}
            used_sites = {s for s,d,p in existing}
            proposals, unresolved, rows = [], [], []
            for site, info in inventory.items():
                if site in used_sites:
                    continue
                key = normalized_name(info['name'])
                matches = by_name.get(key, [])
                if len(matches) != 1 or not info['devices']:
                    unresolved.append({'aioti_plant_id': site, 'name': info['name'],
                                       'reason': 'No unique exact normalized plant name or empty inventory'})
                    continue
                plant, sap, name = matches[0]
                if plant in mapped_plants or used_devices.intersection(info['devices']):
                    unresolved.append({'aioti_plant_id': site, 'name': info['name'], 'reason': 'Mapping conflict'})
                    continue
                proposals.append({'aioti_plant_id': site, 'plant': name, 'sap_id': sap, 'device_ids': info['devices']})
                rows.extend((site, d, plant) for d in info['devices'])
                mapped_plants.add(plant)
                used_devices.update(info['devices'])
            if apply and rows:
                q.execute('LOCK TABLE aioti_device_mapping IN SHARE ROW EXCLUSIVE MODE')
                q.execute('SELECT aioti_plant_id,device_id,plant_id FROM aioti_device_mapping')
                if set(q.fetchall()) != set(existing):
                    raise ValueError('Mappings changed during discovery; rerun')
                q.executemany('''INSERT INTO aioti_device_mapping
                    (aioti_plant_id,device_id,plant_id) VALUES (%s,%s,%s)''', rows)
    return {'mode': 'apply' if apply else 'preview', 'proposed': proposals, 'unresolved': unresolved}


from copilot.daily_energy import parse_daily_series


def fetch_daily_energy(inventory, mappings, start, end):
    data, issues = {}, []
    devices_by_site = defaultdict(set)
    for site, device, plant in mappings:
        devices_by_site[site].add(device)
    # Wider request selects the verified hist_daily route, including early month runs.
    request_start = min(start, end - timedelta(days=40))
    utc_start = datetime.combine(request_start, time.min, tzinfo=TZ).astimezone(timezone.utc)
    utc_end = datetime.combine(end+timedelta(days=1), time.min, tzinfo=TZ).astimezone(timezone.utc)
    for site, devices in devices_by_site.items():
        info = inventory.get(site)
        if info is None:
            issues.append({'site': site, 'reason': 'Site absent from current API inventory'})
            continue
        if devices != set(info['devices']):
            issues.append({'site': site, 'reason': 'Mapped inverter inventory differs from API; site withheld'})
            continue
        paths = [f'INV_{d}.daily_energy_real' for d in sorted(devices)
                 if f'INV_{d}.daily_energy_real' in info['daily_paths']]
        for d in sorted(devices):
            if f'INV_{d}.daily_energy_real' not in paths:
                issues.append({'site': site, 'device': d, 'reason': 'No corrected daily-energy tag'})
        for offset in range(0, len(paths), 50):
            batch = paths[offset:offset+50]
            try:
                selection = aioti_post('/datastudio/selection', {
                    'power_plant_id': site, 'start_ts': utc_start.strftime('%Y-%m-%dT%H:%M:%S'),
                    'end_ts': utc_end.strftime('%Y-%m-%dT%H:%M:%S'), 'timezone': 'UTC',
                    'selection_name': f'Portfolio energy {site} through {end}',
                    'historico_aggregation_default': 'max',
                    'items': [{'pathname': p, 'aggregation_override': 'max'} for p in batch]})
                response = aioti_get('/datastudio/series', params={'selection_id': selection['selection_id']})
                if response.get('errors'):
                    raise ValueError(str(response['errors']))
                seen = set()
                staged = {}
                for series in response.get('series', []):
                    path = series.get('pathname')
                    if path not in batch or path in seen:
                        raise ValueError('Unexpected or duplicate series')
                    seen.add(path)
                    device = int(path.split('.')[0].split('_')[1])
                    try:
                        staged[site, device] = parse_daily_series(series, start, end)
                    except ValueError as exc:
                        issues.append({'site': site, 'device': device, 'reason': str(exc)})
                if seen != set(batch):
                    issues.append({'site': site, 'reason': 'Some requested series were absent'})
                data.update(staged)
            except Exception as exc:
                issues.append({'site': site, 'reason': f'Energy request failed: {type(exc).__name__}: {exc}'})
    return data, issues


def compare_period(units, mappings_by_plant, energy, fabric, forecasts, start, end):
    days = [start+timedelta(days=i) for i in range((end-start).days+1)]
    totals = {'fabric_kwh': 0., 'aioti_kwh': 0., 'forecast_kwh': 0.}
    counts = {'fabric': 0, 'aioti': 0, 'forecast': 0}
    gaps = []
    for plant, sap in units:
        mapped = mappings_by_plant.get(plant, [])
        for day in days:
            f = fabric.get((plant, day))
            forecast = forecasts.get((plant, day.month))
            readings = [energy.get((s,d), {}).get(day) for s,d in mapped]
            a = sum(readings) if readings and all(v is not None for v in readings) else None
            expected = forecast*1000/monthrange(day.year,day.month)[1] if forecast is not None and forecast > 0 else None
            for key, value in [('fabric',f),('aioti',a),('forecast',expected)]:
                if value is not None and isfinite(value) and value >= 0:
                    counts[key] += 1
                    totals[key+'_kwh'] += value
                else:
                    gaps.append({'sap_id':sap,'date':day.isoformat(),'source':key})
    required = len(units)*len(days)
    for key in counts:
        if counts[key] != required:
            totals[key+'_kwh'] = None
    f,a,e = totals['fabric_kwh'],totals['aioti_kwh'],totals['forecast_kwh']
    return {'start':start.isoformat(),'end':end.isoformat(),
            'required_unit_days':required,'valid_unit_days':counts,
            **{k:round(v,2) if v is not None else None for k,v in totals.items()},
            'fabric_vs_forecast_pct':round((f/e-1)*100,2) if f is not None and e else None,
            'aioti_vs_forecast_pct':round((a/e-1)*100,2) if a is not None and e else None,
            'aioti_minus_fabric_kwh':round(a-f,2) if a is not None and f is not None else None,
            'difference_pct_of_aioti':round((a-f)/a*100,2) if a and f is not None else None,
            'gaps':gaps}


def reconciliation_status(period, threshold_pct=5.0):
    if period['aioti_kwh'] is None or period['fabric_kwh'] is None:
        return 'data_gap'
    if period['difference_pct_of_aioti'] is None:
        return 'zero_inverter_total_review' if period['fabric_kwh'] > 0 else 'zero_generation'
    if abs(period['difference_pct_of_aioti']) >= threshold_pct:
        return 'review_discrepancy'
    return 'within_screening_threshold'


def get_portfolio_comparison(as_of_date, inventory=None):
    end = date.fromisoformat(as_of_date) if isinstance(as_of_date,str) else as_of_date
    if type(end) is not date or end >= datetime.now(TZ).date():
        raise ValueError('Use a completed calendar date')
    periods = {'day':(end,end),'mtd':(end.replace(day=1),end),
               'last_7_days':(end-timedelta(days=6),end),
               'previous_7_days':(end-timedelta(days=13),end-timedelta(days=7))}
    start = min(a for a,b in periods.values())
    with get_postgres_connection() as c:
        with c.cursor() as q:
            q.execute('SELECT plant_id,sap_id,COALESCE(reporting_group,plant_name) FROM plants')
            groups = defaultdict(list)
            for p,s,n in q.fetchall(): groups[n].append((p,s))
            q.execute('SELECT aioti_plant_id,device_id,plant_id FROM aioti_device_mapping')
            mappings=q.fetchall()
            q.execute('SELECT plant_id,generation_date,actual_kwh FROM daily_generation WHERE generation_date BETWEEN %s AND %s',(start,end))
            fabric={(p,d):float(v) if v is not None else None for p,d,v in q.fetchall()}
            q.execute('SELECT plant_id,month_num,forecast_mwh FROM monthly_forecast')
            forecasts={(p,m):float(v) for p,m,v in q.fetchall()}
    by_plant=defaultdict(list)
    for s,d,p in mappings: by_plant[p].append((s,d))
    inventory = inventory if inventory is not None else load_inventory()
    energy, issues = fetch_daily_energy(inventory,mappings,start,end)
    screening=get_portfolio_performance(end)
    results=[]
    for screened in screening['plants']:
        name=screened['plant']
        result={'plant':name,'priority':screened['priority'],'trend':screened['trend'],
                'trend_change_pp':screened['trend_change_pp'],
                'unmapped_sap_ids':[s for p,s in groups[name] if not by_plant.get(p)]}
        for label,(a,b) in periods.items():
            result[label]=compare_period(groups[name],by_plant,energy,fabric,forecasts,a,b)
        result['reconciliation_status'] = reconciliation_status(result['last_7_days'])
        result['inverter_evidence'] = [
            {'sap_id': sap, 'aioti_plant_id': site, 'device_id': device,
             'daily_kwh': {day.isoformat(): value for day, value in sorted(energy.get((site, device), {}).items())},
             'missing_dates': [(start+timedelta(days=i)).isoformat()
                               for i in range((end-start).days+1)
                               if energy.get((site,device),{}).get(start+timedelta(days=i)) is None]}
            for plant,sap in groups[name] for site,device in by_plant.get(plant, [])]
        missing = result['last_7_days']['aioti_kwh'] is None or result['last_7_days']['fabric_kwh'] is None
        result['next_step'] = ('Resolve missing source data or mappings' if missing else
            'Validate meter boundaries, scaling, counter quality and mappings before interpreting the discrepancy' if result['reconciliation_status'] in ('review_discrepancy','zero_inverter_total_review') else
            'Review seven-day energy reconciliation and then inverter performance' if result['priority']=='investigate' else
            'Continue daily screening; review trends weekly')
        results.append(result)
    return {'as_of_date':end.isoformat(),'generated_at':datetime.now(TZ).isoformat(),
        'forecast_method':screening['forecast_method'],
        'aioti_method':'Sum of daily maximum corrected daily-energy counters; never sum running counter samples.',
        'limitations':'AIOTI daily aggregates do not prove raw telemetry completeness or meter calibration. Fabric is the Railway copy. Missing unit-days withhold period totals; no weather adjustment or root-cause inference.',
        'screening_thresholds': {'absolute_reconciliation_gap_pct': 5.0, 'forecast_underperformance_pct': -10, 'trend_change_pp': 2},
        'api_issues':issues,'plants':results}


def print_summary(report):
    def pct(value):
        return f'{value:+.2f}%' if value is not None else 'n/a'
    print(f"Portfolio through {report['as_of_date']} | percentages against forecast or AIOTI denominator")
    print(f"{'Plant':20} {'Priority':13} {'MTD Fabric':>11} {'7d Fabric':>11} {'7d AIOTI':>11} {'7d gap':>10}  Reconciliation")
    for plant in report['plants']:
        recent = plant['last_7_days']
        print(f"{plant['plant']:20} {plant['priority']:13} "
              f"{pct(plant['mtd']['fabric_vs_forecast_pct']):>11} "
              f"{pct(recent['fabric_vs_forecast_pct']):>11} "
              f"{pct(recent['aioti_vs_forecast_pct']):>11} "
              f"{pct(recent['difference_pct_of_aioti']):>10}  {plant['reconciliation_status']}")
    print('n/a = incomplete/unavailable data or zero denominator; see JSON gaps and api_issues.')


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date',type=date.fromisoformat,default=datetime.now(TZ).date()-timedelta(days=1))
    parser.add_argument('--discover-mappings',action='store_true')
    parser.add_argument('--apply-mappings',action='store_true')
    parser.add_argument('--output',type=Path)
    parser.add_argument('--summary',action='store_true')
    args=parser.parse_args()
    inventory=load_inventory()
    if args.apply_mappings and not args.discover_mappings:
        parser.error('--apply-mappings requires --discover-mappings')
    if args.discover_mappings:
        report=discover_mappings(inventory,apply=args.apply_mappings)
    else:
        report=get_portfolio_comparison(args.date,inventory=inventory)
    text=json.dumps(report,indent=2,ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(text+'\n')
        print(f'Saved {args.output}')
    elif not args.summary:
        print(text)
    if args.summary:
        if args.discover_mappings:
            parser.error('--summary is for comparison reports')
        print_summary(report)
