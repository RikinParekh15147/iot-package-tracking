# Quantitative evaluation plan

Guided pilot workbench: http://localhost:8000/analysis. Follow ANALYSIS_START_HERE.md for physical steps and when to act. The workbench records protocol, markers, operator observations, original record snapshots and available console lines; it does not infer calibrated accuracy or physical latency automatically.

Status: planned only. No experimental results, sensor accuracy claims, or performance claims are established by this document. Software regression tests are separate from this evaluation.

## Questions to answer

1. How quickly does a physical change become a local prediction, an acknowledged server record, and a visible dashboard state?
2. Which physical conditions can this assembled prototype distinguish, and which conditions produce misses or false alerts?
3. What records survive network loss, server failure, and restart, and how quickly does the backlog recover?
4. What resources and storage does this responsiveness require?
5. Does the simplified trip timeline preserve the meaning and membership of the original readings?

## Rules to keep the evaluation independent

- Write down hypotheses, ground-truth definitions, trial count, pass criteria, exclusions and scoring rules before collecting evaluation data. Targets must come from the intended cargo requirements; do not choose them after seeing results.
- Test the actual hardware and implemented rules. Do not assume a sensor, component label, or an IoT architecture guarantees accuracy.
- Keep threshold-setting/calibration trials separate from final evaluation trials. Freeze firmware, server version, enclosure, sensor mounting and thresholds for final trials.
- Randomize trial order within practical blocks. Include normal handling, near-threshold cases, conflicting sensor evidence and failures, not only obvious demonstrations.
- Store every attempted trial, including failed uploads, unavailable sensors, timeouts and inconclusive outcomes. Report them separately and include them in appropriate denominators.
- Use an independent physical reference or event marker, not the system's own output, to label the expected condition. Where practical, have someone label the reference without seeing the prediction.
- Repeat trials across sessions and orientations. A single ESP32 prototype establishes results for that assembly; repeated readings are not independent devices or trials.
- Report uncertainty and limitations alongside estimates. Never substitute screenshots, passing unit tests, or selected examples for experimental evidence.

## Proposed measurements

| Area | Procedure and independent reference | Report |
| --- | --- | --- |
| End-to-end response | Timestamp physical stimulus, local rule recognition, record capture, server acknowledgement and dashboard display. Use shared-clock instrumentation or video showing stimulus and screen; document clock alignment error. | Median, p95, maximum and distribution by state; failed/timeout trial count. Separate deliberate persistence windows from transport delay. |
| Detection correctness | Label physical lid position, light exposure, imposed orientation and controlled handling independently. Score each supported state/rule, including simultaneous states and unavailable inputs. | Confusion counts, precision, recall, F1 where defined; false alerts per normal-handling hour and misses per trial. Show class counts; avoid overall accuracy alone. |
| Tilt | Compare sensor estimate with independently measured enclosure angle in both directions and multiple orientations, with reference capture repeated across sessions. | Signed bias, MAE, largest absolute error; detection probability versus angle and dwell time. |
| Impact | Apply repeatable, non-destructive stimuli with fixed mounting. If a sufficiently fast calibrated reference is available, compare synchronized peaks. | Detection rate and repeatability versus reference stimulus; saturation and missed-sample counts. Without a suitable reference, report trigger repeatability, not impact accuracy or damage severity. |
| Vibration | Apply reproducible sustained motion with documented settings; include stillness, ordinary carrying and isolated knocks as controls. Use independent vibration reference if measuring amplitude/frequency accuracy. | Trigger/miss rates, false alerts, recognition delay and recovery delay; reference comparisons only where supported. |
| Lid and light evidence | Independently vary magnet proximity/lid position and illumination: closed/dark, open/bright, open/dark, closed/bright, gradual light, magnet alignment changes. | Full evidence-combination table and error counts. Reed state alone is not proof of opening; light exposure is not proof of tampering. |
| Temperature/humidity | Co-locate with a suitable reference across stable conditions and transitions, recording settling time and sensor age. | Bias, MAE, repeatability and response lag; state reference instrument uncertainty. Do not claim traceable accuracy without a traceable reference. |
| Offline retention | Capture known numbered events during Wi-Fi loss, server outage and device restart, then restore connectivity. Exercise near-full/full storage and interrupted uploads. | Expected/captured/acknowledged counts, loss and duplicate rates, order integrity, queue size, reconnect delay and backlog drain rate. Explain overflow policy. |
| Wi-Fi selection | Use saved networks with controlled availability, including a network with Wi-Fi association but no server route. | Selection correctness, time to server reachability, unnecessary switching count, queued-record preservation. |
| API/dashboard capacity | Later exercise documented device/client counts and representative trip histories, including large queues. | Request duration distribution, errors, CPU/RAM, database size, browser responsiveness. Label synthetic load separately from physical-device evidence. |
| Power/storage cost | Measure supply current with an instrument; compare idle, sensing, connected uploading and reconnecting. Count stored bytes and writes. | Measured current/energy, records/bytes per hour and queue retention. Battery life is only an estimate unless an actual discharge test is performed. |
| Trip/history integrity | Use a known ledger across trip completion, next trip, delayed upload, reboot and same-state grouping. | Wrong-trip/orphan counts; raw-record membership, state-transition preservation, and counts before/after grouping. Movement changes must remain inspectable in originals. |

## Trial design

First define an intended use case and allowable delays/error rates. A fragile-electronics demonstration and a temperature-sensitive laboratory shipment do not automatically share acceptance criteria.

Use a small separate pilot to discover wiring/procedure problems and estimate variability. Then determine final independent trial counts from desired confidence/precision and available time; do not declare an arbitrary number of logs to be statistical replication. For proportions, report binomial confidence intervals. For latency, report per-trial observations and uncertainty with the number of independent trials; do not treat thousands of samples from one event as thousands of events.

Suggested factors: armed/disarmed, normal/abnormal handling, stimulus magnitude and duration, orientation, light background, reference validity, sensor faults, healthy/weak/no Wi-Fi, server available/unavailable, empty/full queue, and single/overlapping conditions. Define the expected result for each combination before the trial.

Some firmware labels have precedence: one condition headline may mask another simultaneous condition. Score raw event flags/evidence as well as the chosen headline. Keep unavailable/unknown separate from normal. Interpret "likely opened" as an inference; do not equate it with intent, theft, a smashed package or proven damage.

## Data to retain for every trial

Trial ID, randomized order, date/session, firmware/server commit, thresholds, wiring/mounting/enclosure, armed/reference status, independent expected condition, stimulus start/end, ground-truth instrument and uncertainty, boot/sequence/trip IDs, original payloads, rule trigger time, server receipt/ack time, display time, sensor age, Wi-Fi state/RSSI, queue depth, dropped/storage-error/missed-sample counts, and procedural deviations.

Do not directly subtract ESP uptime from server wall time. Clock synchronization, reboot epochs and offline receipt delays must be accounted for. Screen receipt time does not establish when a queued event physically occurred.

## Comparison and presentation

Compare the saved prior build and optimized build using the same randomized scenarios, hardware and network conditions. Keep detection thresholds identical for a transport-speed comparison. Present speed, false alerts, record loss, resource cost and sensor freshness together so a faster configuration is not automatically called better.

Planned deliverables: scenario matrix, frozen protocol, complete trial ledger, latency distributions, confusion matrices, false-alert rate plot, offline queue/recovery plot, reference-versus-sensor plots where supported, resource-cost table and explicit limitations. Report unfavorable and inconclusive results as well as favorable ones.

## Execution order (not performed yet)

1. Verify hardware, references, logging instrumentation and deployed build identity.
2. Define use-case requirements and expected labels; prepare protocol and separate pilot.
3. Freeze settings; collect the randomized final trials and controls.
4. Validate completeness, then compute the prespecified metrics and uncertainty.
5. Interpret only within the tested assembly and conditions.
