# Guided analysis: what you do and when

Open http://localhost:8000/analysis or choose **Guided analysis** in the dashboard.
The full methodology is in QUANTITATIVE_ANALYSIS_PLAN.md. This guide turns the
first pilot into practical steps. No real trial has been started for you.

## Before the first trial — about 5–10 minutes

1. Put the sensors securely inside an **empty** box. Keep USB power connected.
2. Keep the magnet aligned for a closed lid. Put the LDR inside so closing makes
   it dark and opening exposes it to room light. Check the 10k divider is wired.
3. Confirm the ESP32 can reach the server. The analysis page shows online/offline.
   Wi-Fi settings can be handled later; normal online-response tests need an
   actual server connection. An offline attempt may still be recorded as such.
4. For motion tests, leave the closed box upright and still. In Serial Monitor at
   115200 send `d`, then `c`, then `a`. Verify reference capture and arming succeed.
   The analysis page records observations; it does not remotely arm the ESP32.
5. Keep a phone camera available to record the physical box and screen. For a
   measured tilt angle use a protractor or measured fixture. Without an independent
   reference, label the result a functional check, not calibrated accuracy.

## Create the first pilot — about 2 minutes

- Choose a descriptive session name and the actual device ID.
- Keep **Pilot**, **1 repeat**, and the first three scenarios selected.
- Enter the box/sensor setup and fixed settings. Include the deployed firmware
  identity if known; the export fingerprints source files, which do not by
  themselves prove what is flashed on the device.
- Write the expected behavior and how you will judge a miss/false alert **before**
  you test. Do not silently change these criteria after seeing predictions.
- Describe the independent evidence: visual lid check and video are enough for
  basic opening trials. Note which precision references you do not have.
- Click **Create saved protocol**. The page saves and randomizes the order.

## During each trial — follow the page, usually 20–30 seconds plus recovery

1. Read **Prepare now** and arrange the physical starting condition. Confirm ready.
2. Click **Start recording**. This starts evidence capture, not the physical action.
3. When instructed, click **Mark action**, then do the indicated action. These are
   approximate human markers, not laboratory event timestamps.
4. Hold the condition for the suggested duration. The page shows a timer and
   received-record count. It never auto-finishes because recovery can take longer.
5. Click **Mark return / restore** as you return to the normal condition.
6. Add a note for any interruption, unexpected response or uncertain action.
7. Write what physically happened. Choose your assessment, leaving **Inconclusive**
   if you cannot decide. Record deviations and reference values where available.
8. Click **Finish & preserve evidence**. Review the predictions and originals after
   recording your physical observation; this reduces confirmation bias.
9. Prepare the next trial. Let the box return to normal between conditions. Inspect
   reference/arming after a restart or sensor fault; do not mix a fault with a
   successful trial without recording it.

The initial scenarios check closed/still, normal carrying and opening in light.
Later create a separate pilot with opening in darkness, light through a closed
packaging gap, sustained tilt, repeatable vibration and offline recovery. Select
only what your physical setup can actually reproduce. Never treat a light leak
as proof of malicious tampering or a threshold impact as proof of damage.

## Offline recovery — only when you deliberately select that trial

Keep the browser/server connected. Disable only the ESP32's network, mark the
action and perform a known number of lid changes. Restore that network, mark
return, and wait for the queued records to arrive **before finishing**. Record the
number/order of your actions independently. If the board cannot reconnect, finish
as inconclusive or aborted and retain that attempt rather than deleting it.

## After the pilot

Download **complete evidence JSON** and keep its matching video/reference notes.
The page preserves trial order, frozen setup/criteria, server timestamps, markers,
operator observations, boot/sequence IDs, original telemetry/events and the raw
console lines available at finish. Console history is bounded by the existing
server retention; it is not an unlimited serial recorder. Trial evidence is
stored independently of cargo trip assignment and does not modify trip logs.

Review the method first: did you perform the intended actions, obtain fresh
readings, distinguish faults and keep an independent reference? Pilot results
can guide threshold changes, but those trials then stay **pilot data**.

For evaluation, freeze the final settings and protocol, decide independent trial
counts and acceptable errors/delays, then create a new **Evaluation** session.
Do not call one practice repeat a statistically meaningful accuracy estimate.
Use QUANTITATIVE_ANALYSIS_PLAN.md for the final trial design and metrics.

## What the screen does not claim

- Counts of operator outcomes are not independently verified accuracy scores.
- Receipt-window membership is not proof of physical capture during the trial.
  Delayed offline uploads can be included; arrivals after finish are excluded.
  Raw boot/sequence/uptime and baseline records allow later reconciliation.
- Server receipt minus a human marker is not physical end-to-end latency. A
  synchronized reference or suitable video/instrumentation is needed for that.
- A session source fingerprint records files at setup, not deployed-build proof.
- Temperature/humidity accuracy, calibrated shock amplitude, calibrated vibration,
  battery life and network capacity require separate reference equipment or
  planned procedures; this initial UI does not manufacture those measurements.
