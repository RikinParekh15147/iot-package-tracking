"""Conservative state summaries built from original trip telemetry.

Uptime is meaningful only within a boot. Sequence determines capture order so
late uploads do not reverse history, and backwards uptime remains detectable.
Estimated coverage is not proof that a state persisted between observations.
"""
from collections import defaultdict
from bisect import bisect_right
import json
from statistics import median


STATE_FIELDS = ("seal_state", "condition_state", "movement_state")
DEFAULT_CADENCE_MS = 5000
MAX_GAP_MS = 15000
MAX_TAIL_MS = 5000


def decode_record(row):
    record = dict(row)
    if isinstance(record["data"], str):
        record["data"] = json.loads(record["data"])
    return record


def state_of(record):
    # Legacy sensor values cannot reliably reconstruct onboard predictions.
    return {key: record["data"][key] if isinstance(record["data"].get(key), str)
            and record["data"][key].strip() else "unknown" for key in STATE_FIELDS}


def primary_state(state):
    """Cargo headline used to link cards; movement stays in original logs."""
    condition = state.get("condition_state")
    if condition in {"recent_impact", "strong_vibration", "tilted", "unsafe_environment"}:
        return condition
    return state.get("seal_state") or "unknown"


def explicit_cadence(record):
    value = record["data"].get("sample_interval_ms")
    return value if type(value) is int and 0 < value <= 86_400_000 else None


def build_state_runs(rows):
    """Return summaries in boot order, then sequence order within each boot.

    Different boots cannot be absolutely ordered using device uptime. Their
    display order follows earliest server receipt, with boot ID as tie-breaker.
    Raw rows remain unchanged and available through the run membership IDs.
    """
    boots = defaultdict(list)
    for row in rows:
        record = decode_record(row)
        if record["kind"] == "telemetry":
            boots[record["boot_id"]].append(record)
    ordered_boots = sorted(boots, key=lambda boot: (min(r["received_at"] for r in boots[boot]), boot))
    summaries = []
    for boot in ordered_boots:
        records = sorted(boots[boot], key=lambda record: (record["seq"], record["uptime_ms"], record["id"]))
        deltas = [later["uptime_ms"] - earlier["uptime_ms"] for earlier, later in zip(records, records[1:])]
        regular_deltas = [delta for delta in deltas if 0 < delta <= MAX_GAP_MS]
        inferred = int(median(regular_deltas)) if regular_deltas else DEFAULT_CADENCE_MS
        inferred_source = "inferred_median" if regular_deltas else "assumed_5s"
        groups = []
        current = None
        previous = None
        for record in records:
            state = state_of(record)
            explicit = explicit_cadence(record)
            cadence = explicit if explicit is not None else inferred
            source = "explicit" if explicit is not None else inferred_source
            gap_limit = min(MAX_GAP_MS, max(1000, 3 * cadence))
            reason = None
            if previous is None:
                reason = "start_of_boot"
            else:
                delta = record["uptime_ms"] - previous["uptime_ms"]
                if delta <= 0:
                    reason = "uptime_not_increasing"
                elif delta > min(current["gap_limit_ms"], gap_limit):
                    reason = "time_gap"
                elif state != current["state"]:
                    reason = "state_change"
                elif cadence != current["cadence_ms"]:
                    reason = "cadence_change"
            if reason is not None:
                current = {"records": [], "state": state, "cadence_ms": cadence,
                           "cadence_source": source, "gap_limit_ms": gap_limit, "break_before": reason}
                groups.append(current)
            elif source != current["cadence_source"]:
                current["cadence_source"] = "mixed_explicit_inferred"
            current["records"].append(record)
            previous = record

        for index, group in enumerate(groups):
            members = group["records"]
            first, last = members[0], members[-1]
            span = last["uptime_ms"] - first["uptime_ms"]
            tail = min(group["cadence_ms"], MAX_TAIL_MS)
            next_group = groups[index + 1] if index + 1 < len(groups) else None
            if next_group is not None:
                next_start = next_group["records"][0]["uptime_ms"]
                # Never extrapolate past a contradictory/new observation. A
                # backwards clock makes even this final interval ambiguous.
                tail = min(tail, max(0, next_start - last["uptime_ms"]))
            summaries.append({
                "id": "run-" + str(first["id"]), "state": group["state"],
                "sample_count": len(members), "boot_id": boot,
                "start_uptime_ms": first["uptime_ms"], "end_uptime_ms": last["uptime_ms"],
                "observed_span_ms": span, "estimated_coverage_ms": span + tail,
                "coverage_is_estimate": True, "coverage_tail_ms": tail,
                "cadence_ms": group["cadence_ms"], "cadence_source": group["cadence_source"],
                "gap_limit_ms": group["gap_limit_ms"], "break_before": group["break_before"],
                "time_basis": "device_uptime",
                "first_received_at": min(record["received_at"] for record in members),
                "last_received_at": max(record["received_at"] for record in members),
                "first_seq": first["seq"], "last_seq": last["seq"],
                "record_ids": [record["id"] for record in members],
            })
    return summaries


def build_state_sessions(rows):
    """Link consecutive matching cargo headlines, retaining all detailed segments.

    Strict runs remain intact as segments. A linked card's observed_span_ms is
    its full elapsed span; observed_coverage_ms and estimated_coverage_ms are
    sums of segment coverage, excluding the unobserved holes between them.
    """
    sessions = []
    for run in build_state_runs(rows):
        previous = sessions[-1] if sessions else None
        can_link = (previous is not None and previous["boot_id"] == run["boot_id"]
                    and previous["primary_state"] == primary_state(run["state"])
                    and run["start_uptime_ms"] > previous["end_uptime_ms"]
                    and run["first_seq"] > previous["last_seq"]
                    and run["break_before"] != "uptime_not_increasing")
        if not can_link:
            sessions.append({**run, "state": dict(run["state"]),
                             "primary_state": primary_state(run["state"]),
                             "record_ids": list(run["record_ids"]),
                             "segments": [run], "segment_count": 1,
                             "observed_coverage_ms": run["observed_span_ms"],
                             "gap_count": 0, "unobserved_gap_ms": 0, "has_gaps": False})
            continue
        last_segment = previous["segments"][-1]
        unknown = max(0, run["start_uptime_ms"] - last_segment["end_uptime_ms"]
                      - last_segment["coverage_tail_ms"])
        for field in STATE_FIELDS:
            if previous["state"][field] != run["state"][field]:
                previous["state"][field] = "unknown"
        previous["segments"].append(run)
        previous["segment_count"] += 1
        previous["sample_count"] += run["sample_count"]
        previous["record_ids"].extend(run["record_ids"])
        previous["end_uptime_ms"] = run["end_uptime_ms"]
        previous["last_seq"] = run["last_seq"]
        previous["observed_span_ms"] = previous["end_uptime_ms"] - previous["start_uptime_ms"]
        previous["observed_coverage_ms"] += run["observed_span_ms"]
        previous["estimated_coverage_ms"] += run["estimated_coverage_ms"]
        previous["coverage_tail_ms"] = run["coverage_tail_ms"]
        previous["gap_count"] += int(run["break_before"] == "time_gap")
        previous["unobserved_gap_ms"] += unknown
        previous["has_gaps"] = previous["gap_count"] > 0 or previous["unobserved_gap_ms"] > 0
        previous["first_received_at"] = min(previous["first_received_at"], run["first_received_at"])
        previous["last_received_at"] = max(previous["last_received_at"], run["last_received_at"])
        previous["gap_limit_ms"] = min(previous["gap_limit_ms"], run["gap_limit_ms"])
        if previous["cadence_ms"] != run["cadence_ms"]:
            previous["cadence_ms"] = None
        if previous["cadence_ms"] is None or previous["cadence_source"] != run["cadence_source"]:
            previous["cadence_source"] = "mixed_segments"
    return sessions


def build_state_timeline(rows, entries, sessions=None):
    """Mix linked state cards and every original non-telemetry activity entry.

    Receipt order is approximate occurrence order for offline uploads. Event
    predictions refer to a preceding telemetry snapshot, not new predictions.
    Their age remains visible, and manual entries never inherit device state.
    """
    records = [decode_record(row) for row in rows]
    if sessions is None:
        sessions = build_state_sessions(records)
    items = [{"type": "state", "id": session["id"],
              "created_at": session["first_received_at"], "state_run": session}
             for session in sessions]
    by_boot = defaultdict(list)
    session_by_record = {}
    for session in sessions:
        for identifier in session["record_ids"]:
            session_by_record[identifier] = session["id"]
    for record in records:
        if record["kind"] == "telemetry":
            by_boot[record["boot_id"]].append(record)
    for boot in by_boot:
        by_boot[boot].sort(key=lambda record: (record["seq"], record["id"]))
    sequences = {boot: [record["seq"] for record in records] for boot, records in by_boot.items()}
    for entry in entries:
        if entry["kind"] == "telemetry":
            continue
        context = None
        boot = entry.get("boot_id")
        sequence, uptime = entry.get("seq"), entry.get("uptime_ms")
        if entry["source"] == "device" and boot in by_boot and type(sequence) is int and type(uptime) is int:
            position = bisect_right(sequences[boot], sequence) - 1
            if position >= 0:
                candidate = by_boot[boot][position]
                if candidate["uptime_ms"] <= uptime:
                    context = candidate
        items.append({"type": "event", "id": entry["id"], "created_at": entry["created_at"],
                      "entry": entry, "predicted_state": state_of(context) if context else None,
                      "prediction_source": "at_or_before_event" if context else None,
                      "prediction_age_ms": uptime - context["uptime_ms"] if context else None,
                      "state_run_id": session_by_record.get(context["id"]) if context else None})
    return sorted(items, key=lambda item: (item["created_at"], item["id"]), reverse=True)
