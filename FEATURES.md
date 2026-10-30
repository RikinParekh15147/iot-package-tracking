# Live controls and condition assessment

## Fetch live data

On Overview, click **Fetch live data**. This queues a device request; it does not
just redraw stored history. The next heartbeat delivers the request, and the ESP32
captures a new snapshot, respecting the DHT minimum sampling interval. The UI
waits until that particular snapshot is committed. Request and receipt times are
not interchangeable with old queued-record times. Old firmware/offline boards
receive an actionable error. Server restart clears pending fetch requests.

## Wi-Fi networks

Use the Wi-Fi icon in the dashboard's top bar. Up to eight WPA-passphrase networks
can be saved per device. Lower numeric priorities are attempted first; ties sort
by SSID. Passwords are hidden on browser reads; a blank existing-password field
preserves its saved value. New networks require 8–63 printable ASCII characters.
Settings are stored in the ignored local SQLite database and cached in ESP32 NVS.
They are not encrypted by this prototype; use HTTPS for a cloud server.

Saving stages a new revision. The dialog distinguishes saved settings from the
revision actually confirmed by the device. An already-working connection is kept,
even if a newly added network has higher priority. On Wi-Fi loss or three failed
server heartbeats, the device scans and attempts available profiles. Each must
reach the configured server, not just associate to Wi-Fi. A recently failed
server route is tried after the other available profiles on that pass. The
original setup network is used when the managed list is empty. Sensor collection
and the bounded flash queue continue while network probing runs.

A laptop's LAN-only server may be unreachable from another network. Cloud hosting
with a stable HTTPS address is needed for remote networks that cannot route to it.
New settings need one initial working connection; cached settings survive reboot.
Actual multi-network roaming remains a physical acceptance test.

## Trips

A device has at most one active trip. Complete/cancel it before starting its next
trip. Each record keeps the trip ID known when captured; retries and late uploads
do not move it to the current trip. The device caches its last confirmed trip ID
for offline restarts. It cannot learn about a server-side trip change while offline.
Open an older trip to see its own readings, events, checkpoints and notes.

## Evidence-based states

| Label | Evidence | Limit |
| --- | --- | --- |
| Likely closed | Reed indicates closed; dark too if LDR enabled | Reed-only when LDR disabled |
| Opening suspected | Reed indicates open | Magnet movement alone is not proof of lid opening |
| Likely opened | Reed open plus internal light | Cannot establish authorization |
| Possible packaging gap | Light enters while reed remains closed | Light leakage or placement can cause it |
| Tilted | Angle exceeds configured reference limit | Full accelerometer calibration is incomplete |
| Strong vibration | Repetitive high-pass acceleration, sufficient samples and RMS over 0.25g | Experimental threshold; distinguish via physical tests |
| Recent impact | Armed impact rule triggered within last 15 seconds | Does not prove cargo was smashed or damaged |
| Unsafe environment | Sustained configured temperature/humidity excursion | DHT11 is a prototype sensor |

Opening/light/vibration/tilt alert rules use duration checks and require arming.
Snapshots can still describe current evidence while disarmed. Sensor faults and
missing references are shown as unavailable/uncalibrated, not normal handling.
A single missed MPU frame marks that sample invalid; three consecutive failed
reads invalidate the reference and start sensor recovery. LDR-derived states stay
disabled until the proper bare-LDR resistor divider is installed and configured.

The handling summary preserves counts of recorded alerts and the largest recorded
impact for each trip. It does not infer unseen damage or exposure duration from
sparse samples.

## Useful next ideas

- Authorized-opening window with an audit trail, to distinguish inspection from
  unexpected opening while retaining the evidence.
- Short raw sensor trace around each impact to separate a brief strike from
  sustained shaking and support later analysis.
- Persistent tamper latch requiring explicit acknowledgment after a restart.

## ESP raw logs and immediate state updates

Overview's MPU6050 panel has an **ESP raw logs** link. It opens the application
console, with selected trip/device filters carried over. Search, pause/resume,
and download the visible lines. The ESP32 retains 96 pending log lines in RAM;
the server retains the latest 5,000 per device. Older pending lines are dropped
on overflow and counted. Application logs mirror the sketch's Serial output;
boot-ROM and library/system logging still require USB Serial Monitor. Debug logs
are separate from the persistent cargo event queue and can be lost on power loss.

Every confirmed cargo event or classification transition also queues a state
snapshot and wakes the network task. Upload does not wait for the minute timer.
Noise debounce and sustained-event rules still apply. If the server cannot be
reached, persistent cargo records remain queued for reconnection.

The local LDR divider has now been confirmed installed, so LDR_ENABLED is enabled
in the ignored local configuration. The example configuration stays conservative.
The Light sensor card displays raw ADC, exposure state, and disabled/waiting status
explicitly; its threshold is experimental and should be checked in the actual box.

## Trip detail pages and stitched state logs

Opening a trip in Trips now opens a dedicated detail page. Its URL includes the
trip ID, so refreshing or reopening an older trip keeps that trip selected.
The page contains route/cargo details, grouped predicted-state readings, original
trip events/notes, and the selected trip's latest recorded sensor values.

Consecutive matching seal, condition, and movement states form one run. Expand
**Show logs** for paginated original readings, each carrying its recorded state;
**Raw JSON** shows the immutable record. The full original activity and CSV export
remain available. No records are rewritten, discarded, or moved between trips.

Duration uses device uptime within a boot, not upload receipt time. Ten readings
at 5-second intervals span 45 seconds between observations and estimate about
50 seconds of coverage. Extra immediate samples do not blindly add another five
seconds each. State changes, reboots, backwards/nonincreasing uptime, cadence
changes, and long gaps split runs. Sparse offline samples do not imply continuous
state coverage. Legacy readings without predicted states say State not recorded.

Each card on Device has its own **Wi-Fi networks** action, tied to that device ID.
Settings can be edited while the device is offline; they remain staged until the
ESP32 reconnects and confirms their revision. These UI/server changes do not
require the ESP32 to be connected for browsing stored trip history.

## Compact unified timeline refinement

Trip state history, events and notes now share one compact timeline. Consecutive
identical states from the same boot are linked into one card even when older
records were split by gaps or sampling cadence. Each original segment is kept;
unknown gaps are counted and excluded from estimated coverage. Different states,
reboots and ambiguous/backwards uptime still remain separate.

Show all states displays every linked state card; Show events too restores the
mixed, paginated timeline. Device event rows carry the nearest preceding captured
state from the same boot and its age. Manual entries without captured state remain
explicitly unassociated. Expanded log tables use compact rows, and pagination
controls appear only when there is more than one page of readings.
