"""Shared daily aggregate validation; independent of network credentials."""
from datetime import datetime,time
from math import isfinite
from zoneinfo import ZoneInfo
TZ=ZoneInfo("America/Fortaleza")

def parse_daily_series(series, start, end):
    """Accept daily max aggregates only; never sum a running daily counter."""
    if (series.get('truncated') or series.get('unit') != 'kWh'
            or series.get('resolved_route') != 'hist_daily'
            or series.get('aggregation_resolved') != 'max'):
        raise ValueError('Expected untruncated daily max series in kWh')
    values = {}
    for point in series.get('points', []):
        ts = datetime.fromisoformat(point['ts'])
        if ts.tzinfo is None:
            raise ValueError('Timestamp lacks timezone')
        local = ts.astimezone(TZ)
        day = local.date()
        if not start <= day <= end:
            continue
        if local.time() != time.min:
            raise ValueError('Daily aggregate is not aligned with Fortaleza midnight')
        if day in values:
            raise ValueError('Duplicate daily aggregate')
        raw = point.get('value')
        value = float(raw) if raw is not None else None
        values[day] = value if value is not None and isfinite(value) and value >= 0 else None
    return values

