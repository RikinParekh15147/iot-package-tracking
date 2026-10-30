# Responsiveness changes

Dashboard refreshes are driven by an authenticated held `/api/v1/changes` request.
Successful API mutations wake listeners after the route returns. Console-only
writes do not trigger expensive trip refreshes. Idle requests return after 2 s
for presence checking and recovery; failures retry after 1 s. The change channel
is process-local, so this prototype must use one uvicorn worker. A multi-worker
cloud deployment needs a shared notification broker before scaling out.

Firmware state changes continue to persist a snapshot and notify the network task
immediately. Successful backlog batches no longer add an artificial 250 ms pause.
Regular connected telemetry and heartbeat/control checks use 1 s rather than 5 s.
Offline periodic telemetry stays at 60 s to conserve the finite flash queue;
state changes/events still queue immediately. Network failures retain exponential
backoff to avoid a busy retry loop. DHT reads use the existing 2.5 s minimum cadence
rather than extending routine reads to 3 s.

The 10 ms IMU sample period, reed debounce, light hysteresis and rule persistence
windows are preserved. Those windows define what counts as a sustained condition;
removing them would change the detector, not just improve reporting speed.
DHT freshness cannot match the IMU rate. Firmware uses a yielding main loop and
keeps sensing separate from the network task.

These are source-level timing choices, not measured latency guarantees. A queued
backlog, network failure, sensor read, filesystem write or rule window can delay
visible updates. Flash retention at 1 s is shorter than at 5 s; validate storage
and power costs under the quantitative plan before long deployments.
