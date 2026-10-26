
from calendar import monthrange
from datetime import date

from postgres_connection import get_postgres_connection


def analyze_generation(as_of_date):
    month_start = as_of_date.replace(day=1)
    days_elapsed = as_of_date.day
    days_in_month = monthrange(
        as_of_date.year, as_of_date.month
    )[1]

    query = """
        WITH per_plant AS (
            SELECT
                p.plant_id,
                COALESCE(
                    p.reporting_group,
                    p.plant_name
                ) AS reporting_name,
                f.forecast_mwh * 1000 AS forecast_month_kwh,
                COALESCE(SUM(g.actual_kwh), 0) AS actual_mtd_kwh,
                COUNT(g.generation_date) AS reported_days
            FROM plants p
            JOIN monthly_forecast f
                ON p.plant_id = f.plant_id
                AND f.month_num = %s
            LEFT JOIN daily_generation g
                ON p.plant_id = g.plant_id
                AND g.generation_date BETWEEN %s AND %s
            GROUP BY
                p.plant_id,
                p.reporting_group,
                p.plant_name,
                f.forecast_mwh
        )
        SELECT
            reporting_name,
            SUM(actual_mtd_kwh) AS actual_kwh,
            SUM(forecast_month_kwh) * %s / %s
                AS expected_kwh,
            MIN(reported_days) AS minimum_reported_days,
            COUNT(*) AS unit_count
        FROM per_plant
        GROUP BY reporting_name
        ORDER BY reporting_name
    """

    with get_postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                query,
                (
                    as_of_date.month,
                    month_start,
                    as_of_date,
                    days_elapsed,
                    days_in_month,
                ),
            )
            rows = cursor.fetchall()

    for name, actual, expected, reported_days, units in rows:
        actual = float(actual)
        expected = float(expected)

        if reported_days < days_elapsed:
            print(
                f"{name}: INCOMPLETE DATA "
                f"(minimum {reported_days}/{days_elapsed} "
                f"days across {units} units)"
            )
            continue

        if expected <= 0:
            print(f"{name}: INVALID FORECAST")
            continue

        deviation = (actual - expected) / expected * 100

        print(
            f"{name}: "
            f"Actual={actual:,.0f} kWh | "
            f"Expected={expected:,.0f} kWh | "
            f"Deviation={deviation:+.2f}%"
        )



def get_portfolio_performance(as_of_date=None):
    """Screen reporting groups through an inclusive, completed calendar date.

    Defaults to yesterday in Fortaleza. Forecasts are recurring monthly
    references prorated by calendar day, not weather-adjusted expectations.
    Missing/invalid observations never count as zero generation.
    """
    from collections import defaultdict
    from datetime import datetime, timedelta
    from math import isfinite
    from zoneinfo import ZoneInfo

    today = datetime.now(ZoneInfo('America/Fortaleza')).date()
    if as_of_date is None:
        as_of_date = today - timedelta(days=1)
    if isinstance(as_of_date, str):
        as_of_date = date.fromisoformat(as_of_date)
    if type(as_of_date) is not date or as_of_date >= today:
        raise ValueError('as_of_date must be a completed calendar date')

    periods = {
        'mtd': (as_of_date.replace(day=1), as_of_date),
        'last_7_days': (as_of_date - timedelta(days=6), as_of_date),
        'previous_7_days': (as_of_date - timedelta(days=13),
                            as_of_date - timedelta(days=7)),
    }
    start = min(a for a, b in periods.values())
    with get_postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute('SELECT plant_id, COALESCE(reporting_group, plant_name) FROM plants')
            groups = defaultdict(list)
            for plant_id, name in cursor.fetchall():
                groups[name].append(plant_id)
            cursor.execute('SELECT plant_id, month_num, forecast_mwh FROM monthly_forecast')
            forecasts = {(p, m): float(v) * 1000 for p, m, v in cursor.fetchall()}
            cursor.execute('''SELECT plant_id, generation_date, actual_kwh
                              FROM daily_generation
                              WHERE generation_date BETWEEN %s AND %s''',
                           (start, as_of_date))
            generation = {(p, d): float(v) if v is not None else None
                          for p, d, v in cursor.fetchall()}

    results = []
    for name, units in sorted(groups.items()):
        result = {'plant': name, 'unit_count': len(units)}
        for label, (first, last) in periods.items():
            days = [first + timedelta(days=i) for i in range((last-first).days+1)]
            actual = expected = 0.0
            valid = forecast_days = 0
            for unit in units:
                for day in days:
                    value = generation.get((unit, day))
                    if value is not None and isfinite(value) and value >= 0:
                        actual += value
                        valid += 1
                    forecast = forecasts.get((unit, day.month))
                    if forecast is not None and isfinite(forecast) and forecast > 0:
                        expected += forecast / monthrange(day.year, day.month)[1]
                        forecast_days += 1
            required = len(units) * len(days)
            complete = valid == required
            forecast_complete = forecast_days == required
            comparable = complete and forecast_complete
            result[label] = {
                'start': first.isoformat(), 'end': last.isoformat(),
                'valid_unit_days': valid, 'expected_unit_days': required,
                'completeness_pct': round(valid / required * 100, 2),
                'complete': complete, 'forecast_complete': forecast_complete,
                'actual_kwh': round(actual, 2) if valid else None,
                'expected_kwh': round(expected, 2) if forecast_complete else None,
                'deviation_pct': round((actual / expected - 1) * 100, 2) if comparable else None,
                'shortfall_kwh': round(max(expected - actual, 0), 2) if comparable else None,
            }
        mtd = result['mtd']['deviation_pct']
        recent = result['last_7_days']['deviation_pct']
        previous = result['previous_7_days']['deviation_pct']
        delta = recent - previous if recent is not None and previous is not None else None
        result['trend_change_pp'] = round(delta, 2) if delta is not None else None
        result['trend'] = ('unknown' if delta is None else
                           'improving' if delta >= 2 else
                           'declining' if delta <= -2 else 'stable')
        result['priority'] = (
            'data_review' if mtd is None or recent is None else
            'investigate' if mtd <= -10 and recent <= -10 else
            'recovering' if mtd <= -10 else
            'watch' if recent <= -10 else 'routine'
        )
        results.append(result)

    order = {'investigate': 0, 'watch': 1, 'recovering': 2, 'data_review': 3, 'routine': 4}
    results.sort(key=lambda r: (order[r['priority']],
                               -(r['mtd']['shortfall_kwh'] or 0), r['plant']))
    return {
        'as_of_date': as_of_date.isoformat(),
        'forecast_method': 'Recurring monthly forecast, uniformly prorated by calendar day; not weather-adjusted.',
        'thresholds': {'underperformance_pct': -10, 'trend_change_pp': 2},
        'screening': 'Daily: review priorities and data gaps. Weekly: investigate persistent shortfalls; reconcile inverter energy with Fabric before drilling into equipment.',
        'plants': results,
    }


if __name__ == "__main__":
    analyze_generation(date(2026, 10, 22))
