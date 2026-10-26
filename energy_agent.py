
import json
import os
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from ollama import chat

from investigation_tools import (
    get_telemetry_health,
    get_equipment_events,
)


MODEL = os.getenv("OLLAMA_MODEL", "qwen3.5:9b")
LOCAL_TZ = ZoneInfo("America/Fortaleza")


def check_inverter_power(aioti_plant_id: int, day: str) -> dict:
    """Detect inverter power anomalies for a plant.

    Args:
        aioti_plant_id: AIOTI plant identifier.
        day: Calendar date in YYYY-MM-DD format.
    """
    return get_telemetry_health(aioti_plant_id, day)


def summarize_inverter_alarms(
    aioti_plant_id: int,
    day: str,
    device_ids: list[int],
) -> dict:
    """Summarize alarm records for specific inverters.

    Args:
        aioti_plant_id: AIOTI plant identifier.
        day: Calendar date in YYYY-MM-DD format.
        device_ids: List of inverter device IDs to investigate.
    """

    result = get_equipment_events(
        aioti_plant_id, day, device_ids
    )

    grouped = defaultdict(list)

    for event in result["events"]:
        key = (
            event["device_id"],
            event.get("event_code"),
            event.get("event_name"),
        )
        grouped[key].append(event)

    summaries = []

    for (device_id, code, name), events in grouped.items():
        times = sorted(
            datetime.fromisoformat(event["event_ts"])
            .astimezone(LOCAL_TZ)
            for event in events
            if event.get("event_ts")
        )

        statuses = {}

        for event in events:
            status = event.get("status", "unknown")
            statuses[status] = statuses.get(status, 0) + 1

        summaries.append({
            "device_id": device_id,
            "code": code,
            "name": name,
            "severity": events[0].get("severity"),
            "records": len(events),
            "statuses": statuses,
            "first": times[0].isoformat() if times else None,
            "last": times[-1].isoformat() if times else None,
        })

    return {
        "complete": result["complete"],
        "alarms": summaries,
        "devices_without_matching_events": [
            device_id
            for device_id in device_ids
            if not any(
                str(s["device_id"]) == str(device_id)
                for s in summaries
            )
        ],
    }


AVAILABLE_TOOLS = {
    "check_inverter_power": check_inverter_power,
    "summarize_inverter_alarms": summarize_inverter_alarms,
}


def run_agent(plant_id: int, day: str):
    messages = [
        {
            "role": "system",
            "content": (
                "You are a solar plant O&M investigation agent. "
                "Use tools to investigate the requested plant and date. "
                "First check inverter power. "
                "For detected anomalies, investigate equipment alarms. "
                "Do not invent measurements, causes, or events. "
                "Distinguish reported zero power from confirmed failure. "
                "No matching alarms does not mean equipment is healthy. "
                "Alarm transitions are not necessarily separate incidents. "
                "Give a concise report with evidence, uncertainty, "
                "and recommended O&M checks. "
                "Never recommend bypassing electrical protections."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Investigate AIOTI plant {plant_id} "
                f"for {day}. Identify inverter anomalies, "
                "examine relevant alarms, and prepare an O&M report."
            ),
        },
    ]

    tools_used = []

    for step in range(6):
        response = chat(
            model=MODEL,
            messages=messages,
            tools=list(AVAILABLE_TOOLS.values()),
        )

        messages.append(response.message)

        calls = response.message.tool_calls or []

        if not calls:
            if not tools_used:
                print("The model did not call any tools.")
                print("No evidence-based investigation completed.")
                return

            print("\n--- AGENT REPORT ---\n")
            print(response.message.content)
            return

        for call in calls:
            name = call.function.name
            arguments = call.function.arguments

            print(f"\n[Step {step + 1}] Tool: {name}")
            print(f"Arguments: {arguments}")

            if name not in AVAILABLE_TOOLS:
                result = {"error": "Unknown tool"}
            else:
                try:
                    result = AVAILABLE_TOOLS[name](**arguments)
                    tools_used.append(name)
                except Exception as exc:
                    result = {
                        "error": str(exc),
                        "tool": name,
                    }

            messages.append({
                "role": "tool",
                "tool_name": name,
                "content": json.dumps(
                    result,
                    ensure_ascii=False,
                    default=str,
                ),
            })

    print("Agent stopped: maximum investigation steps reached.")


if __name__ == "__main__":
    import argparse
    from pathlib import Path
    from copilot.agent import run_investigation

    parser = argparse.ArgumentParser(description="Energy investigation: scoped SAP pipeline or original inverter agent")
    parser.add_argument("--sap", help="SAP unit, for example BETN01")
    parser.add_argument("--date", help="Completed Fortaleza date, YYYY-MM-DD")
    parser.add_argument("--mode", choices=["baseline", "agent"], default="agent")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.sap:
        if not args.date:
            parser.error("--sap requires --date")
        result = run_investigation(args.sap, args.date, args.mode)
        text = json.dumps(result, indent=2, ensure_ascii=False)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text + "\n")
            print(f"Saved {args.output}")
        else:
            print(text)
    elif args.date or args.output:
        parser.error("Use --sap with --date/--output")
    else:
        parser.error("Provide --sap and --date for an explicitly scoped investigation")
