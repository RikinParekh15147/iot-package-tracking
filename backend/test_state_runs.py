"""Telemetry state stitching uses capture order and explicitly estimated times."""
import csv
import io

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.state_runs import build_state_runs, build_state_sessions, build_state_timeline
from backend.test_app import AUTH, server
from backend.test_trips import start


def sample(seq, uptime, *, boot="boot-1", state=None, interval=None, identifier=None):
    data = {"seal_state": "closed", "condition_state": "normal", "movement_state": "stationary"}
    if state:
        data.update(state)
    if interval is not None:
        data["sample_interval_ms"] = interval
    return {"id": identifier if identifier is not None else seq, "device_id": "cargo-01", "boot_id": boot,
            "seq": seq, "uptime_ms": uptime, "kind": "telemetry", "trip_id": "trip-1", "data": data,
            "received_at": "2026-01-01T00:00:00+00:00"}


def upload(client, trip_id, records):
    message = {"device_id": "cargo-01", "boot_id": records[0]["boot_id"], "records": [
        {"seq": row["seq"], "uptime_ms": row["uptime_ms"], "kind": row["kind"],
         "data": row["data"], "trip_id": trip_id} for row in records]}
    return client.post("/api/v1/batches", headers=AUTH, json=message)


def test_ten_five_second_samples_cover_45_observed_50_estimated_seconds():
    runs = build_state_runs([sample(seq + 1, seq * 5000) for seq in range(10)])
    assert len(runs) == 1
    run = runs[0]
    assert run["state"] == {"seal_state": "closed", "condition_state": "normal", "movement_state": "stationary"}
    assert run["sample_count"] == 10
    assert run["observed_span_ms"] == 45000
    assert run["estimated_coverage_ms"] == 50000
    assert run["coverage_is_estimate"] is True
    assert run["cadence_ms"] == 5000 and run["cadence_source"] == "inferred_median"
    assert run["time_basis"] == "device_uptime"
    assert run["record_ids"] == list(range(1, 11))


@pytest.mark.parametrize("field,value", [("seal_state", "packaging_separation"),
                                         ("condition_state", "unsafe_environment"),
                                         ("movement_state", "strong_vibration")])
def test_any_composite_state_change_splits_runs(field, value):
    rows = [sample(1, 0), sample(2, 5000), sample(3, 10000, state={field: value})]
    runs = build_state_runs(rows)
    assert [run["sample_count"] for run in runs] == [2, 1]
    assert runs[1]["break_before"] == "state_change"
    assert runs[1]["state"][field] == value


def test_reboot_never_combines_matching_states():
    rows = [sample(1, 0), sample(2, 5000), sample(1, 0, boot="boot-2", identifier=3)]
    runs = build_state_runs(rows)
    assert len(runs) == 2
    assert [run["sample_count"] for run in runs] == [2, 1]
    assert all(run["break_before"] == "start_of_boot" for run in runs)


@pytest.mark.parametrize("uptime", [5000, 4000])
def test_duplicate_or_backwards_uptime_breaks_continuity(uptime):
    runs = build_state_runs([sample(1, 0), sample(2, 5000), sample(3, uptime)])
    assert len(runs) == 2
    assert runs[1]["break_before"] == "uptime_not_increasing"
    assert runs[0]["coverage_tail_ms"] == 0
    assert runs[0]["observed_span_ms"] == 5000


def test_long_gap_splits_even_when_state_is_unchanged():
    runs = build_state_runs([sample(1, 0), sample(2, 5000), sample(3, 65000)])
    assert len(runs) == 2
    assert runs[1]["break_before"] == "time_gap"
    assert runs[0]["estimated_coverage_ms"] == 10000
    assert runs[1]["estimated_coverage_ms"] == 5000


def test_sparse_explicit_offline_cadence_does_not_claim_continuous_minutes():
    runs = build_state_runs([sample(1, 0, interval=60000), sample(2, 60000, interval=60000)])
    assert len(runs) == 2
    assert all(run["cadence_ms"] == 60000 for run in runs)
    assert all(run["observed_span_ms"] == 0 and run["estimated_coverage_ms"] == 5000 for run in runs)


def test_backfilled_samples_use_sequence_and_uptime_not_receipt_order():
    early = sample(1, 0, identifier=3)
    early["received_at"] = "2026-01-01T00:05:00+00:00"
    middle = sample(2, 5000, identifier=1)
    last = sample(3, 10000, identifier=2)
    run = build_state_runs([middle, last, early])[0]
    assert run["first_seq"] == 1 and run["last_seq"] == 3
    assert run["record_ids"] == [3, 1, 2]
    assert run["observed_span_ms"] == 10000
    assert run["first_received_at"] == "2026-01-01T00:00:00+00:00"
    assert run["last_received_at"] == "2026-01-01T00:05:00+00:00"


def test_extra_immediate_samples_do_not_multiply_coverage():
    rows = [sample(seq + 1, time, interval=5000) for seq, time in enumerate([0, 5000, 5100, 10000])]
    run = build_state_runs(rows)[0]
    assert run["sample_count"] == 4
    assert run["observed_span_ms"] == 10000
    assert run["estimated_coverage_ms"] == 15000  # Not 4 * 5000.
    # A new state observed before the nominal next interval clips the tail.
    rows.append(sample(5, 11000, interval=5000, state={"seal_state": "opened"}))
    runs = build_state_runs(rows)
    assert runs[0]["estimated_coverage_ms"] == 11000
    assert runs[0]["coverage_tail_ms"] == 1000


def test_irregular_cadence_uses_median_and_exposes_estimate():
    run = build_state_runs([sample(1, 0), sample(2, 4000), sample(3, 10000)])[0]
    assert run["cadence_ms"] == 5000 and run["cadence_source"] == "inferred_median"
    assert run["estimated_coverage_ms"] == 15000


def test_explicit_cadence_changes_split_and_missing_matching_metadata_does_not():
    rows = [sample(1, 0, interval=5000), sample(2, 5000), sample(3, 10000, interval=10000)]
    runs = build_state_runs(rows)
    assert len(runs) == 2
    assert runs[0]["sample_count"] == 2
    assert runs[0]["cadence_source"] == "mixed_explicit_inferred"
    assert runs[1]["break_before"] == "cadence_change"


def test_missing_predictions_stay_unknown_and_single_sample_assumption_is_labelled():
    row = sample(1, 0)
    row["data"] = {"lid_closed": True, "seal_state": None, "movement_state": ""}
    run = build_state_runs([row])[0]
    assert run["state"] == {"seal_state": "unknown", "condition_state": "unknown", "movement_state": "unknown"}
    assert run["cadence_source"] == "assumed_5s"
    assert run["observed_span_ms"] == 0 and run["estimated_coverage_ms"] == 5000


def test_non_telemetry_events_never_become_state_samples():
    event = sample(2, 5000)
    event["kind"] = "event"
    event["data"] = {"type": "impact", "value": 4.2}
    assert build_state_runs([sample(1, 0), event])[0]["sample_count"] == 1


def test_api_membership_authentication_pagination_and_trip_isolation(server):
    client, _ = server
    first_trip = start(client).json()["id"]
    assert upload(client, first_trip, [sample(seq + 1, seq * 5000) for seq in range(10)]).status_code == 200
    detail = client.get(f"/api/v1/trips/{first_trip}", headers=AUTH).json()
    run = detail["state_runs"][0]
    endpoint = f"/api/v1/trips/{first_trip}/state-runs/{run['id']}"
    assert client.get(endpoint).status_code == 401
    page = client.get(endpoint + "?offset=2&limit=3", headers=AUTH).json()
    assert page["total"] == 10
    assert [record["seq"] for record in page["records"]] == [3, 4, 5]
    assert all(record["trip_id"] == first_trip and isinstance(record["data"], dict) for record in page["records"])
    assert client.get(endpoint + "?offset=10", headers=AUTH).json()["records"] == []
    assert client.get(endpoint + "?limit=201", headers=AUTH).status_code == 422
    assert client.get(endpoint + "?offset=-1", headers=AUTH).status_code == 422
    client.post(f"/api/v1/trips/{first_trip}/finish", headers=AUTH, json={"status": "completed"})
    next_trip = start(client).json()["id"]
    assert client.get(f"/api/v1/trips/{next_trip}/state-runs/{run['id']}", headers=AUTH).status_code == 404
    assert client.get(f"/api/v1/trips/missing/state-runs/{run['id']}", headers=AUTH).status_code == 404
    assert client.get(f"/api/v1/trips/{next_trip}", headers=AUTH).json()["state_runs"] == []


def test_late_uploads_extend_runs_without_modifying_original_activity_or_export(server):
    client, _ = server
    trip = start(client).json()["id"]
    rows = [sample(seq + 1, seq * 5000) for seq in range(10)]
    upload(client, trip, rows[5:])
    upload(client, trip, rows[:5])
    event = sample(11, 47000)
    event["kind"] = "event"
    event["data"] = {"type": "impact", "value": 4.2}
    upload(client, trip, [event])
    client.post(f"/api/v1/trips/{trip}/logs", headers=AUTH, json={"kind": "note", "message": "Manual handling note"})
    detail = client.get(f"/api/v1/trips/{trip}?limit=1", headers=AUTH).json()
    run = detail["state_runs"][0]
    assert run["sample_count"] == 10 and run["estimated_coverage_ms"] == 50000
    assert detail["alert_count"] == 1 and detail["activity_count"] == 13
    assert detail["run_count"] == 1 and detail["total_state_samples"] == 10
    other = client.get(f"/api/v1/trips/{trip}?kind=other&limit=2", headers=AUTH).json()
    assert other["total"] == 3 and len(other["activity"]) == 2
    assert all(entry["kind"] != "telemetry" for entry in other["activity"])
    assert other["state_runs"] == detail["state_runs"]
    members = client.get(f"/api/v1/trips/{trip}/state-runs/{run['id']}", headers=AUTH).json()["records"]
    assert [row["seq"] for row in members] == list(range(1, 11))
    exported = list(csv.DictReader(io.StringIO(client.get(f"/api/v1/trips/{trip}/export", headers=AUTH).text)))
    assert len(exported) == 13
    assert len([row for row in exported if row["kind"] == "telemetry"]) == 10
    assert any(row["message"] == "Manual handling note" for row in exported)


def test_runs_use_all_trip_samples_not_latest_120_chart_window(server):
    client, _ = server
    trip = start(client).json()["id"]
    rows = [sample(seq + 1, seq * 5000) for seq in range(130)]
    upload(client, trip, rows[:100])
    upload(client, trip, rows[100:])
    detail = client.get(f"/api/v1/trips/{trip}", headers=AUTH).json()
    assert len(detail["telemetry"]) == 120
    assert detail["state_runs"][0]["sample_count"] == 130


def test_state_runs_rebuild_consistently_after_restart(tmp_path):
    path = tmp_path / "runs.db"
    with TestClient(create_app(path, "test-secret")) as client:
        trip = start(client).json()["id"]
        upload(client, trip, [sample(1, 0), sample(2, 5000)])
        original = client.get(f"/api/v1/trips/{trip}", headers=AUTH).json()["state_runs"]
    with TestClient(create_app(path, "test-secret")) as client:
        assert client.get(f"/api/v1/trips/{trip}", headers=AUTH).json()["state_runs"] == original


def test_sessions_link_identical_states_across_gaps_without_filling_unknown_time():
    rows = [sample(1, 0), sample(2, 5000), sample(3, 65000)]
    strict = build_state_runs(rows)
    assert len(strict) == 2  # Conservative segment behavior stays unchanged.
    sessions = build_state_sessions(rows)
    assert len(sessions) == 1
    session = sessions[0]
    assert session["id"] == strict[0]["id"]
    assert session["record_ids"] == [1, 2, 3] and session["sample_count"] == 3
    assert session["segments"] == strict
    assert session["segment_count"] == 2 and session["gap_count"] == 1 and session["has_gaps"] is True
    assert session["observed_span_ms"] == 65000
    assert session["observed_coverage_ms"] == 5000
    assert session["estimated_coverage_ms"] == 15000
    assert session["unobserved_gap_ms"] == 55000


def test_sessions_link_cadence_changes_but_never_reboot_or_actual_state_changes():
    rows = [sample(1, 0, interval=5000), sample(2, 5000, interval=5000),
            sample(3, 10000, interval=10000)]
    session = build_state_sessions(rows)[0]
    assert session["sample_count"] == 3 and session["segment_count"] == 2
    assert session["cadence_ms"] is None and session["cadence_source"] == "mixed_segments"
    assert session["gap_count"] == 0 and session["unobserved_gap_ms"] == 0
    rows += [sample(4, 20000, state={"seal_state": "opened"}),
             sample(5, 25000), sample(1, 0, boot="boot-2", identifier=6)]
    sessions = build_state_sessions(rows)
    assert len(sessions) == 4
    assert [session["sample_count"] for session in sessions] == [3, 1, 1, 1]


@pytest.mark.parametrize("uptime", [5000, 4000])
def test_sessions_do_not_link_through_duplicate_or_backwards_uptime(uptime):
    sessions = build_state_sessions([sample(1, 0), sample(2, 5000), sample(3, uptime)])
    assert len(sessions) == 2
    assert sessions[1]["break_before"] == "uptime_not_increasing"


def test_sparse_sessions_show_linked_observations_not_continuous_minutes():
    rows = [sample(1, 0, interval=60000), sample(2, 60000, interval=60000),
            sample(3, 120000, interval=60000)]
    session = build_state_sessions(rows)[0]
    assert session["sample_count"] == 3 and session["gap_count"] == 2
    assert session["observed_span_ms"] == 120000
    assert session["observed_coverage_ms"] == 0
    assert session["estimated_coverage_ms"] == 15000
    assert session["unobserved_gap_ms"] == 110000


def test_api_membership_includes_both_linked_segments_and_contextual_mixed_timeline(server):
    client, _ = server
    trip = start(client).json()["id"]
    rows = [sample(1, 0), sample(2, 5000), sample(4, 65000)]
    assert upload(client, trip, rows).status_code == 200
    event = sample(3, 7000)
    event["kind"] = "event"
    event["data"] = {"type": "impact", "value": 4.2}
    assert upload(client, trip, [event]).status_code == 200
    client.post(f"/api/v1/trips/{trip}/logs", headers=AUTH, json={"kind": "note", "message": "Manual note"})
    detail = client.get(f"/api/v1/trips/{trip}?limit=1", headers=AUTH).json()
    assert detail["run_count"] == 1
    session = detail["state_runs"][0]
    assert session["sample_count"] == 3 and session["has_gaps"] is True
    members = client.get(f"/api/v1/trips/{trip}/state-runs/{session['id']}?offset=1&limit=2", headers=AUTH).json()
    assert members["total"] == 3 and [record["seq"] for record in members["records"]] == [2, 4]
    timeline = detail["timeline"]
    assert len(timeline) == 4  # One state card + started + impact + manual note.
    assert len([item for item in timeline if item["type"] == "state"]) == 1
    impact = next(item for item in timeline if item["type"] == "event" and item["entry"]["kind"] == "alert")
    assert impact["predicted_state"] == {"seal_state": "closed", "condition_state": "normal", "movement_state": "stationary"}
    assert impact["prediction_source"] == "at_or_before_event"
    assert impact["prediction_age_ms"] == 2000
    assert impact["state_run_id"] == session["id"]
    assert all(item["predicted_state"] is None for item in timeline
               if item["type"] == "event" and item["entry"]["source"] == "trip")
    assert timeline == sorted(timeline, key=lambda item: (item["created_at"], item["id"]), reverse=True)


def test_event_context_uses_preceding_capture_state_not_future_upload_or_other_boot(server):
    client, _ = server
    trip = start(client).json()["id"]
    # Upload the later state first to exercise backfill rather than receipt order.
    upload(client, trip, [sample(3, 10000, state={"seal_state": "opened"})])
    upload(client, trip, [sample(1, 0)])
    event = sample(2, 5000)
    event["kind"], event["data"] = "event", {"type": "impact", "value": 3.5}
    upload(client, trip, [event])
    other_boot = sample(1, 100, boot="boot-with-no-telemetry")
    other_boot["kind"], other_boot["data"] = "event", {"type": "imu_fault"}
    upload(client, trip, [other_boot])
    timeline = client.get(f"/api/v1/trips/{trip}", headers=AUTH).json()["timeline"]
    events = [item for item in timeline if item["type"] == "event" and item["entry"]["source"] == "device"]
    first_boot_event = next(item for item in events if item["entry"]["boot_id"] == "boot-1")
    assert first_boot_event["predicted_state"]["seal_state"] == "closed"
    assert first_boot_event["prediction_age_ms"] == 5000
    no_telemetry = next(item for item in events if item["entry"]["boot_id"] == "boot-with-no-telemetry")
    assert no_telemetry["predicted_state"] is no_telemetry["prediction_source"] is None


def test_timeline_context_does_not_cross_backwards_uptime_ambiguity():
    row = sample(1, 10000)
    event = {"id": "record-2", "kind": "alert", "source": "device", "boot_id": "boot-1", "seq": 2,
             "uptime_ms": 5000, "created_at": row["received_at"], "data": {"type": "impact"}}
    item = next(item for item in build_state_timeline([row], [event]) if item["type"] == "event")
    assert item["predicted_state"] is None


def test_linked_sessions_and_event_context_remain_trip_scoped(server):
    client, _ = server
    old = start(client).json()["id"]
    upload(client, old, [sample(1, 0), sample(2, 60000)])
    client.post(f"/api/v1/trips/{old}/finish", headers=AUTH, json={"status": "completed"})
    new = start(client).json()["id"]
    event = sample(3, 65000)
    event["kind"], event["data"] = "event", {"type": "impact", "value": 4.0}
    upload(client, new, [event])
    new_detail = client.get(f"/api/v1/trips/{new}", headers=AUTH).json()
    assert new_detail["state_runs"] == []
    new_event = next(item for item in new_detail["timeline"] if item["type"] == "event" and item["entry"]["source"] == "device")
    assert new_event["predicted_state"] is None
    old_detail = client.get(f"/api/v1/trips/{old}", headers=AUTH).json()
    assert old_detail["run_count"] == 1 and old_detail["state_runs"][0]["sample_count"] == 2


def test_main_state_links_movement_and_setup_changes_preserving_every_log(server):
    client, _ = server
    trip = start(client).json()['id']
    rows = [sample(i+1, i*5000, state={
        'seal_state':'likely_opened',
        'movement_state':movement,
        'condition_state':'orientation_uncalibrated' if i%2 else 'normal',
    }) for i,movement in enumerate(['still','moving','still','unknown','moving'])]
    assert upload(client, trip, rows).status_code == 200
    detail = client.get(f'/api/v1/trips/{trip}', headers=AUTH).json()
    assert detail['run_count'] == 1
    session = detail['state_runs'][0]
    assert session['primary_state'] == 'likely_opened'
    assert session['sample_count'] == 5
    assert session['state']['movement_state'] == 'unknown'
    assert session['estimated_coverage_ms'] == 25000
    members = client.get(f"/api/v1/trips/{trip}/state-runs/{session['id']}", headers=AUTH).json()
    assert [r['data'] for r in members['records']] == [r['data'] for r in rows]
    assert len([item for item in detail['timeline'] if item['type']=='state']) == 1


@pytest.mark.parametrize('hazard',['recent_impact','strong_vibration','tilted','unsafe_environment'])
def test_main_state_changes_remain_separate_and_do_not_merge_through_hazards(hazard):
    rows = [sample(1,0,state={'seal_state':'likely_opened'}),
            sample(2,5000,state={'seal_state':'likely_opened','condition_state':hazard}),
            sample(3,10000,state={'seal_state':'likely_opened'})]
    sessions = build_state_sessions(rows)
    assert [r['primary_state'] for r in sessions] == ['likely_opened',hazard,'likely_opened']
    assert [r['sample_count'] for r in sessions] == [1,1,1]
