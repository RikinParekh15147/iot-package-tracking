'use strict';
const paths={
 box:'<path d="m12 3 9 5v9l-9 5-9-5V8l9-5Z M3 8l9 5 9-5 M12 13v9 M7.5 5.5l9 5v4"/>',
 grid:'<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
 route:'<circle cx="5" cy="5" r="2"/><circle cx="19" cy="19" r="2"/><path d="M7 5h8a4 4 0 0 1 0 8H9a4 4 0 0 0 0 8h8"/>',
 list:'<path d="M8 5h13M8 12h13M8 19h13M3 5h.01M3 12h.01M3 19h.01"/>',
 chip:'<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v4m6-4v4M9 18v4m6-4v4M2 9h4m-4 6h4m12-6h4m-4 6h4"/><rect x="10" y="10" width="4" height="4"/>',
 pin:'<path d="M19 10c0 5-7 11-7 11S5 15 5 10a7 7 0 1 1 14 0Z"/><circle cx="12" cy="10" r="2"/>',
 arrow:'<path d="M4 12h16m-6-6 6 6-6 6"/>',plus:'<path d="M12 5v14M5 12h14"/>',check:'<path d="m5 12 4 4L19 6"/>',
 download:'<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',wifi:'<path d="M2 8a16 16 0 0 1 20 0M5 12a11 11 0 0 1 14 0m-11 4a6 6 0 0 1 8 0M12 20h.01"/>',
 alert:'<path d="m12 3 10 18H2L12 3Zm0 6v5m0 3h.01"/>',temp:'<path d="M9 14V5a3 3 0 0 1 6 0v9a5 5 0 1 1-6 0Zm3-7v10"/>',
 drop:'<path d="M12 2s-7 8-7 13a7 7 0 0 0 14 0c0-5-7-13-7-13Z"/>',tilt:'<path d="M3 21h18M5 15l5-12 12 5-5 12-12-5Z"/>',
 clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',search:'<circle cx="10" cy="10" r="6"/><path d="m15 15 6 6"/>',
 settings:'<path d="M4 6h16M4 12h16M4 18h16"/><circle cx="9" cy="6" r="2"/><circle cx="16" cy="12" r="2"/><circle cx="8" cy="18" r="2"/>',close:'<path d="m6 6 12 12M6 18 18 6"/>'
};
const icon=name=>`<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${paths[name]||paths.list}</svg>`;
document.querySelectorAll('[data-icon]').forEach(el=>el.innerHTML=icon(el.dataset.icon));
const $=selector=>document.querySelector(selector);
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=(value,opts={day:'numeric',month:'short',year:'numeric'})=>value?new Date(value).toLocaleDateString(undefined,opts):'—';
const clock=value=>value?new Date(value).toLocaleTimeString(undefined,{hour:'2-digit',minute:'2-digit'}):'—';
const stamp=value=>value?`${fmt(value)} · ${clock(value)}`:'Not received';
const ago=value=>{if(!value)return 'Not received';const s=Math.max(0,Math.floor((Date.now()-new Date(value))/1000));return s<60?`${s}s ago`:s<3600?`${Math.floor(s/60)}m ago`:s<86400?`${Math.floor(s/3600)}h ago`:`${Math.floor(s/86400)}d ago`};
const badge=status=>`<span class="badge ${esc(status)}">${status==='active'?'<span class="live-dot online"></span>':''}${({active:'In transit',completed:'Delivered',cancelled:'Cancelled'})[status]||esc(status)}</span>`;
let savedToken;try{savedToken=sessionStorage.getItem('waypost-token')}catch{}
const state={view:'overview',token:savedToken||'dev-token-change-me',trips:[],devices:[],selected:null,detail:null,search:'',status:'all',kind:'all',offset:0,unassigned:0,stale:false,loaded:false,live:null,showLive:false,fetchBusy:false,fetchRequestId:null,runOffset:0,expandedRun:null,expandedData:null,memberOffset:0,allStates:false};
const requestedTrip=new URLSearchParams(location.search).get('trip');if(requestedTrip){state.selected=requestedTrip;state.view='trip'}
let refreshing=false,queued=false,toastTimer,modalTrip=null;
$('#today').textContent=fmt(new Date());
async function api(path,options={}){
 const response=await fetch('/api/v1'+path,{...options,headers:{'X-Device-Token':state.token,...(options.body?{'Content-Type':'application/json'}:{}),...options.headers},cache:'no-store',signal:AbortSignal.timeout(path.startsWith('/changes?')?25000:8000)});
 if(!response.ok){let data;try{data=await response.json()}catch{};const failure=Error(response.status===401?'Device token rejected. Open Connection settings to reconnect.':typeof data?.detail==='string'?data.detail:`Request failed (${response.status}). Please try again.`);failure.status=response.status;throw failure}
 return response;
}
const json=async(path,options)=>(await api(path,options)).json();
const post=(path,body)=>json(path,{method:'POST',body:JSON.stringify(body)});
function notify(message){clearTimeout(toastTimer);$('#toast').textContent=message;$('#toast').hidden=false;toastTimer=setTimeout(()=>$('#toast').hidden=true,4500)}
function error(message){$('#error-banner').textContent=message;$('#error-banner').hidden=!message}
async function refresh(){
 if(refreshing){queued=true;return}refreshing=true;
 const token=state.token;
 try{
  const [trips,devices]=await Promise.all([json('/trips'),json('/devices')]);
  if(token!==state.token){queued=true;return}
  state.trips=trips.trips;state.devices=devices.devices;state.unassigned=trips.unassigned_records;
  if((state.view!=='trip'||!state.selected)&&!state.trips.some(t=>t.id===state.selected))state.selected=state.trips.find(t=>t.status==='active')?.id||state.trips[0]?.id||null;
  const selected=state.selected, view=state.view, kind=state.kind, offset=state.offset,expandedRun=state.expandedRun,memberOffset=state.memberOffset;
  const detail=selected?await json(`/trips/${encodeURIComponent(selected)}?`+new URLSearchParams({kind:view==='trip'?'other':view==='activity'?kind:'all',offset:String(['activity','trip'].includes(view)?offset:0),limit:'50'})):null;
  let expandedData=null;
  if(expandedRun&&detail?.state_runs.some(r=>r.id===expandedRun)){try{expandedData=await json(`/trips/${encodeURIComponent(selected)}/state-runs/${encodeURIComponent(expandedRun)}?`+new URLSearchParams({offset:String(memberOffset),limit:'20'}))}catch(err){if(err.status===404){state.expandedRun=null;state.expandedData=null;queued=true;return}throw err}}
  const deviceId=detail?.trip.device_id||devices.devices[0]?.device_id||'cargo-esp32-01';
  const live=await json('/latest?'+new URLSearchParams({device_id:deviceId}));
  if(token!==state.token||selected!==state.selected||view!==state.view||kind!==state.kind||offset!==state.offset||expandedRun!==state.expandedRun||memberOffset!==state.memberOffset){queued=true;return}
  state.live=live;state.expandedData=expandedData;
  if(expandedRun&&!detail?.state_runs.some(r=>r.id===expandedRun))state.expandedRun=null;
  if(state.fetchRequestId && live.record?.data.fetch_request_id===state.fetchRequestId && !live.request){state.fetchRequestId=null;notify('Fresh device reading received')}
  if($('#wifi-dialog').open)wifiStatus(await json('/wifi?'+new URLSearchParams({device_id:$('#wifi-form').elements.device_id.value})));
  state.detail=detail;state.loaded=true;state.stale=false;error('');
  $('#sync-status').textContent='Updated '+clock(new Date())+' · live updates';
  if(!$('#content').contains(document.activeElement)||!['INPUT','SELECT','TEXTAREA'].includes(document.activeElement.tagName))render();
 }catch(err){state.stale=true;error(err.message);$('#sync-status').textContent='Server unavailable · displayed history may be outdated';render()}
 finally{refreshing=false;if(queued){queued=false;refresh()}}
}
function currentDevice(){return state.devices.find(d=>d.device_id===state.detail?.trip.device_id)||state.devices[0]||{device_id:'cargo-esp32-01',connected:false}}
function stats(){const active=state.trips.filter(t=>t.status==='active').length,done=state.trips.filter(t=>t.status==='completed').length,online=state.stale?0:state.devices.filter(d=>d.connected).length;return `<div class="stats">${[
 ['Total trips',state.trips.length,'Every shipment, in one place','box'],['In transit',active,active?'Journeys currently underway':'Ready for your next shipment','route'],['Delivered',done,'Completed journeys, saved safely','check'],['Devices online',state.stale?'—':online,state.stale?'Connection check unavailable':'Live Wi-Fi heartbeat status','wifi']
 ].map(([title,value,caption,i])=>`<article class="stat"><div class="stat-heading">${title}<span class="stat-icon">${icon(i)}</span></div><div class="stat-value">${value}</div><div class="stat-caption">${caption}</div></article>`).join('')}</div>`}
function routeArt(){return `<svg class="empty-route-art" viewBox="0 0 180 110" aria-hidden="true"><path d="M12 85H69a18 18 0 0 0 0-36H54a18 18 0 0 1 0-36h95" fill="none" stroke="#d4e2d2" stroke-width="2" stroke-dasharray="4 5"/><circle cx="12" cy="85" r="7" fill="#edf3e7" stroke="#a5bd9a" stroke-width="2"/><path d="m144 5 19 10v21l-19 10-19-10V15Z" fill="#f2dcc2" stroke="#cbab84" stroke-width="1.5"/><path d="m125 15 19 10 19-10m-19 10v21m-9-36 19 10v9" fill="none" stroke="#cbab84" stroke-width="1.5"/><circle cx="77" cy="84" r="20" fill="#f0f5eb"/><path d="M77 68a10 10 0 0 0-10 10c0 7 10 16 10 16s10-9 10-16a10 10 0 0 0-10-10Z" fill="#dce9d6" stroke="#8ba981" stroke-width="1.5"/><circle cx="77" cy="78" r="3" fill="white"/></svg>`}
function mapLink(location){return `<a href="https://www.google.com/maps/search/?api=1&amp;query=${encodeURIComponent(location)}" target="_blank" rel="noopener noreferrer">Open in Maps ${icon('arrow')}</a>`}
function tripSelect(){return state.trips.length?`<select class="selected-trip" aria-label="Select trip" id="trip-select">${state.trips.map(t=>`<option value="${esc(t.id)}" ${t.id===state.selected?'selected':''}>${esc(t.name)}</option>`).join('')}</select>`:''}
function routePanel(){const detail=state.detail,t=detail?.trip;if(!t)return `<section class="panel"><div class="panel-head"><h2>Your next journey</h2><span class="badge">Ready to dispatch</span></div><div class="route-empty"><div class="empty-kicker">FROM HERE TO THERE</div><h3>Every shipment starts<br>with a destination.</h3><p>Create your first trip to bring its route, checkpoints and device activity together.</p><button class="button primary" data-action="new-trip">Create your first trip ${icon('arrow')}</button>${routeArt()}</div></section>`;
return `<section class="panel"><div class="panel-head"><h2>${t.status==='active'?'Current journey':'Trip history'}</h2>${badge(t.status)}</div><div class="route-body"><div class="trip-title-row"><div><h3 class="trip-title">${esc(t.name)}</h3><div class="trip-meta"><span>${icon('clock')} ${esc(stamp(t.started_at))}</span><span>${esc(t.cargo||'Cargo not specified')}</span></div></div><span class="trip-reference">WP-${esc(t.id.slice(0,6).toUpperCase())}</span></div><div class="route-display"><div class="route-location"><div class="route-label">${icon('pin')} PICKUP</div><strong>${esc(t.origin)}</strong>${mapLink(t.origin)}</div><div class="route-connector">${icon('route')}</div><div class="route-location"><div class="route-label">${icon('pin')} DESTINATION</div><strong>${esc(t.destination)}</strong>${mapLink(t.destination)}</div></div><div class="trip-actions">${t.status==='active'?`<button class="button primary" data-action="checkpoint">${icon('pin')} Add checkpoint</button>`:''}<button class="button secondary" data-action="note">${icon('plus')} Add note</button>${t.status==='active'?'<button class="button secondary" data-action="finish">Finish trip</button>':''}<button class="button secondary" data-action="export">${icon('download')} Export</button></div><div class="route-foot">${icon('pin')} Manual route · ${detail.checkpoints.length} checkpoint${detail.checkpoints.length===1?'':'s'} · ${detail.alert_count} alert${detail.alert_count===1?'':'s'}${detail.handling_summary?.max_impact_g!=null?' · Largest recorded impact '+Number(detail.handling_summary.max_impact_g).toFixed(1)+' g':''}</div></div>${detail.checkpoints.length?`<div class="checkpoint-list">${detail.checkpoints.map(c=>`<div class="checkpoint-row">${icon('pin')}<div><strong>${esc(c.location)}</strong><small>${esc(stamp(c.created_at))} · ${esc(c.message)}</small></div></div>`).join('')}</div>`:''}${t.recipient||t.notes?`<div class="trip-detail-notes">${t.recipient?`<strong>Recipient:</strong> ${esc(t.recipient)}<br>`:''}${esc(t.notes)}</div>`:''}</section>`}
function devicePanel(d=currentDevice()){
const online=!state.stale&&d.connected;const firmware=d.firmware==='connection-test'?'Connection test':d.firmware==='cargo-monitor'?'Cargo monitor':d.firmware||'Awaiting device details';
return `<section class="panel device-panel"><div class="panel-head"><h2>Device health</h2><button class="text-button" data-view="device">${icon('arrow')}</button></div><div class="device-body"><div class="device-visual"><div class="chip-art">${icon('chip')}</div><div><div class="device-name">${esc(d.device_id)}</div><div class="device-sub">ESP32 · ${esc(firmware)}</div></div></div><div class="connection-row"><span>Connection</span><span class="badge ${online?'connected':''}"><span class="live-dot ${online?'online':''}"></span>${state.stale?'Unknown':online?'Connected':'Offline'}</span></div><dl class="device-facts"><div><dt>Last heartbeat</dt><dd>${esc(ago(d.last_seen||d.last_received_at))}</dd></div><div><dt>Wi-Fi signal</dt><dd>${typeof d.rssi==='number'?`${d.rssi} dBm`:'Not reported'}</dd></div><div><dt>Device IP</dt><dd>${esc(d.ip||'Not reported')}</dd></div><div><dt>Uptime</dt><dd>${typeof d.uptime_ms==='number'?`${Math.floor(d.uptime_ms/3600000)}h ${Math.floor(d.uptime_ms/60000)%60}m`:'Not reported'}</dd></div></dl><div class="device-notice">${d.firmware==='cargo-monitor'?'Sensor values appear as the device uploads records. Location checkpoints are entered manually.':'Connection test is active. Trip activity is live; sensor readings need the cargo-monitor firmware.'}</div><div class="device-settings-actions"><button class="button secondary" data-action="device-wifi" data-device="${esc(d.device_id)}">${icon('wifi')} Wi-Fi networks</button><a class="button secondary" href="/console?${new URLSearchParams({device_id:d.device_id})}" target="_blank" rel="noopener">ESP raw logs</a></div></div></section>`}
function sparkline(key,valid){const values=(state.detail?.telemetry||[]).filter(t=>t.data[valid]!==false&&typeof t.data[key]==='number').map(t=>t.data[key]);if(values.length<2)return '';const min=Math.min(...values),max=Math.max(...values),range=max-min||1;return `<svg class="sparkline" viewBox="0 0 80 30" aria-label="Recent readings"><polyline points="${values.map((v,i)=>`${(i*78/(values.length-1)+1).toFixed(1)},${(27-(v-min)/range*24).toFixed(1)}`).join(' ')}"/></svg>`}
function consoleLink(){const q=new URLSearchParams({device_id:currentDevice().device_id});if(state.detail?.trip.id&&!state.showLive)q.set('trip_id',state.detail.trip.id);return '/console?'+q}
function mpuPanel(d){
 const num=(key,unit)=>d.imu_ok===false||!Number.isFinite(d[key])?'—':d[key].toFixed(3)+' '+unit;
 const gyroRaw=!d.reference_valid;
 const fields=[['Acceleration X',num('ax_g','g')],['Acceleration Y',num('ay_g','g')],['Acceleration Z',num('az_g','g')],['Total acceleration',num('total_g','g')],['Gyro X',num(gyroRaw?'raw_gx_rad_s':'gx_rad_s','rad/s')],['Gyro Y',num(gyroRaw?'raw_gy_rad_s':'gy_rad_s','rad/s')],['Gyro Z',num(gyroRaw?'raw_gz_rad_s':'gz_rad_s','rad/s')],['Vibration RMS',num('vibration_rms_g','g')]];
 return `<section class="panel mpu-panel"><div class="panel-head"><h2>MPU6050 readings</h2><span class="badge">${d.imu_ok===false?'Sensor unavailable':d.imu_ok?'Reading':'Waiting for data'}</span></div><dl class="mpu-readings">${fields.map(([label,value])=>`<div><dt>${label}</dt><dd>${value}</dd></div>`).join('')}</dl><p class="mpu-note">${d.reference_valid?'Gyro values corrected for stationary bias.': 'Orientation reference needed; gyro values are raw when available.'} Stationary total acceleration should be near 1 g. <a href="${consoleLink()}" target="_blank" rel="noopener">ESP raw logs</a></p></section>`;
}
function sensors(){const telemetry=state.detail?.telemetry||[],useLive=state.showLive||!state.detail,latest=useLive?state.live?.record:telemetry.at(-1),d=latest?.data||{};
const waiting=Boolean(state.live?.request),freshness=waiting?'Waiting for a fresh device reading…':!latest?'No readings received':`Received ${stamp(latest.received_at)} · ${ago(latest.received_at)}`;

const value=(key,valid)=>d[valid]===false||typeof d[key]!=='number'?'—':d[key].toFixed(1);
return `<div class="section-label"><div><h2>${useLive?'Live device snapshot':'Cargo conditions'}</h2><span>${esc(freshness)}${useLive?' · '+esc(state.live?.device_id||currentDevice().device_id):''}</span></div><button class="button secondary" data-action="fetch-live" ${waiting||state.fetchBusy?'disabled':''}>${icon('download')}${waiting?'Waiting for device…':state.fetchBusy?'Requesting…':'Fetch live data'}</button></div><div class="sensors">${[
 ['Temperature','temp',value('temperature_c','dht_ok'),'°C',d.dht_ok===false?'Sensor unavailable':latest?'Latest uploaded reading':'Waiting for sensor data','temperature_c','dht_ok'],
 ['Humidity','drop',value('humidity_pct','dht_ok'),'% RH',d.dht_ok===false?'Sensor unavailable':latest?'Latest uploaded reading':'Waiting for sensor data','humidity_pct','dht_ok'],
 ['Package tilt','tilt',d.reference_valid===false?'—':value('tilt_deg','imu_ok'),'°',d.imu_ok===false?'Sensor unavailable':d.reference_valid===false?'Reference not calibrated':latest?'Relative to calibrated position':'Waiting for sensor data','tilt_deg','imu_ok'],
 ['Seal assessment','box',({likely_closed:'Likely closed',opening_suspected:'Opening suspected',likely_opened:'Likely opened',possible_packaging_separation:'Possible packaging gap'})[d.seal_state]||'—','',d.seal_evidence||'Waiting for sensor evidence',null,null],
 ['Handling state','alert',({normal:'Normal',motion_sensor_unavailable:'Sensor unavailable',recent_impact:'Recent impact',strong_vibration:'Strong vibration',tilted:'Tilted',orientation_uncalibrated:'Reference needed',unsafe_environment:'Unsafe environment'})[d.condition_state]||'—','',d.condition_state==='recent_impact'?'Impact detected; inspect cargo':d.movement_state?`Movement: ${d.movement_state.replaceAll('_',' ')}`:'Waiting for sensor data',null,null],
 ['Internal light','alert',d.ldr_enabled===false?'Disabled':Number.isFinite(d.ldr_raw)?String(d.ldr_raw):'—',Number.isFinite(d.ldr_raw)&&d.ldr_enabled!==false?'/ 4095':'',d.ldr_enabled===false?'Enable after wiring the LDR divider':typeof d.light_exposed==='boolean'?(d.light_exposed?'Light exposure detected':'Low internal light'):Number.isFinite(d.ldr_raw)?'Raw ADC reading · not lux':'Waiting for LDR data',null,null],
 ['Lid status','box',typeof d.lid_open==='boolean'?(d.lid_open?'Open':'Closed'):'—','',typeof d.armed==='boolean'?(d.armed?'Protection armed':'Protection disarmed'):'Waiting for sensor data',null,null]
].map(([title,i,val,unit,caption,key,valid])=>`<article class="sensor"><div class="sensor-heading">${icon(i)}${title}</div><div class="sensor-value">${val} <small>${unit}</small></div><div class="sensor-caption">${caption}</div>${key?sparkline(key,valid):''}</article>`).join('')}</div>`+mpuPanel(d)}
function tripTable(trips,full=false){return trips.length?`<div class="table-wrap"><table><thead><tr><th>SHIPMENT / ROUTE</th><th>STATUS</th>${full?'<th>DEVICE</th>':''}<th class="table-date">STARTED</th></tr></thead><tbody>${trips.map(t=>`<tr class="trip-row ${t.id===state.selected?'selected':''}"><td><button class="trip-link" data-trip="${esc(t.id)}">${esc(t.name)}</button><div class="table-sub" title="${esc(t.origin+' → '+t.destination)}">${esc(t.origin)} → ${esc(t.destination)}</div></td><td>${badge(t.status)}</td>${full?`<td>${esc(t.device_id)}<div class="table-sub">${t.record_count+t.log_count} activity entries</div></td>`:''}<td class="table-date">${esc(fmt(t.started_at,{day:'numeric',month:'short'}))}</td></tr>`).join('')}</tbody></table></div>`:`<div class="empty-table">${icon('box')}<strong>${state.trips.length?'No matching trips':'Your trips will appear here'}</strong>${state.trips.length?'Try another search or status.':'Start a shipment to keep its entire journey in one place.'}</div>`}
function timeline(entries,full=false){if(!entries.length)return `<div class="timeline-empty"><strong>${state.selected?'No activity to show':'A story for every shipment'}</strong>${state.selected?'No entries match this view yet.':'Checkpoints, notes and device events will build a timeline as your trip unfolds.'}</div>`;return `<div class="timeline">${entries.map(e=>{const kindIcon=({checkpoint:'pin',note:'list',started:'route',completed:'check',cancelled:'close',heartbeat:'wifi',device_online:'wifi',alert:'alert'})[e.kind]||'chip';return `<div class="timeline-item ${e.kind==='alert'?'alert':''}"><span class="timeline-icon">${icon(kindIcon)}</span><button class="event-detail" data-entry="${esc(e.id)}" aria-label="View activity: ${esc(e.message)}"><strong>${esc(e.message)}</strong><p>${esc(e.location||({heartbeat:'Device presence · sampled once a minute',device_online:'Live Wi-Fi heartbeat',started:'Journey created',completed:'Trip history saved',note:'Manual note',checkpoint:'Manual location update'})[e.kind]||'Device record · receipt time')}${full?' · '+esc(fmt(e.created_at)):''}</p></button><time datetime="${esc(e.created_at)}">${esc(clock(e.created_at))}</time></div>`}).join('')}</div>`}
function overview(){return stats()+`<div class="section-label"><h2>${state.selected?'Journey at a glance':'Dispatch center'}</h2>${tripSelect()}</div><div class="hero-grid">${routePanel()}${devicePanel()}</div>`+sensors()+`<div class="lower-grid"><section class="panel"><div class="panel-head"><h2>Recent trips</h2><button class="text-button" data-view="trips">View all ${icon('arrow')}</button></div>${tripTable(state.trips.slice(0,5))}</section><section class="panel"><div class="panel-head"><h2>Latest activity</h2><button class="text-button" data-view="activity">Full log ${icon('arrow')}</button></div>${timeline((state.detail?.activity||[]).slice(0,5))}</section></div>${state.unassigned?`<div class="info-panel"><h2>${state.unassigned} earlier device records</h2><p>These records arrived without a trip ID and remain outside trip histories. <a href="/records" target="_blank" rel="noopener">View the raw records</a>.</p></div>`:''}`}
const stateNames={likely_closed:'Likely closed',opening_suspected:'Opening suspected',likely_opened:'Likely opened',possible_packaging_separation:'Possible packaging gap',normal:'Normal',motion_sensor_unavailable:'Motion sensor unavailable',orientation_uncalibrated:'Reference needed',recent_impact:'Recent impact',strong_vibration:'Strong vibration',tilted:'Tilted',unsafe_environment:'Unsafe environment',moving:'Moving',still:'Still'};
function stateBadges(prediction={}){const values=[...new Set(['seal_state','condition_state','movement_state'].map(k=>prediction[k]).filter(v=>v&&v!=='unknown'))];return (values.length?values:['unknown']).map(v=>`<span class="state-tag ${v==='unknown'?'unknown':['opening_suspected','likely_opened','possible_packaging_separation','recent_impact','strong_vibration','unsafe_environment','motion_sensor_unavailable','orientation_uncalibrated'].includes(v)?'warning':''}">${esc(stateNames[v]|| (v==='unknown'?'State not recorded':v.replaceAll('_',' ')))}</span>`).join('')}
function durationText(ms){const seconds=Math.max(0,Number(ms)||0)/1000;if(seconds<60)return `${Number(seconds.toFixed(1))} s`;const minutes=Math.floor(seconds/60),rest=Number((seconds%60).toFixed(1));return `${minutes} min${rest?' '+rest+' s':''}`}
function runRecords(run){
 const data=state.expandedData;if(!data||data.run.id!==run.id)return '<p class="state-loading">Loading readings…</p>';
 const reading=d=>[['T',d.dht_ok===false?null:d.temperature_c,'°C'],['RH',d.dht_ok===false?null:d.humidity_pct,'%'],['Accel',d.imu_ok===false?null:d.total_g,'g'],['Light',d.ldr_enabled===false?null:d.ldr_raw,'ADC']].filter(([,v])=>Number.isFinite(v)).map(([label,value,unit])=>`${label} ${Number(value.toFixed(2))}${unit}`).join(' · ')||'No numeric readings';
 const prediction=d=>[...new Set(['seal_state','condition_state','movement_state'].map(k=>d[k]).filter(v=>v&&v!=='unknown'))].map(v=>stateNames[v]||v.replaceAll('_',' ')).join(' / ')||'State not recorded';
 return `<div class="table-wrap state-samples"><table><thead><tr><th>DEVICE TIME</th><th>READING / STATE</th><th></th></tr></thead><tbody>${data.records.map(r=>`<tr><td>${durationText(r.uptime_ms)} <small>Sequence ${r.seq}</small></td><td><div>${esc(reading(r.data))}</div><small class="sample-prediction" title="${esc(stamp(r.received_at))}">${esc(prediction(r.data))}</small></td><td><button class="text-button" data-run-record="${r.id}">Raw JSON</button></td></tr>`).join('')}</tbody></table></div>${data.total>data.limit?`<div class="pagination"><span>${data.offset+1}–${data.offset+data.records.length} of ${data.total} readings</span><div><button class="button secondary" data-run-member-page="prev" ${data.offset===0?'disabled':''}>Previous readings</button> <button class="button secondary" data-run-member-page="next" ${data.offset+data.limit>=data.total?'disabled':''}>Next readings</button></div></div>`:''}`;
}
function stateRunCard(run){
 const expanded=state.expandedRun===run.id,gaps=run.gap_count||0,prediction=run.state||{};
 const condition=prediction.condition_state,seal=prediction.seal_state;
 const hazards=['recent_impact','strong_vibration','tilted','unsafe_environment'];
 const primary=run.primary_state||(hazards.includes(condition)?condition:(seal&&seal!=='unknown'?seal:'unknown'));
 const tone=['recent_impact','strong_vibration','unsafe_environment'].includes(primary)?'danger':['likely_opened','opening_suspected','possible_packaging_separation','tilted'].includes(primary)?'caution':primary==='likely_closed'?'safe':'neutral';
 const diagnostic=['orientation_uncalibrated','motion_sensor_unavailable'].includes(condition)?stateNames[condition]:'';
 return `<article class="state-run state-tone-${tone}" data-run-id="${esc(run.id)}"><div class="state-run-head"><div class="state-run-main"><h3 class="state-headline">${esc(stateNames[primary]||'State not recorded')}</h3><div class="state-support"><span class="state-reading-count">${run.sample_count} reading${run.sample_count===1?'':'s'}</span></div></div><button class="button secondary" data-state-run="${esc(run.id)}" aria-expanded="${expanded}">${expanded?'Hide logs':'Show '+run.sample_count+' logs'}</button></div><details class="state-technical" data-state-details="${esc(run.id)}"><summary>Details</summary>${diagnostic?`<p class="state-diagnostic">${esc(diagnostic)}</p>`:''}<div class="state-run-meta">About ${durationText(run.estimated_coverage_ms)} estimated coverage · Observed span: ${durationText(run.observed_span_ms)}${gaps?' · '+gaps+' data gap'+(gaps===1?'':'s')+' ('+durationText(run.unobserved_gap_ms)+' unobserved)':''}</div><div class="state-run-clock">Device ${durationText(run.start_uptime_ms)}–${durationText(run.end_uptime_ms)} · received ${esc(stamp(run.first_received_at))}<br>Boot ${esc(run.boot_id)} · sequence ${run.first_seq}–${run.last_seq}</div></details>${expanded?runRecords(run):''}</article>`;
}
function timelineEventCard(item){
 const entry=item.entry||item,prediction=item.predicted_state;
 const cargoPrediction=prediction?{seal_state:prediction.seal_state,condition_state:['recent_impact','strong_vibration','tilted','unsafe_environment'].includes(prediction.condition_state)?prediction.condition_state:null}:null;
 return `<article class="trip-timeline-event ${entry.kind==='alert'?'alert':''}"><div class="event-summary"><button class="event-detail" data-entry="${esc(entry.id)}"><strong>${esc(entry.message)}</strong>${entry.location?`<small>${esc(entry.location)}</small>`:''}</button><time title="${esc(stamp(entry.created_at))}">${esc(clock(entry.created_at))}</time></div><div class="event-state-context">${prediction?`<div class="state-tags">${stateBadges(cargoPrediction)}</div><small>State at/before event${item.prediction_age_ms!=null?' · '+durationText(item.prediction_age_ms)+' earlier':''}</small>`:'<small>No device state captured with this entry</small>'}</div></article>`;
}
function stateRunPanel(){
 const runs=state.detail?.state_runs||[],all=state.detail?.timeline||runs.map(r=>({type:'state',id:r.id,created_at:r.first_received_at,state_run:r})).concat((state.detail?.activity||[]).map(e=>({type:'event',id:e.id,created_at:e.created_at,entry:e}))).sort((a,b)=>b.created_at.localeCompare(a.created_at));
 const items=state.allStates?all.filter(i=>i.type==='state'):all,offset=state.allStates?0:Math.min(state.runOffset,Math.max(0,Math.floor((items.length-1)/20)*20)),visible=state.allStates?items:items.slice(offset,offset+20);
 return `<section class="panel state-history trip-other-logs"><div class="panel-head"><div><h2>Trip timeline</h2><span class="sub">${state.detail?.total_state_samples||0} readings · ${runs.length} linked states · ${all.filter(i=>i.type==='event').length} events & notes</span></div><div class="timeline-controls"><button class="button secondary" data-action="all-states" aria-pressed="${Boolean(state.allStates)}">${state.allStates?'Show events too':'Show all states'}</button><button class="text-button" data-view="activity">All original logs</button></div></div><div class="timeline-caption">Matching cargo states are linked. Movement and sensor details are inside the logs. <details><summary>Duration estimates</summary><p>Ten readings at 5-second intervals span 45 seconds and estimate about 50 seconds of coverage. Linked groups keep any unobserved gaps marked. Reboots and actual state changes remain separate.</p></details></div>${visible.length?visible.map(item=>item.type==='state'?stateRunCard(item.state_run):timelineEventCard(item)).join(''):'<div class="timeline-empty">No logs for this trip yet.</div>'}${!state.allStates&&items.length>20?`<div class="pagination"><span>Entries ${offset+1}–${offset+visible.length} of ${items.length}</span><div><button class="button secondary" data-run-page="prev" ${offset===0?'disabled':''}>Previous entries</button> <button class="button secondary" data-run-page="next" ${offset+20>=items.length?'disabled':''}>Next entries</button></div></div>`:''}</section>`;
}
function tripPage(){
 if(!state.detail)return '<section class="panel"><div class="timeline-empty">Loading this trip’s details and logs…</div></section>';
 return `<div class="section-label"><button class="button secondary" data-view="trips">← Back to trips</button>${tripSelect()}</div>`+routePanel()+stateRunPanel()+sensors();
}
function updateTripUrl(){const url=new URL(location.href);if(state.view==='trip'&&state.selected)url.searchParams.set('trip',state.selected);else url.searchParams.delete('trip');history.replaceState(null,'',url.pathname+url.search+url.hash)}

function tripDirectory(){const q=state.search.toLowerCase();const trips=state.trips.filter(t=>(state.status==='all'||t.status===state.status)&&[t.name,t.origin,t.destination,t.device_id].some(v=>v.toLowerCase().includes(q)));return `<div class="toolbar"><div class="search-field">${icon('search')}<input id="trip-search" aria-label="Search trips" placeholder="Search shipments, cities or devices…" value="${esc(state.search)}"></div><select id="status-filter" aria-label="Filter trip status">${[['all','All statuses'],['active','In transit'],['completed','Delivered'],['cancelled','Cancelled']].map(([v,l])=>`<option value="${v}" ${state.status===v?'selected':''}>${l}</option>`).join('')}</select></div><section class="panel"><div class="panel-head"><h2>All journeys</h2><span class="sub">${trips.length} trip${trips.length===1?'':'s'}</span></div><div id="trip-table">${tripTable(trips,true)}</div></section>`}
function activityPage(){return `<section class="panel"><div class="panel-head"><h2>Trip activity</h2>${tripSelect()}</div><div class="activity-toolbar"><div class="filter-chips">${[['all','All activity'],['checkpoint','Checkpoints'],['note','Notes'],['alert','Alerts'],['device','Device']].map(([k,l])=>`<button class="filter-chip ${state.kind===k?'active':''}" data-filter="${k}">${l}</button>`).join('')}</div><button class="text-button" data-action="export" ${state.selected?'':'disabled'}>${icon('download')} Export CSV</button></div>${timeline(state.detail?.activity||[],true)}${state.selected?`<div class="pagination"><span>${state.detail?.total||0} entries · page ${Math.floor(state.offset/50)+1}</span><div><button class="button secondary" data-page="prev" ${state.offset===0?'disabled':''}>Previous</button> <button class="button secondary" data-page="next" ${state.offset+50>=(state.detail?.total||0)?'disabled':''}>Next</button></div></div>`:''}</section><div class="info-panel"><h2>A complete record of this journey</h2><p>Manual checkpoints record where you report the shipment to be. Device events use server receipt time; queued readings may have happened earlier. Sensor records retain their original trip ID even when uploaded after delivery.</p></div>`}
function devicePage(){return `<div class="device-page">${(state.devices.length?state.devices:[currentDevice()]).map(devicePanel).join('')}</div><div class="info-panel"><h2>From a connected board to a monitored shipment</h2><p>The dashboard updates when new data arrives. A board appears offline after 20 seconds without a heartbeat. The connection-test sketch reports Wi-Fi status; the cargo-monitor sketch adds temperature, humidity, tilt, lid state and sensor alerts. Starting a trip does not arm cargo protection; arming is a separate device operation.</p><p>Pickup, destination and checkpoints are manual locations. No GPS position or route distance is inferred. <a href="/connection">Open the simple connection test</a> · <a href="/records">Open raw sensor records</a> · <a href="/console">ESP raw logs</a></p></div>`}
function render(){
 const views={trip:['Trips / Details','Trip details','This shipment’s details, predicted states and original logs.'],overview:['Overview','Shipment overview','A clear view of every trip, from dispatch to delivery.'],trips:['Trips','Your journeys','Every shipment has a story. Keep them all in one place.'],activity:['Activity log','The journey, recorded','Checkpoints, notes and device events, tied to the right trip.'],device:['Device','Connected to your cargo','Live device presence and the health of your connection.']};
 const [crumb,title,description]=views[state.view];$('#breadcrumb').textContent=crumb;$('#page-title').textContent=title;$('#page-description').textContent=description;
 document.querySelectorAll('.sidebar [data-view]').forEach(b=>{b.classList.toggle('active',(b.dataset.view===state.view||(state.view==='trip'&&b.dataset.view==='trips')));b.setAttribute('aria-current',(b.dataset.view===state.view||(state.view==='trip'&&b.dataset.view==='trips'))?'page':'false')});
 $('#nav-count').textContent=state.trips.length;const online=!state.stale&&state.devices.some(d=>d.connected);$('#sidebar-dot').classList.toggle('online',online);$('#sidebar-state').textContent=state.stale?'Server unavailable':online?'Device connected':'Device offline';
 if(!state.loaded&&state.stale){$('#content').innerHTML='<div class="loading-card">Could not load the workspace.<br><button class="button secondary" data-action="settings">Connection settings</button></div>';return}
 const openStateDetails=new Set([...document.querySelectorAll('[data-state-details][open]')].map(el=>el.dataset.stateDetails));
 $('#content').innerHTML=({trip:tripPage,overview,trips:tripDirectory,activity:activityPage,device:devicePage})[state.view]();
 document.querySelectorAll('[data-state-details]').forEach(el=>{el.open=openStateDetails.has(el.dataset.stateDetails)});
}
function navigate(view){state.view=view;state.allStates=false;state.kind='all';state.offset=0;state.runOffset=0;state.expandedRun=null;state.expandedData=null;state.memberOffset=0;updateTripUrl();render();refresh()}
function openDialog(id){const dialog=$(id);dialog.querySelector('.form-error')?.replaceChildren();dialog.showModal()}
function openSettings(){$('#settings-form').elements.token.value=state.token;openDialog('#settings-dialog')}
$('#settings-open').onclick=openSettings;$('#mobile-settings').onclick=openSettings;
document.addEventListener('click',async event=>{
 const button=event.target.closest('button');if(!button||button.disabled)return;
 if(button.hasAttribute('data-close')){button.closest('dialog').close();return}
 if(button.dataset.view){navigate(button.dataset.view);return}
 if(button.dataset.trip){state.selected=button.dataset.trip;state.detail=null;state.showLive=false;navigate('trip');return}
 if(button.dataset.stateRun){state.expandedRun=state.expandedRun===button.dataset.stateRun?null:button.dataset.stateRun;state.memberOffset=0;state.expandedData=null;render();await refresh();return}
 if(button.dataset.runPage){state.runOffset=Math.max(0,state.runOffset+(button.dataset.runPage==='next'?20:-20));state.expandedRun=null;state.expandedData=null;render();return}
 if(button.dataset.runMemberPage){state.memberOffset=Math.max(0,state.memberOffset+(button.dataset.runMemberPage==='next'?20:-20));state.expandedData=null;await refresh();return}
 if(button.dataset.runRecord){const record=state.expandedData?.records.find(r=>String(r.id)===button.dataset.runRecord);if(record){$('#detail-json').textContent=JSON.stringify(record,null,2);openDialog('#detail-dialog')}return}
 if(button.dataset.filter){state.kind=button.dataset.filter;state.offset=0;await refresh();return}
 if(button.dataset.page){state.offset=Math.max(0,state.offset+(button.dataset.page==='next'?50:-50));await refresh();return}
 if(button.dataset.entry){const entry=state.detail?.timeline?.find(i=>i.type==='event'&&i.id===button.dataset.entry)?.entry||state.detail?.activity.find(e=>e.id===button.dataset.entry);if(entry){$('#detail-json').textContent=JSON.stringify(entry,null,2);openDialog('#detail-dialog')}return}
 const action=button.dataset.action;
 if(action==='settings'){openSettings();return}
 if(action==='all-states'){state.allStates=!state.allStates;state.runOffset=0;render();return}
 if(action==='device-wifi'){openWifi(button.dataset.device);return}
 if(action==='fetch-live'){
  state.fetchBusy=true;button.disabled=true;
  try{
   const request=await post('/fetch',{device_id:currentDevice().device_id});
   state.fetchRequestId=request.request_id;state.showLive=true;
   notify('Fresh reading requested. Waiting for the device…');
   await refresh();
  }catch(err){error(err.message)}finally{state.fetchBusy=false;render()}
  return;
 }
 if(action==='new-trip'){const form=$('#trip-form');form.reset();const available=state.devices.find(d=>!state.trips.some(t=>t.device_id===d.device_id&&t.status==='active'));form.elements.device_id.value=available?.device_id||currentDevice().device_id;$('#device-options').innerHTML=state.devices.map(d=>`<option value="${esc(d.device_id)}"></option>`).join('');openDialog('#trip-dialog');updateTripAvailability();return}
 if(!state.selected)return;
 if(action==='checkpoint'||action==='note'){modalTrip=state.selected;const form=$('#log-form');form.reset();form.elements.kind.value=action;form.elements.location.required=action==='checkpoint';$('#location-label').hidden=action!=='checkpoint';$('#log-title').textContent=action==='checkpoint'?'Add a checkpoint':'Add a trip note';$('#log-trip-name').textContent=state.detail.trip.name;openDialog('#log-dialog')}
 if(action==='finish'){modalTrip=state.selected;$('#finish-form').reset();openDialog('#finish-dialog')}
 if(action==='export'){button.disabled=true;try{const response=await api(`/trips/${state.selected}/export`);const url=URL.createObjectURL(await response.blob());const link=document.createElement('a');link.href=url;link.download=`waypost-trip-${state.selected.slice(0,8)}.csv`;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);notify('Trip history exported')}catch(err){error(err.message)}finally{button.disabled=false}}
});
document.addEventListener('change',event=>{if(event.target.id==='trip-select'){state.selected=event.target.value;state.detail=null;state.showLive=false;state.offset=0;state.runOffset=0;state.expandedRun=null;state.expandedData=null;updateTripUrl();refresh()}if(event.target.id==='status-filter'){state.status=event.target.value;render()}});
document.addEventListener('input',event=>{if(event.target.closest('#trip-form')&&event.target.name==='device_id')updateTripAvailability();if(event.target.id==='trip-search'){state.search=event.target.value;const q=state.search.toLowerCase();const trips=state.trips.filter(t=>(state.status==='all'||t.status===state.status)&&[t.name,t.origin,t.destination,t.device_id].some(v=>v.toLowerCase().includes(q)));$('#trip-table').innerHTML=tripTable(trips,true)}});
function handleForm(id,action){$(id).addEventListener('submit',async event=>{event.preventDefault();const form=event.currentTarget;const submit=form.querySelector('[type="submit"]');submit.disabled=true;form.querySelector('.form-error').textContent='';try{await action(Object.fromEntries(new FormData(form)));form.closest('dialog').close();await refresh()}catch(err){form.querySelector('.form-error').textContent=err.message}finally{submit.disabled=false}})}
handleForm('#trip-form',async data=>{const trip=await post('/trips',data);state.selected=trip.id;state.view='trip';state.offset=0;state.kind='all';state.showLive=false;state.runOffset=0;state.expandedRun=null;state.expandedData=null;updateTripUrl();notify('Trip started. Its journey is now being recorded.')});
handleForm('#log-form',async data=>{await post(`/trips/${modalTrip}/logs`,data);notify(data.kind==='checkpoint'?'Checkpoint added to this trip':'Note saved to this trip')});
handleForm('#finish-form',async data=>{await post(`/trips/${modalTrip}/finish`,data);notify(data.status==='completed'?'Delivery confirmed. Trip history saved.':'Trip cancelled. History preserved.')});
handleForm('#settings-form',async data=>{const previous=state.token;state.token=data.token;try{await json('/trips');try{sessionStorage.setItem('waypost-token',state.token)}catch{};notify('Connected to your workspace')}catch(err){state.token=previous;throw err}});

function updateTripAvailability(){
 const form=$('#trip-form');if(!$('#trip-dialog').open)return;
 const active=state.trips.find(t=>t.status==='active'&&t.device_id===form.elements.device_id.value.trim());
 form.querySelector('[type="submit"]').disabled=Boolean(active);
 form.querySelector('.form-error').textContent=active?`This device is already on “${active.name}”. Finish that trip before starting another.`:'';
}
function wifiRow(profile={ssid:'',priority:0,has_password:false}){
 const row=document.createElement('div');row.className='wifi-network-row';
 row.innerHTML=`<label>Network name (SSID)<input data-field="ssid" value="${esc(profile.ssid)}" maxlength="32" required autocomplete="off"></label><label>Password<input data-field="password" type="password" maxlength="63" placeholder="${profile.has_password?'Saved · leave blank to keep':'8–63 characters'}" autocomplete="new-password"></label><label>Priority<input data-field="priority" type="number" min="0" max="999" value="${profile.priority}" required></label><button class="button secondary" type="button" data-wifi-remove>Remove</button>`;
 return row;
}
function wifiStatus(config){
 const network=config.network||{};
 $('#wifi-current').textContent=network.connected?'Connected to '+network.ssid:network.ssid?'Last reported network: '+network.ssid+' · not confirmed connected':'Connected network not reported yet';
 $('#wifi-current').className=network.connected?'wifi-current connected':'wifi-current';
 document.querySelectorAll('#wifi-list .wifi-network-row').forEach(row=>{
  row.querySelector('.wifi-connection-badge')?.remove();
  const matches=row.querySelector('[data-field="ssid"]').value===network.ssid;
  if(matches){const badge=document.createElement('span');badge.className='wifi-connection-badge';badge.textContent=network.connected?'Connected':'Last reported';row.append(badge)}
 });
 $('#wifi-status').textContent=config.revision===0?'No networks saved yet. The original setup network is still available.':config.applied_revision===config.revision?`Settings revision ${config.revision} confirmed by device.`:`Settings revision ${config.revision} saved. Waiting for the device to apply them.`}
async function openWifi(deviceId=currentDevice().device_id){
 const form=$('#wifi-form');form.reset();form.elements.device_id.value=deviceId;
 $('#wifi-list').replaceChildren();$('#wifi-current').textContent='';$('#wifi-status').textContent='Loading settings…';openDialog('#wifi-dialog');
 try{const config=await json('/wifi?'+new URLSearchParams({device_id:form.elements.device_id.value}));config.profiles.forEach(p=>$('#wifi-list').append(wifiRow(p)));wifiStatus(config)}catch(err){form.querySelector('.form-error').textContent=err.message}
}
$('#wifi-open').onclick=()=>openWifi();
$('#wifi-add').onclick=()=>{const list=$('#wifi-list');if(list.children.length>=8){$('#wifi-form .form-error').textContent='Save up to eight networks.';return}list.append(wifiRow({ssid:'',priority:list.children.length*10,has_password:false}))};
$('#wifi-list').onclick=e=>{if(e.target.closest('[data-wifi-remove]'))e.target.closest('.wifi-network-row').remove()};
$('#wifi-form').addEventListener('submit',async e=>{
 e.preventDefault();const form=e.currentTarget,button=form.querySelector('[type="submit"]');button.disabled=true;form.querySelector('.form-error').textContent='';
 try{
  const profiles=[...$('#wifi-list').children].map(row=>{const profile={ssid:row.querySelector('[data-field="ssid"]').value,priority:Number(row.querySelector('[data-field="priority"]').value)},password=row.querySelector('[data-field="password"]').value;if(password)profile.password=password;return profile});
  const config=await json('/wifi',{method:'PUT',body:JSON.stringify({device_id:form.elements.device_id.value,profiles})});
  $('#wifi-list').replaceChildren(...config.profiles.map(wifiRow));wifiStatus(config);notify('Wi-Fi networks saved');
 }catch(err){form.querySelector('.form-error').textContent=err.message}finally{button.disabled=false}
});
setInterval(async()=>{if(!$('#wifi-dialog').open)return;try{wifiStatus(await json('/wifi?'+new URLSearchParams({device_id:$('#wifi-form').elements.device_id.value})))}catch{}},5000);

// Keep one request open; the server responds immediately after a change.
async function watchChanges(){
 let revision='';
 for(;;){
  try{
   const token=state.token;
   const change=await json('/changes?'+new URLSearchParams({after:revision}));
   if(token!==state.token){revision='';continue}
   revision=change.revision;
   await refresh(); // Also refresh presence on the bounded idle timeout.
  }catch(err){
   await refresh();
   await new Promise(resolve=>setTimeout(resolve,1000));
  }
 }
}
watchChanges();
