"""Copy Fabric daily generation to Railway, matching registered SAP IDs."""
import argparse
import json
from collections import Counter
from contextlib import closing
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from fabric_connection import get_fabric_connection
from postgres_connection import get_postgres_connection
from postgres_sync import upsert_generation_batch


def prepare_batch(records, plant_ids, start, end):
    batch, seen = [], set()
    for sap_id, day, kwh in records:
        if isinstance(day, datetime):
            day = day.date()
        if sap_id not in plant_ids:
            raise ValueError(f'Unregistered SAP ID: {sap_id}')
        key = (plant_ids[sap_id], day)
        if key in seen:
            raise ValueError(f'Duplicate plant/date: {sap_id}, {day}')
        if not isinstance(day, date) or not start <= day <= end:
            raise ValueError(f'Unexpected date for {sap_id}: {day}')
        value = Decimal(str(kwh)) if kwh is not None else None
        if value is None or not value.is_finite() or value < 0:
            raise ValueError(f'Invalid generation for {sap_id}, {day}')
        seen.add(key)
        batch.append((plant_ids[sap_id], day, value))
    return batch


def sync_generation(start_date, end_date, apply=False):
    """Refresh an inclusive range; preview by default, safe to repeat."""
    start = date.fromisoformat(start_date) if isinstance(start_date, str) else start_date
    end = date.fromisoformat(end_date) if isinstance(end_date, str) else end_date
    today = datetime.now(ZoneInfo('America/Fortaleza')).date()
    if start > end or end >= today:
        raise ValueError('Use a valid range ending before today')
    with get_postgres_connection() as c:
        with c.cursor() as q:
            q.execute('SELECT sap_id, plant_id, plant_name FROM plants ORDER BY plant_name')
            plants = q.fetchall()
    lookup, names = {}, {}
    for sap, plant_id, name in plants:
        if not sap or not sap.strip():
            raise ValueError(f'Plant has no SAP ID: {name}')
        if sap in lookup:
            raise ValueError(f'Ambiguous SAP ID: {sap}')
        lookup[sap], names[sap] = plant_id, name
    if not lookup:
        raise ValueError('No registered plants')
    placeholders = ','.join('?' for _ in lookup)
    # SAP identity also covers source rows whose fk_usina is missing.
    query = f'''SELECT id_usina_sap, data_medicao, valor_consumo_total
                FROM ouro.telemedicao_geracao_diaria
                WHERE data_medicao >= ? AND data_medicao < ?
                  AND id_usina_sap IN ({placeholders})
                ORDER BY id_usina_sap, data_medicao'''
    with closing(get_fabric_connection()) as c:
        with closing(c.cursor()) as q:
            q.execute(query, (start, end + timedelta(days=1), *lookup))
            records = [tuple(r) for r in q.fetchall()]
    batch = prepare_batch(records, lookup, start, end)
    source_keys = {(p, d) for p, d, v in batch}
    counts = Counter(p for p, d, v in batch)
    coverage = []
    for sap, plant_id in lookup.items():
        missing = [start + timedelta(days=i) for i in range((end-start).days+1)
                   if (plant_id, start + timedelta(days=i)) not in source_keys]
        coverage.append({'plant': names[sap], 'sap_id': sap,
                         'source_records': counts[plant_id],
                         'missing_in_fabric': [d.isoformat() for d in missing]})
    with get_postgres_connection() as c:
        with c.cursor() as q:
            q.execute('''SELECT plant_id, generation_date, actual_kwh
                         FROM daily_generation WHERE generation_date BETWEEN %s AND %s''',
                      (start, end))
            existing = {(p, d): v for p, d, v in q.fetchall()}
    inserts = sum((p, d) not in existing for p, d, v in batch)
    changes = sum((p, d) in existing and
                  (existing[p, d] is None or Decimal(str(existing[p, d])) != v)
                  for p, d, v in batch)
    summary = {'start': start.isoformat(), 'end': end.isoformat(),
               'mode': 'apply' if apply else 'preview', 'source_records': len(batch),
               'new_records': inserts, 'changed_records': changes,
               'coverage': coverage}
    if apply and batch:
        # One transaction for all generation rows; never delete absent source rows.
        upsert_generation_batch(batch)
    summary['uploaded_records'] = len(batch) if apply else 0
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', type=date.fromisoformat)
    parser.add_argument('--end', type=date.fromisoformat)
    parser.add_argument('--lookback-days', type=int, default=35,
                        help='Rolling refresh includes late data and corrections (default: 35)')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if args.lookback_days < 1:
        parser.error('--lookback-days must be positive')
    end = args.end or datetime.now(ZoneInfo('America/Fortaleza')).date() - timedelta(days=1)
    start = args.start or end - timedelta(days=args.lookback_days-1)
    sync_generation(start, end, apply=args.apply)
