
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from aioti_connection import aioti_get, aioti_post
from postgres_connection import get_postgres_connection
from collections import Counter


def get_telemetry_health(aioti_plant_id: int, day: str):
    """
    Analyze inverter power telemetry and detect sustained zero output.

    A zero-power anomaly occurs when:
    - The inverter reports between -0.5 and +0.5 kW
    - At least 4 other inverters report more than 10 kW
    - The condition lasts at least 30 consecutive minutes

    This detects reported zero power, not a confirmed equipment failure.
    """

    report_day = date.fromisoformat(day)

    # --------------------------------------------------
    # 1. Get inverter-to-SAP mapping from PostgreSQL
    # --------------------------------------------------

    with get_postgres_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT m.device_id, p.sap_id
                FROM aioti_device_mapping m
                JOIN plants p ON p.plant_id = m.plant_id
                WHERE m.aioti_plant_id = %s
                ORDER BY m.device_id
                """,
                (aioti_plant_id,)
            )

            devices = {
                int(device_id): sap_id
                for device_id, sap_id in cursor.fetchall()
            }

    if not devices:
        raise ValueError(
            f"No mapped inverters for AIOTI plant {aioti_plant_id}"
        )

    # --------------------------------------------------
    # 2. Convert Fortaleza day boundaries to UTC
    # --------------------------------------------------

    local_start = datetime.combine(
        report_day,
        time.min,
        tzinfo=ZoneInfo("America/Fortaleza")
    )

    utc_start = local_start.astimezone(timezone.utc)
    utc_end = (
        local_start + timedelta(days=1)
    ).astimezone(timezone.utc)

    # --------------------------------------------------
    # 3. Request inverter active power from AIOTI
    # --------------------------------------------------

    selection = aioti_post(
        "/datastudio/selection",
        {
            "power_plant_id": aioti_plant_id,
            "start_ts": utc_start.strftime("%Y-%m-%dT%H:%M:%S"),
            "end_ts": utc_end.strftime("%Y-%m-%dT%H:%M:%S"),
            "timezone": "UTC",
            "selection_name": f"Power health {aioti_plant_id} {day}",
            "historico_aggregation_default": "avg",
            "items": [
                {
                    "pathname": f"INV_{device_id}.active_power",
                    "label": f"Inverter {device_id}",
                    "aggregation_override": "avg",
                }
                for device_id in devices
            ],
        }
    )

    result = aioti_get(
        "/datastudio/series",
        params={
            "selection_id": selection["selection_id"]
        }
    )

    if result.get("errors"):
        raise RuntimeError(
            f"AIOTI errors: {result['errors']}"
        )

    # --------------------------------------------------
    # 4. Organize measurements by inverter
    # --------------------------------------------------

    measurements = {
        device_id: {}
        for device_id in devices
    }

    for series in result.get("series", []):
        device_id = int(series["device_id"])

        if device_id not in devices:
            continue

        if series.get("truncated"):
            raise RuntimeError(
                f"Truncated data for INV_{device_id}"
            )

        if series.get("unit") != "kW":
            raise ValueError(
                f"Unexpected unit for INV_{device_id}: "
                f"{series.get('unit')}"
            )

        readings = {}

        for point in series.get("points", []):
            if point["value"] is None:
                continue

            ts = datetime.fromisoformat(point["ts"])
            if ts.tzinfo is None:
                raise ValueError(
                    f"Timestamp has no timezone: {point['ts']}"
                )

            ts = ts.astimezone(timezone.utc)
            readings[ts] = float(point["value"])

        measurements[device_id] = readings

    # --------------------------------------------------
    # 5. Determine the observed generation period
    # --------------------------------------------------

    generating_times = [
        ts
        for readings in measurements.values()
        for ts, power in readings.items()
        if power > 5
    ]

    if not generating_times:
        return {
            "plant_id": aioti_plant_id,
            "date": day,
            "status": "insufficient_reference_data",
            "devices": [],
        }

    first = min(generating_times)
    last = max(generating_times)

    expected_times = []
    ts = first

    while ts <= last:
        expected_times.append(ts)
        ts += timedelta(minutes=5)

    # --------------------------------------------------
    # 6. Analyze each inverter
    # --------------------------------------------------

    health = []

    for device_id, sap_id in devices.items():

        readings = measurements[device_id]

        # Historical telemetry coverage
        received = sum(
            ts in readings
            for ts in expected_times
        )

        coverage_pct = (
            received / len(expected_times) * 100
        )

        if received == 0:
            telemetry_status = "no_power_telemetry"
        elif coverage_pct < 95:
            telemetry_status = "partial_power_telemetry"
        else:
            telemetry_status = "power_telemetry_available"

        # --------------------------------------------------
        # 7. Detect consecutive zero-power measurements
        # --------------------------------------------------

        zero_streak = 0
        longest_streak = 0

        streak_start = None
        longest_start = None
        longest_end = None

        for ts in expected_times:

            value = readings.get(ts)

            # Count OTHER inverters generating >10 kW.
            # Missing measurements are not treated as zero.
            active_peers = sum(
                1
                for other_id, other_readings in measurements.items()
                if other_id != device_id
                and (
                    other_readings.get(ts) is not None
                    and other_readings[ts] > 10
                )
            )

            is_zero_anomaly = (
                value is not None
                and abs(value) <= 0.5
                and active_peers >= 4
            )

            if is_zero_anomaly:

                if zero_streak == 0:
                    streak_start = ts

                zero_streak += 1

                if zero_streak > longest_streak:
                    longest_streak = zero_streak
                    longest_start = streak_start
                    longest_end = ts + timedelta(minutes=5)

            else:
                zero_streak = 0
                streak_start = None

        # --------------------------------------------------
        # 8. Classify the anomaly
        # --------------------------------------------------

        zero_power_minutes = longest_streak * 5

        if zero_power_minutes >= 30:
            production_alert = "zero_power_while_peers_active"
        else:
            production_alert = None

        health.append({
            "device_id": device_id,
            "sap_id": sap_id,
            "telemetry_status": telemetry_status,
            "coverage_pct": round(coverage_pct, 2),
            "received_intervals": received,
            "expected_intervals": len(expected_times),
            "zero_power_minutes": zero_power_minutes,
            "zero_start": (
                longest_start.isoformat()
                if longest_start else None
            ),
            "zero_end": (
                longest_end.isoformat()
                if longest_end else None
            ),
            "production_alert": production_alert,
        })

    # --------------------------------------------------
    # 9. Return structured results
    # --------------------------------------------------

    return {
        "plant_id": aioti_plant_id,
        "date": day,
        "status": "analyzed",
        "devices": health,
    }



def get_equipment_events(
    aioti_plant_id: int,
    day: str,
    device_ids: list[int],
):
    """Fetch inverter event records for a plant and local calendar day."""

    report_day = date.fromisoformat(day)

    local_start = datetime.combine(
        report_day,
        time.min,
        tzinfo=ZoneInfo("America/Fortaleza"),
    )

    start = local_start.astimezone(timezone.utc)
    end = (
        local_start + timedelta(days=1)
    ).astimezone(timezone.utc)

    events = []
    page_size = 50
    max_pages = 20
    complete = False

    for page in range(1, max_pages + 1):
        response = aioti_get(
            "/events",
            params={
                "plant_id": aioti_plant_id,
                "start_time": start.isoformat(),
                "end_time": end.isoformat(),
                "page": page,
                "page_size": page_size,
            },
        )

        items = response.get("items", [])

        for event in items:
            if str(event.get("device_id")) in {
                str(device_id) for device_id in device_ids
            }:
                events.append(event)

        if len(items) < page_size:
            complete = True
            break

    # Remove duplicate event records
    unique = {}

    for event in events:
        key = (
            event.get("device_id"),
            event.get("event_ts"),
            event.get("event_code"),
            event.get("status"),
            str(event.get("event_value")),
        )
        unique[key] = event

    return {
        "plant_id": aioti_plant_id,
        "date": day,
        "events": sorted(
            unique.values(),
            key=lambda e: e.get("event_ts") or "",
        ),
        "complete": complete,
    }



def investigate_plant(aioti_plant_id: int, day: str):
    health = get_telemetry_health(aioti_plant_id, day)

    flagged = [
        device for device in health["devices"]
        if device["production_alert"]
    ]

    if not flagged:
        return {
            "plant_id": aioti_plant_id,
            "date": day,
            "findings": [],
        }

    event_result = get_equipment_events(
        aioti_plant_id,
        day,
        [device["device_id"] for device in flagged],
    )

    findings = []

    for device in flagged:
        start = datetime.fromisoformat(device["zero_start"])
        end = datetime.fromisoformat(device["zero_end"])

        # Include events up to 60 minutes before the anomaly
        # and 15 minutes after it ends.
        window_start = start - timedelta(hours=1)
        window_end = end + timedelta(minutes=15)

        relevant = []

        for event in event_result["events"]:
            if str(event.get("device_id")) != str(device["device_id"]):
                continue

            raw_ts = event.get("event_ts")
            if not raw_ts:
                continue

            event_ts = datetime.fromisoformat(raw_ts)

            if event_ts.tzinfo is None:
                continue

            if window_start <= event_ts <= window_end:
                relevant.append(event)

        grouped = {}

        for event in relevant:
            code = event.get("event_code") or "unknown"

            if code not in grouped:
                grouped[code] = {
                    "code": code,
                    "name": event.get("event_name"),
                    "severity": event.get("severity"),
                    "records": 0,
                    "statuses": Counter(),
                }

            grouped[code]["records"] += 1
            grouped[code]["statuses"][event.get("status")] += 1

        event_summaries = []

        for group in grouped.values():
            event_summaries.append({
                **group,
                "statuses": dict(group["statuses"]),
            })

        findings.append({
            "device_id": device["device_id"],
            "sap_id": device["sap_id"],
            "zero_power_minutes": device["zero_power_minutes"],
            "zero_start": device["zero_start"],
            "zero_end": device["zero_end"],
            "events_near_anomaly": event_summaries,
        })

    return {
        "plant_id": aioti_plant_id,
        "date": day,
        "events_complete": event_result["complete"],
        "findings": findings,
    }

def summarize_energy_counter(series, start, end):
    """Observed-window estimate only: daytime counters may omit edge energy."""
    from math import isfinite
    if not series or series.get('truncated') or series.get('unit') != 'kWh':
        return {'status': 'unusable_series', 'estimated_kwh': None}
    readings = {}
    for point in series.get('points', []):
        if point.get('value') is None:
            continue
        ts = datetime.fromisoformat(point['ts'])
        if ts.tzinfo is None:
            raise ValueError('Energy timestamp must include timezone')
        if not start <= ts < end:
            continue
        value = float(point['value'])
        if not isfinite(value) or value < 0 or (ts in readings and readings[ts] != value):
            return {'status': 'invalid_counter', 'estimated_kwh': None}
        readings[ts] = value
    points = sorted(readings.items())
    if len(points) < 2:
        return {'status': 'insufficient_readings', 'estimated_kwh': None}
    detail = {'first_reading': points[0][0].isoformat(),
              'last_reading': points[-1][0].isoformat(), 'reading_count': len(points)}
    if any(b[1] < a[1] - 0.001 for a, b in zip(points, points[1:])):
        return {**detail, 'status': 'counter_reset_or_decrease', 'estimated_kwh': None}
    max_gap = max((b[0]-a[0]).total_seconds()/60 for a,b in zip(points, points[1:]))
    detail['maximum_gap_minutes'] = max_gap
    if max_gap > 15:
        return {**detail, 'status': 'gaps_in_counter', 'estimated_kwh': None}
    return {**detail, 'status': 'observed_window_estimate',
            'estimated_kwh': round(points[-1][1]-points[0][1], 3)}


def compare_generation_sources(aioti_plant_id: int, day: str):
    """Compare observed inverter counter increases with Fabric data in Railway.

    The comparison is an estimate, not a measurement of confirmed losses.
    Uses Fortaleza calendar dates and the existing inverter-to-plant mapping.
    """
    from collections import defaultdict
    from math import isfinite
    report_day = date.fromisoformat(day)
    local_tz = ZoneInfo('America/Fortaleza')
    if report_day >= datetime.now(local_tz).date():
        raise ValueError('Use a completed calendar day')
    start = datetime.combine(report_day, time.min, tzinfo=local_tz).astimezone(timezone.utc)
    end = start + timedelta(days=1)
    with get_postgres_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute('''SELECT m.device_id, p.plant_id, p.sap_id,
                                     p.plant_name, g.actual_kwh, g.synced_at
                              FROM aioti_device_mapping m
                              JOIN plants p ON p.plant_id=m.plant_id
                              LEFT JOIN daily_generation g
                                ON g.plant_id=p.plant_id AND g.generation_date=%s
                              WHERE m.aioti_plant_id=%s ORDER BY m.device_id''',
                           (report_day, aioti_plant_id))
            mappings = cursor.fetchall()
    if not mappings:
        raise ValueError('No inverter mapping for this AIOTI plant')
    devices = [int(row[0]) for row in mappings]
    if len(set(devices)) != len(devices):
        raise ValueError('Duplicate device mapping')
    selection = aioti_post('/datastudio/selection', {
        'power_plant_id': aioti_plant_id,
        'start_ts': start.strftime('%Y-%m-%dT%H:%M:%S'),
        'end_ts': end.strftime('%Y-%m-%dT%H:%M:%S'), 'timezone': 'UTC',
        'selection_name': f'Energy comparison {aioti_plant_id} {day}',
        'historico_aggregation_default': 'avg',
        'items': [{'pathname': f'INV_{device}.cumulative_energy',
                   'label': f'INV_{device}', 'aggregation_override': 'avg'} for device in devices],
    })
    response = aioti_get('/datastudio/series', params={'selection_id': selection['selection_id']})
    if response.get('errors'):
        raise RuntimeError(f"AIOTI errors: {response['errors']}")
    series_by_path = {}
    for series in response.get('series', []):
        path = series.get('pathname')
        if path in series_by_path:
            raise ValueError(f'Duplicate energy series: {path}')
        series_by_path[path] = series
    groups = defaultdict(list)
    for device, plant, sap, name, actual, synced in mappings:
        estimate = summarize_energy_counter(series_by_path.get(f'INV_{device}.cumulative_energy'), start, end)
        groups[plant].append({'device_id': int(device), **estimate})
    results, seen = [], set()
    for device, plant, sap, name, actual, synced in mappings:
        if plant in seen:
            continue
        seen.add(plant)
        inverters = groups[plant]
        usable = all(i['estimated_kwh'] is not None for i in inverters)
        fabric = float(actual) if actual is not None else None
        valid_fabric = fabric is not None and isfinite(fabric) and fabric >= 0
        total = sum(i['estimated_kwh'] for i in inverters) if usable else None
        comparable = usable and valid_fabric
        results.append({'plant': name, 'sap_id': sap,
            'status': 'estimated_comparison' if comparable else 'insufficient_data',
            'fabric_kwh': fabric if valid_fabric else None,
            'fabric_synced_at': synced.isoformat() if synced else None,
            'inverter_estimated_kwh': round(total, 3) if usable else None,
            'inverters_minus_fabric_kwh': round(total-fabric, 3) if comparable else None,
            'difference_pct_of_inverters': round((total-fabric)/total*100, 2) if comparable and total > 0 else None,
            'inverters': inverters})
    return {'aioti_plant_id': aioti_plant_id, 'date': day, 'timezone': 'America/Fortaleza',
        'method': 'Last minus first observed cumulative energy, per inverter, from averaged five-minute series.',
        'limitations': 'Daytime observed windows may omit energy. Mapping completeness, counter accuracy and meter boundaries require validation. A discrepancy does not confirm electrical losses. Fabric values are the synchronized Railway copy.',
        'plants': results}


# Source-aware diagnostics, shared with the new scoped agent.
from copilot.diagnostics import investigate_data_gaps
