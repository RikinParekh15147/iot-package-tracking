# Live ESP32 performance

Open **Performance** in the dashboard, or
http://localhost:8000/?view=performance. The page reads actual stored records
from the ESP32 receiver and updates every three seconds. Select a 300- or
400-record window; if fewer records exist, only those available are shown.

## Receiver setup

By default, the tab reads this server's database. For a device already uploading
to another receiver, save `{"url":"http://RECEIVER_IP:8000"}` in the ignored
`backend/data/performance_source.json`, or set `PERFORMANCE_SOURCE_URL` before
starting the backend. This reads the existing records without reassigning trips
or changing firmware. Only the explicit **Measure live response** action requests
a fresh reading from that receiver. The receiver token defaults to the local
server token; `PERFORMANCE_SOURCE_TOKEN` can override it through the environment.

The connected board's receiver at setup was `http://10.177.148.229:8000`.
The local source file contains only that URL, never a token. Explicit temporary
database apps used by automated tests default to their local test data.

## Reliability counts

- **Receiver history:** the actual count of unique stored records for this device.
- **Selected window:** up to 400 original records retrieved for analysis.
- **Dashboard loaded:** record IDs acknowledged by this browser after the
  Performance page has rendered its charts and records. This does not assert
  that a person inspected every record.
- **Window coverage:** acknowledged IDs / records in the selected window.
- **Sequence coverage:** unique sequences observed / the inclusive sequence span
  between the first and last observed ID for each boot. Earlier IDs outside the
  window are not treated as missing. Different boots are never merged.
- **Sequence gaps:** IDs absent inside those observed spans. Gaps may still be
  queued or delayed; they are not proof of permanent packet loss.
- Queue depth, dropped records and storage errors come from the last telemetry
  record and apply to that device boot. They are not lifetime totals.

The firmware does not expose a complete source-generated/attempted ledger, so
total generated-to-delivered loss percentage cannot be established from the
current records alone. Sensor-health availability reports actual device flags;
it is not detection accuracy against independently labeled physical events.

## Measured latency

The browser measures API round-trip and rendering durations with
`performance.now()`. These include the Performance API's receiver query and
network response transfer. Reported median, mean, p95, p99 and maximum use actual
completed browser requests. Percentiles use linear interpolation at `(n-1)*p`.

**Measure live response** issues the existing live-fetch command to the configured
receiver, then looks for the exact matching `fetch_request_id` in received
telemetry. The server's single monotonic clock measures request-to-observation
time, including heartbeat scheduling, snapshot capture, upload and polling.
Timeouts remain separate and have no successful response-time value. Restart
invalidates pending measurements; it does not mix unrelated clock epochs.

Receiver inter-arrival graphs measure timestamp intervals between stored
records. Batched arrivals can have zero intervals. Throughput uses actual record
count over the observed receipt window. Neither number is a physical-event
detection or capture-to-server latency.

Records currently contain device uptime, not synchronized capture timestamps.
Uptime is never subtracted from server wall time. Physical event-to-display
latency, precision/recall and calibrated sensor errors need independent timing
and reference measurements; the tab does not infer them from ordinary telemetry.

## Storage and exports

Receipt snapshots, browser acknowledgements and timing samples are stored in
separate `performance_*` tables. Device records and trips remain untouched.
CSV and full JSON exports read the latest selected real-record window. They
preserve boot IDs, sequence numbers, uptime, original values and receipt times.
The JSON report also includes the actual window statistics.

Run checks with `python -m pytest backend/test_performance.py -q`.
