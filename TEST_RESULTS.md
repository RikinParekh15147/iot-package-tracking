# Guided analysis workbench — 30 October 2026

Full suite: **253 passed**, one existing Starlette/httpx deprecation warning.
A subsequent focused run passed **5 tests**, including the added console snapshot
retention regression and the complete guided browser workflow. No physical
quantitative trial or real analysis session was started by the assistant.

Coverage includes authenticated session/trial APIs, protocol validation, randomized
order enforcement, one recording trial per device, immutable finished evidence,
receipt-window/device isolation, retry deduplication, human markers, persistence,
pagination, export, and preservation after console-ring deletion. Browser checks
exercise create/start/mark/collect/observe/finish/review/export/reload. A live page
check found eight scenarios, no JavaScript errors and no mobile horizontal overflow.
Server restarted with the workbench available at /analysis.

See ANALYSIS_START_HERE.md for operator steps and QUANTITATIVE_ANALYSIS_PLAN.md
for the methodology. Human markers and operator assessments are not calibrated
latency measurements or independently verified accuracy scores.

---

# Responsiveness update — 30 October 2026

**245 tests passed** (192 backend, 28 native firmware helper, 25 browser), with
one existing Starlette/httpx deprecation warning. The ESP32 firmware compiled and
was uploaded to COM4 with flash verification. A brief serial smoke check saw
IMU=OK but no Wi-Fi connection; end-to-end delivery on the new firmware remains
unverified. Local server health and change endpoint returned 200; a live trip
browser check reported no JavaScript errors.

Added authenticated revision notification tests for committed writes, simultaneous
listeners, rejected requests, timeout and server-restart revision identity. Added
a browser regression for data-arrival-driven refresh. The complete suite was
rerun after fixing held-request cleanup bounds. See RESPONSIVENESS.md for timing
choices and QUANTITATIVE_ANALYSIS_PLAN.md for the separate, unexecuted protocol.
No quantitative hardware experiment or measured speed comparison was performed.

The entries below describe earlier builds and their results.

---

# Test results — 29 October 2026

State-first timeline follow-up: **23 browser tests passed**. The live trip page
was also opened in headless Edge with no JavaScript errors. Cargo predictions
are the prominent headings; timing, gaps and calibration notes are collapsed
under Details, which stays open across dashboard refreshes. Browser checks cover
impact, vibration, tilt and environmental warning prominence as well as existing
trip/log workflows. Screenshot: `build/state-first-timeline.png`.

Latest server/dashboard suite: **242 passed**, one existing Starlette/httpx
deprecation warning. The ESP32 was offline during this update; no firmware upload
or new on-device validation was performed for this server-only change.

| Layer | Cases | Result |
| --- | ---: | --- |
| Backend API, SQLite, trips, fetch, Wi-Fi, console, linked state sessions | 190 | Passed |
| Native C++ firmware helpers | 28 | Passed |
| Headless Microsoft Edge workflows | 24 | Passed |

Run: `backend/.venv/Scripts/python.exe -m pytest backend tests -q --junitxml=build/test-results.xml`.
Generated XML is under ignored `build/`. Tests use temporary databases and browser
contexts; synthetic shipments do not enter the live cargo history.

## Latest regression coverage

- Repeated-state stitching: 10 readings at 5-second cadence => 45 seconds observed,
  about 50 seconds estimated coverage. Extra samples do not multiply duration.
- State changes, separate boots, backwards/equal uptime, long gaps, changed or
  sparse cadence, delayed uploads, and missing legacy state fields.
- Run membership pagination and trip isolation; all original records, manual
  notes/alerts and CSV history remain unchanged.
- Dedicated trip pages, persistent old-trip links, expandable predicted-state
  readings, raw JSON, member pagination, and missing-trip handling.
- Device-card Wi-Fi settings use that device, preserving other devices' profiles.
- Existing validation/authentication, failure/transaction/persistence, single-active
  trip, live-fetch matching, console retention/retries, browser text safety,
  sensor-unavailable display, and native decoding/timing/queue tests still pass.

## Earlier fixes and hardware evidence

Tests previously found and fixed SQLite-unsafe heartbeat uptime, uncontrolled
storage errors, and timer edge cases at uptime zero. The firmware decoder was
also fixed to avoid Arduino's `word` macro, and HTTP transport lifetime corrected.

Earlier uploaded builds demonstrated live sensor uploads, about 0.95g stationary
acceleration, a 145-second no-crash run, and queued-event recovery after server
outage and a controlled device restart. The MPU later stopped acknowledging and
recovered at address 0x68 following a hardware restart; its reference was captured
again. These are historical observations for those builds, not validation of an
offline board or every future build. See HARDWARE_VALIDATION.md.

Latest expanded firmware sources (LDR, console streaming, immediate state wake,
5-second online sampling) compiled previously but remain pending their final
upload. Physical light thresholds, impact/vibration sensitivity, actual roaming,
long-duration power/storage endurance and sensor accuracy need hands-on checks.

The latest refinement links identical gap-separated states without filling unknown
time, unifies events/notes with state context, provides Show all states, and reduces
expanded-row height. Regression checks cover the annotated two-state example,
contextual event rows, compact expansion and all-states/mixed-timeline switching.

The latest grouping uses the main cargo prediction. Still/moving and sensor setup
changes stay in original per-reading logs instead of splitting headline cards.
Real cargo-state changes and reboot/clock boundaries remain separate. The live
trip retains all 963 readings in 184 state groups (previously 520). API and browser
regressions verify merged membership, movement history, and separate hazards.
