"use strict";

const API_BASE = "http://127.0.0.1:8000";
// Retain existing stored workouts from the earlier 3-tab website.
const HISTORY_KEY = "runningAnalyzerHistory";
const $ = id => document.getElementById(id);
let paceChart = null;
let heartRateChart = null;
let displayedWorkout = null;

const now = new Date();
$("workoutDate").value = new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 10);

function showView(view) {
  for (const id of ["workoutView", "historyView"]) $(id).classList.toggle("hidden", id !== view);
  document.querySelectorAll("[data-view]").forEach(button => {
    const active = button.dataset.view === view;
    button.classList.toggle("active", active);
    button.setAttribute("aria-current", active ? "page" : "false");
  });
  // Recreate charts after their canvas becomes visible.
  if (view === "historyView") requestAnimationFrame(() => renderAnalytics(historyCache));
}
document.querySelectorAll("[data-view]").forEach(b => b.addEventListener("click", () => showView(b.dataset.view)));

function status(message, kind="") {
  $("uploadStatus").textContent = message;
  $("uploadStatus").className = `status ${kind}`;
}
function num(value) {
  if (value === null || value === undefined || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}
function parsePace(value) {
  const n = num(value);
  if (n !== null) return n > 0 ? n : null;
  if (typeof value !== "string") return null;
  const m = value.match(/(\d{1,2})\s*[:'’]\s*(\d{2})/);
  if (!m || Number(m[2]) >= 60) return null;
  return Number(m[1]) + Number(m[2]) / 60;
}
function normalizeSplits(rows) {
  if (!Array.isArray(rows)) return [];
  const byMile = new Map();
  for (const row of rows) {
    const mile = num(row.mile);
    if (mile === null || mile <= 0) continue;
    byMile.set(mile, {
      mile,
      time: row.time || null,
      pace: parsePace(row.pace),
      heartRate: num(row.heartRate)
    });
  }
  return [...byMile.values()].sort((a,b) => a.mile - b.mile);
}
function paceLabel(pace) {
  if (pace === null || !Number.isFinite(pace)) return "—";
  const seconds = Math.round(pace * 60);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}
function classifyWorkout(averagePace, originalActivity="Outdoor Run") {
  // Back Walk is explicitly selected; the pace rule does not override it.
  if (/^back\s*walk$/i.test(String(originalActivity).trim())) return "Back Walk";
  const pace = parsePace(averagePace);
  if (pace === null) return originalActivity || "Workout";
  const environment = /indoor/i.test(originalActivity) ? "Indoor" : "Outdoor";
  return `${environment} ${pace >= 15 ? "Walk" : "Run"}`;
}
let historyCache = [];
let pendingWorkout = null;
let editingId = null;
let reviewBaseWarnings = [];
let weeklyChart = null;
let trendChart = null;

async function apiJson(path, options={}) {
  let response;
  try {response = await fetch(`${API_BASE}${path}`, options);}
  catch {throw new Error("Cannot connect to the Python API. Start uvicorn on port 8000.");}
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : `HTTP ${response.status}`);
  return body;
}
async function refreshHistory() {
  const data = await apiJson("/workouts");
  historyCache = data.workouts || [];
  renderHistory();
}
async function persistWorkout(workout, id=null) {
  const data = await apiJson(id ? `/workouts/${encodeURIComponent(id)}` : "/workouts", {
    method: id ? "PUT" : "POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({workout})
  });
  await refreshHistory();
  return data.workout;
}
function validateImage(file) {
  if (!file) throw new Error("Select a Workout Details screenshot.");
  if (!["image/png","image/jpeg"].includes(file.type)) throw new Error("Use JPEG or PNG screenshots.");
  if (file.size > 10 * 1024 * 1024) throw new Error("Each screenshot must be under 10 MB.");
}
async function sendImage(endpoint, file, extra={}) {
  const body = new FormData();
  body.append("file", file);
  for (const [key,value] of Object.entries(extra)) body.append(key, String(value));
  const response = await fetch(`${API_BASE}${endpoint}`, {method:"POST",body});
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(typeof error.detail === "string" ? error.detail : `${endpoint}: HTTP ${response.status}`);
  }
  return await response.json();
}
function approximatePace(splits) {
  // Fallback only; the workout overview's average pace is preferred.
  // Ignore a partial final mile because its extent may be unknown.
  const full = splits.filter((s,i) => s.pace !== null && (i < splits.length - 1 || splits.length === 1));
  return full.length ? full.reduce((sum,s) => sum+s.pace, 0) / full.length : null;
}
function metric(label, value) {
  const el = document.createElement("div");el.className="metric";
  const small = document.createElement("small");small.textContent=label;
  const strong = document.createElement("strong");strong.textContent=value == null || value === "" ? "—" : String(value);
  el.append(small,strong);return el;
}
function graphWorkouts(splits) {
  paceChart?.destroy(); heartRateChart?.destroy();
  paceChart = null; heartRateChart = null;
  const rows = normalizeSplits(splits);
  $("graphsContainer").classList.toggle("hidden", !rows.length);
  if (!rows.length) return;
  if (!window.Chart) throw new Error("Chart.js is unavailable. Check your internet connection.");
  // Include missing mile positions as nulls: do not draw misleading lines across gaps.
  const byMile = new Map(rows.map(row => [row.mile, row]));
  const maxMile = Math.min(200, Math.max(...rows.map(row => row.mile)));
  const labels = Array.from({length:maxMile}, (_, i) => String(i + 1));
  const base = {responsive:true,maintainAspectRatio:false,interaction:{mode:"index",intersect:false},plugins:{legend:{display:false}}};
  paceChart = new Chart($("paceChart"), {
    type:"line",
    data:{labels,datasets:[{label:"Pace",data:labels.map(label=>byMile.get(Number(label))?.pace ?? null),borderColor:"#3487ff",backgroundColor:"#3487ff",pointRadius:4,borderWidth:2,tension:0,spanGaps:false}]},
    options:{...base,plugins:{legend:{display:false},tooltip:{callbacks:{label:ctx=>`Pace: ${paceLabel(ctx.parsed.y)} /mi`}}},scales:{x:{title:{display:true,text:"Mile / split number"}},y:{title:{display:true,text:"Pace (min/mi)"}}}}
  });
  heartRateChart = new Chart($("heartRateChart"), {
    type:"line",
    data:{labels,datasets:[{label:"Heart rate",data:labels.map(label=>byMile.get(Number(label))?.heartRate ?? null),borderColor:"#ff5555",backgroundColor:"#ff5555",pointRadius:4,borderWidth:2,tension:0,spanGaps:false}]},
    options:{...base,scales:{x:{title:{display:true,text:"Mile / split number"}},y:{title:{display:true,text:"Heart rate (BPM)"}}}}
  });
}
function displayWorkout(workout) {
  displayedWorkout = workout;
  showView("workoutView");
  $("workoutResults").classList.remove("hidden");
  $("workoutResultTitle").textContent = workout.activity || "Workout Details";
  $("workoutResultSubtitle").textContent = [workout.date,workout.startTime,workout.location].filter(Boolean).join(" · ");
  const metrics = $("workoutMetrics");metrics.replaceChildren();
  [
    ["Workout time",workout.duration],
    ["Distance",workout.distanceMiles != null ? `${workout.distanceMiles} mi` : null],
    ["Average pace",parsePace(workout.averagePace) != null ? paceLabel(parsePace(workout.averagePace)) + "/mi" : null],
    ["Average heart rate",workout.averageHeartRate != null ? `${workout.averageHeartRate} BPM` : null],
    ["Active calories",workout.activeCalories != null ? `${workout.activeCalories} cal` : null],
    ["Total calories",workout.totalCalories != null ? `${workout.totalCalories} cal` : null],
    ["Elevation gain",workout.elevationGainFt != null ? `${workout.elevationGainFt} ft` : null],
    ["Splits",(workout.splits || []).length]
  ].forEach(([k,v]) => metrics.append(metric(k,v)));
  $("jsonOutput").textContent = JSON.stringify(workout,null,2);
  graphWorkouts(workout.splits || []);
  $("workoutResults").scrollIntoView({behavior:"smooth",block:"start"});
}


// ===== 1. Extract first, then review, then save =====
function reviewInput(label,key,value,type="text",hint="") {
  const holder=document.createElement("div");holder.className="review-field";
  const l=document.createElement("label");l.textContent=label;
  const input= type === "select" ? document.createElement("select") : document.createElement("input");
  input.dataset.field=key;
  if (type === "select") {
    for (const act of ["Outdoor Run","Outdoor Walk","Indoor Run","Indoor Walk","Back Walk"]) {
      const option=document.createElement("option");option.value=act;option.textContent=act;input.append(option);
    }
    if (value && ![...input.options].some(o=>o.value===value)) {
      const option=document.createElement("option");option.value=value;option.textContent=value;input.append(option);
    }
    input.value=value || "Outdoor Run";
  } else {
    input.type=type;
    input.value=value ?? "";
    if (type==="number") {input.step="any";input.min="0";}
  }
  l.append(input);holder.append(l);
  if (hint) {const small=document.createElement("small");small.textContent=hint;holder.append(small);}
  return holder;
}
function reviewRow(row={}) {
  const tr=document.createElement("tr");
  const entries=[
    ["mile",row.mile ?? "","number"],
    ["time",row.time ?? "","text"],
    ["pace",parsePace(row.pace) != null ? paceLabel(parsePace(row.pace)) : "","text"],
    ["heartRate",row.heartRate ?? "","number"]
  ];
  for (const [key,value,type] of entries) {
    const td=document.createElement("td");const input=document.createElement("input");
    input.dataset.split=key;input.value=value;input.type=type;
    if(type==="number") {input.min="0";input.step="1";}
    input.setAttribute("aria-label",key);
    td.append(input);tr.append(td);
  }
  const td=document.createElement("td");const del=makeButton("Remove","danger",()=>tr.remove());td.append(del);tr.append(td);
  $("reviewSplits").append(tr);
}
function splitDistanceWarnings(workout) {
  const miles = num(workout.distanceMiles);
  const rows = normalizeSplits(workout.splits || []);
  if (miles === null || miles <= 0) return [];
  const expected = Math.ceil(miles - 0.00001);
  if (expected > 200) return ["Distance looks unusually large. Check OCR before saving."];
  const present = new Set(rows.map(row => row.mile));
  const missing = Array.from({length: expected}, (_,i) => i+1)
    .filter(m => !present.has(m));
  return missing.length ? [`MISSING SPLITS: miles ${missing.join(", ")}. Confirm the workout distance and add any genuinely missing rows.`] : [];
}

function updateReviewWarnings() {
  if (!pendingWorkout) return;
  const field = document.querySelector('#reviewFields [data-field="distanceMiles"]');
  const miles = field ? num(field.value.trim()) : num(pendingWorkout.distanceMiles);
  const rows = Array.from(document.querySelectorAll('#reviewSplits tr')).map(tr => {
    const mileInput = tr.querySelector('[data-split="mile"]');
    return {mile: Number(mileInput?.value)};
  });
  const warnings = [...reviewBaseWarnings, ...splitDistanceWarnings({distanceMiles:miles,splits:rows})];
  if (pendingWorkout.distanceEstimatedFromPace) {
    warnings.unshift('Distance was estimated from workout time and average pace because OCR was missing or inconsistent. Verify against Apple Fitness.');
  }
  $("reviewWarnings").textContent = warnings.length ? `Please check: ${warnings.join(" ")}` : 'No split-number gaps found for this distance. Verify the other OCR values before saving.';
}

function showReview(workout,warnings=[],id=null) {
  pendingWorkout=structuredClone(workout);editingId=id;
  reviewBaseWarnings = warnings.filter(w => !/missing (?:mile|split)|first recognized mile|distance was estimated/i.test(w));
  $("reviewSection").classList.remove("hidden");
  $("reviewWarnings").textContent = warnings.length
    ? `Please check: ${warnings.join(" ")}` : "Review every value against your screenshots.";
  const fields=$("reviewFields");fields.replaceChildren();
  const types=[
    ["Date","date","date"],["Activity","activity","select"],
    ["Start time","startTime","text"],["Location","location","text"],
    ["Workout time (H:MM:SS)","duration","text"],
    ["Distance (miles)","distanceMiles","number"],
    ["Average pace (MM:SS per mile)","averagePace","pace"],
    ["Average heart rate (BPM)","averageHeartRate","number"],
    ["Active calories","activeCalories","number"],
    ["Total calories","totalCalories","number"],
    ["Elevation (ft)","elevationGainFt","number"]
  ];
  for (const [label,key,type] of types) {
    const value=type==="pace" ? (parsePace(workout[key])!=null ? paceLabel(parsePace(workout[key])) : "") : workout[key];
    fields.append(reviewInput(label,key,value,type==="pace"?"text":type));
  }
  const splits=$("reviewSplits");splits.replaceChildren();
  for (const row of workout.splits||[]) reviewRow(row);
  updateReviewWarnings();
  $("reviewSection").scrollIntoView({behavior:"smooth",block:"start"});
}
function readReview() {
  if (!pendingWorkout) throw new Error("No workout to review.");
  const w=structuredClone(pendingWorkout);
  for (const field of document.querySelectorAll("#reviewFields [data-field]")) {
    const key=field.dataset.field; const v=field.value.trim();
    if (["distanceMiles","averageHeartRate","activeCalories","totalCalories","elevationGainFt"].includes(key)) {
      if (v && (!Number.isFinite(Number(v)) || Number(v)<0)) throw new Error(`Invalid ${key}.`);
      w[key]=v===""?null:Number(v);
    } else if (key==="averagePace") {
      w[key]=v ? parsePace(v) : null;
      if (v && w[key]===null) throw new Error("Average pace must look like 11:08 or 11.13.");
    } else w[key]=v || null;
  }
  if (!/^\d{4}-\d{2}-\d{2}$/.test(w.date||"")) throw new Error("Choose a valid date.");
  if (!w.activity) throw new Error("Choose an activity.");
  if (!w.distanceMiles || w.distanceMiles<=0) throw new Error("Distance must be greater than 0.");
  if (w.duration && durationSeconds(w.duration)===null) throw new Error("Workout duration must look like 2:27:41 or 42:01.");
  const byMile=new Map();
  for (const tr of $("reviewSplits").querySelectorAll("tr")) {
    const inputs=Object.fromEntries([...tr.querySelectorAll("[data-split]")].map(input=>[input.dataset.split,input.value.trim()]));
    const mile=Number(inputs.mile);if (!Number.isInteger(mile) || mile<1 || mile>999) throw new Error("Split mile must be a positive whole number.");
    if (byMile.has(mile)) throw new Error(`Mile ${mile} appears twice. Remove one or merge it manually.`);
    const pace=inputs.pace?parsePace(inputs.pace):null;
    if (inputs.pace && pace===null) throw new Error(`Invalid pace at mile ${mile}.`);
    if (inputs.time && durationSeconds(inputs.time)===null) throw new Error(`Invalid split time at mile ${mile}.`);
    const heart=inputs.heartRate?Number(inputs.heartRate):null;
    if (inputs.heartRate && (!Number.isInteger(heart)||heart<30||heart>250)) throw new Error(`Invalid heart rate at mile ${mile}.`);
    byMile.set(mile,{mile,time:inputs.time||null,pace,heartRate:heart});
  }
  w.splits=[...byMile.values()].sort((a,b)=>a.mile-b.mile);
  if (/^Back Walk$/i.test(w.activity)) w.activity="Back Walk";
  else w.activity=classifyWorkout(w.averagePace,w.activity);
  // Confirmed date/activity and OCR corrections are authoritative.
  w.reviewed=true;
  return w;
}
$("addSplitBtn").addEventListener("click",()=>{reviewRow();updateReviewWarnings();});
$("reviewSplits").addEventListener("input",updateReviewWarnings);
$("reviewSplits").addEventListener("click",event=>{
  if (event.target.closest("button")) requestAnimationFrame(updateReviewWarnings);
});
$("reviewFields").addEventListener("input",event=>{
  if (event.target.dataset.field === "distanceMiles") {
    if (pendingWorkout) pendingWorkout.distanceEstimatedFromPace=false;
    updateReviewWarnings();
  }
});
$("cancelReviewBtn").addEventListener("click",()=>{$("reviewSection").classList.add("hidden");pendingWorkout=null;editingId=null;});
$("reviewForm").addEventListener("submit",async event=>{
  event.preventDefault();const button=$("confirmSaveBtn");
  try {const corrected=readReview();button.disabled=true;
    const saved=await persistWorkout(corrected,editingId);
    $("reviewSection").classList.add("hidden");pendingWorkout=null;editingId=null;
    displayWorkout(saved);
    status("Reviewed workout saved permanently to the local database.","success");
  } catch(e){status(`Review not saved: ${e.message}`,"error"); $("reviewWarnings").textContent=e.message;}
  finally{button.disabled=false;}
});

$("workoutForm").addEventListener("submit",async event=>{
  event.preventDefault();const detailsFile=$("detailsImage").files[0];const splitsFiles=[...$("splitsImage").files];
  const date=$("workoutDate").value;const button=$("saveWorkoutBtn");
  try {
    validateImage(detailsFile);if (splitsFiles.length>2) throw new Error("Select 1 or 2 split screenshots maximum.");
    splitsFiles.forEach(validateImage);if (!date) throw new Error("Select the workout date.");
    button.disabled=true;status("Extracting workout details...");
    const result=await sendImage("/history",detailsFile,{year:date.slice(0,4)});
    if (!result.success || !result.workout || typeof result.workout!=="object") throw new Error("Invalid OCR result.");
    const workout=result.workout;workout.date=date;workout.sourceFile=detailsFile.name;workout.sourceType="combined";
    workout.splits=normalizeSplits(workout.splits);
    const warnings=[];
    if (splitsFiles.length) {
      status(`Processing ${splitsFiles.length} split image(s)...`);
      try {
        const body=new FormData();splitsFiles.forEach(f=>body.append("files",f));
        const combined=await apiJson("/extract-pages",{method:"POST",body});
        if (!combined.success || !Array.isArray(combined.data)) throw new Error("No split rows found.");
        workout.splits=normalizeSplits(combined.data);
        workout.splitsSourceFiles=splitsFiles.map(f=>f.name);
        // The generic splitter cannot know the workout distance and may flag
        // miles outside a short workout. Recalculate using overview distance.
        warnings.push(...(combined.warnings||[]).filter(w =>
          !/missing (?:mile|split)|first recognized mile/i.test(w)));
        warnings.push(...splitDistanceWarnings(workout));
        if (Array.isArray(combined.conflicts) && combined.conflicts.length) warnings.push(`${combined.conflicts.length} conflicting mile row(s). Compare both screenshots.`);
      } catch(e){warnings.push(`Split extraction failed: ${e.message}. Add/correct rows manually.`);}
    }
    const choice=$("workoutEnvironment").value;const original=workout.activity||"Outdoor Run";
    if (choice==="Back Walk") workout.activity="Back Walk";
    else if (choice!=="auto") workout.activity=`${choice} ${/walk/i.test(original)?"Walk":"Run"}`;
    workout.averagePace=parsePace(workout.averagePace) ?? approximatePace(workout.splits);
    workout.activity=classifyWorkout(workout.averagePace,workout.activity||original);
    if (workout.distanceEstimatedFromPace) warnings.push("Distance was estimated using workout time and average pace. Confirm the distance in the original screenshot.");
    if (!workout.duration) warnings.push("Workout time is missing.");
    if (workout.activeCalories==null || workout.totalCalories==null) warnings.push("Calories are incomplete.");
    showReview(workout,warnings);status("OCR complete. Review and correct values before saving.");
  } catch(e){status(e.message,"error");}
  finally{button.disabled=false;}
});

function makeButton(label,kind,fn) {
  const b=document.createElement("button");b.type="button";b.className=kind;b.textContent=label;b.addEventListener("click",fn);return b;
}
const RACE_DISTANCES=[
  {id:"best5k",label:"5K",miles:3.106856},
  {id:"best10k",label:"10K",miles:6.213712},
  {id:"bestHalf",label:"Half Marathon",miles:13.109378},
  {id:"bestFull",label:"Full Marathon",miles:26.218757}
];
function durationSeconds(raw) {
  if (raw===null||raw===undefined||raw==="") return null;
  if (typeof raw==="number") return Number.isFinite(raw)&&raw>0?raw:null;
  const m=String(raw).trim().match(/^(\d{1,3}):(\d{2})(?::(\d{2}))?$/);
  if (!m||Number(m[2])>=60||(m[3]&&Number(m[3])>=60)) return null;
  return m[3]===undefined ? Number(m[1])*60+Number(m[2]) : Number(m[1])*3600+Number(m[2])*60+Number(m[3]);
}
function formatDuration(raw) {
  const seconds=Math.round(raw),hours=Math.floor(seconds/3600),minutes=Math.floor((seconds%3600)/60),secs=seconds%60;
  return hours?`${hours}:${String(minutes).padStart(2,"0")}:${String(secs).padStart(2,"0")}`:`${minutes}:${String(secs).padStart(2,"0")}`;
}
function isRun(w){return /\brun\b/i.test(w.activity||"") && !/back\s*walk/i.test(w.activity||"");}
function isBack(w){return /^back\s*walk$/i.test(w.activity||"");}

// PRs: exact only when the recorded workout is at exact race distance (<= 0.005 mi discrepancy)
// and the elapsed duration exists. Any fractional split interpolation is explicitly ESTIMATED.
function raceResult(w,target) {
  if (!isRun(w)) return null;
  const miles=num(w.distanceMiles),total=durationSeconds(w.duration);
  if (miles===null || miles<target) return null;
  if (total!==null && Math.abs(miles-target)<=0.005) return {seconds:total,kind:"Recorded workout",date:w.date};
  const splitRows=normalizeSplits(w.splits||[]);
  if (!splitRows.length) return null;
  const byMile=new Map(splitRows.map(s=>[s.mile,s]));
  const full=Math.floor(target);
  let seconds=0;
  for(let mile=1;mile<=full;mile++) {
    const row=byMile.get(mile);if(!row) return null;
    const time=durationSeconds(row.time);
    // Full mile split time may fall in pace if time OCR failed; don't fill missing actual elapsed times.
    if(time===null) return null;
    seconds+=time;
  }
  const remainder=target-full;
  if (remainder>0.000001) {
    const row=byMile.get(full+1);
    if (!row || parsePace(row.pace)===null) return null;
    // Fractional distances are estimated at constant pace within the split.
    seconds+=remainder*parsePace(row.pace)*60;
  }
  return {seconds,kind:"Estimated from splits",date:w.date};
}
function renderPersonalBests(history) {
  for (const race of RACE_DISTANCES) {
    let best=null;
    for(const workout of history) {
      const result=raceResult(workout,race.miles);
      if(result && (!best||result.seconds<best.seconds)) best=result;
    }
    $(race.id).textContent=best?formatDuration(best.seconds):"—";
    $(race.id+"Date").textContent=best?`${best.kind} · ${best.date||"Date unknown"}`:`${race.miles.toFixed(2)} miles · No supported result yet`;
  }
}
function renderTrainingPaces(history) {
  const ids=["training5k","training10k","trainingHalf","trainingFull"];
  RACE_DISTANCES.forEach((race,i)=>{
    const matches=history.filter(w=>{
      const miles=num(w.distanceMiles);
      return isRun(w)&&miles!==null && miles>=race.miles*.98 && miles<=race.miles*1.03;
    }).sort((a,b)=>(b.date||"").localeCompare(a.date||"")).slice(0,5);
    let minutes=0,distance=0,count=0;
    for(const w of matches) {
      const miles=num(w.distanceMiles),seconds=durationSeconds(w.duration),pace=parsePace(w.averagePace);
      const p=seconds!==null ? seconds/60/miles : pace;
      if(p===null||!Number.isFinite(p)||p<=0) continue;
      minutes+=p*miles;distance+=miles;count++;
    }
    $(ids[i]).textContent=count?`${paceLabel(minutes/distance)}/mi`:"—";
    $(ids[i]+"Info").textContent=count?`${count} recent running workout(s)`:`${race.miles.toFixed(2)} miles · No qualifying runs yet`;
  });
}

// ===== 5. Training analytics =====
function startOfWeek(date) {
  const d=new Date(date+"T12:00:00");
  if(Number.isNaN(d.getTime())) return null;
  d.setDate(d.getDate()-((d.getDay()+6)%7));d.setHours(12,0,0,0);
  return d.toISOString().slice(0,10);
}
function renderAnalytics(history) {
  const weeks=Number($("analyticsWeeks").value);
  const current=startOfWeek(new Date().toISOString().slice(0,10));
  const first=new Date(current+"T12:00:00");first.setDate(first.getDate()-7*(weeks-1));
  const labels=[];const buckets=new Map();
  for(let i=0;i<weeks;i++) {
    const date=new Date(first);date.setDate(date.getDate()+i*7);
    const key=date.toISOString().slice(0,10);
    labels.push(key);buckets.set(key,{run:0,walk:0,back:0});
  }
  const paces=[];let runMiles=0,walkMiles=0,backMiles=0,runSeconds=0,runCount=0;
  for (const workout of history) {
    const miles=num(workout.distanceMiles),key=startOfWeek(workout.date);
    if(miles===null || !buckets.has(key)) continue;
    const category=isBack(workout)?"back":isRun(workout)?"run":"walk";
    buckets.get(key)[category]+=miles;
    if(category==="run") {
      runMiles+=miles;runCount++;
      const time=durationSeconds(workout.duration),pace=parsePace(workout.averagePace);
      if(time!==null)runSeconds+=time;
      else if(pace!==null)runSeconds+=pace*miles*60;
      const avg=pace ?? (time!==null?time/60/miles:null);
      if(avg!==null)paces.push({x:workout.date,y:avg});
    } else if(category==="back")backMiles+=miles;else walkMiles+=miles;
  }
  const stats=$("trainingStats");stats.replaceChildren();
  [["Running distance",`${runMiles.toFixed(2)} mi`],["Walking distance",`${walkMiles.toFixed(2)} mi`],
   ["Back Walk distance",`${backMiles.toFixed(2)} mi`],["Run workouts",runCount],
   ["Running pace",runMiles&&runSeconds?`${paceLabel(runSeconds/60/runMiles)}/mi`:"—"]].forEach(([k,v])=>stats.append(metric(k,v)));
  weeklyChart?.destroy();trendChart?.destroy();weeklyChart=null;trendChart=null;
  if(!window.Chart) return;
  weeklyChart=new Chart($("weeklyDistanceChart"),{type:"bar",data:{labels,datasets:[
    {label:"Run",data:labels.map(k=>buckets.get(k).run),backgroundColor:"#3478f6"},
    {label:"Walk",data:labels.map(k=>buckets.get(k).walk),backgroundColor:"#23a37c"},
    {label:"Back Walk",data:labels.map(k=>buckets.get(k).back),backgroundColor:"#a17de4"}
  ]},options:{responsive:true,maintainAspectRatio:false,scales:{x:{stacked:true},y:{stacked:true,title:{display:true,text:"Miles"},beginAtZero:true}}}});
  paces.sort((a,b)=>a.x.localeCompare(b.x));
  trendChart=new Chart($("paceTrendChart"),{type:"line",data:{labels:paces.map(p=>p.x),datasets:[{label:"Run pace (min/mi)",data:paces.map(p=>p.y),borderColor:"#3478f6",pointRadius:4,tension:0}]},options:{responsive:true,maintainAspectRatio:false,scales:{y:{title:{display:true,text:"min/mi (lower is faster)"}}},plugins:{tooltip:{callbacks:{label:ctx=>paceLabel(ctx.parsed.y)+" /mi"}}}}});
}
$("analyticsWeeks").addEventListener("change",()=>renderAnalytics(historyCache));
function renderHistory() {
  const history=historyCache;
  $("historyCount").textContent=String(history.length);
  $("historyDistance").textContent=`${history.reduce((sum,w)=>sum+(num(w.distanceMiles)||0),0).toFixed(2)} mi`;
  renderPersonalBests(history);renderTrainingPaces(history);renderAnalytics(history);
  $("clearHistoryBtn").disabled=history.length===0;
  const list=$("historyList");list.replaceChildren();
  if(!history.length) {const p=document.createElement("p");p.className="muted";p.textContent="No saved workouts yet. Upload your first workout.";list.append(p);return;}
  for(const workout of history) {
    const card=document.createElement("article");card.className="history-item";
    const h=document.createElement("h3");h.textContent=`${workout.activity||"Workout"} — ${workout.date||"Date unknown"}`;
    const info=document.createElement("p");info.className="muted";
    info.textContent=[workout.distanceMiles!=null?`${workout.distanceMiles} mi`:null,workout.duration,
      parsePace(workout.averagePace)!=null?`${paceLabel(parsePace(workout.averagePace))}/mi`:null,
      `${(workout.splits||[]).length} splits`].filter(Boolean).join(" · ");
    const actions=document.createElement("div");actions.className="actions";
    actions.append(makeButton("Open Graphs","secondary",()=>{try{displayWorkout(workout);}catch(e){alert(e.message);}}));
    actions.append(makeButton("Edit Details + Splits","secondary",()=>{showView("workoutView");showReview(workout,[],workout.id);}));
    const pre=document.createElement("pre");pre.className="hidden history-detail";pre.textContent=JSON.stringify(workout,null,2);
    actions.append(makeButton("View JSON","secondary",()=>pre.classList.toggle("hidden")));
    actions.append(makeButton("Delete","danger",async()=>{
      if(!confirm(`Delete ${workout.activity} on ${workout.date}?`)) return;
      try {await apiJson(`/workouts/${encodeURIComponent(workout.id)}`,{method:"DELETE"});await refreshHistory();}
      catch(e){alert(e.message);}
    }));
    card.append(h,info,actions,pre);list.append(card);
  }
}
$("clearHistoryBtn").addEventListener("click",async()=>{
  if(!confirm("Permanently delete EVERY workout from the local database? Export a backup first."))return;
  try {for(const w of [...historyCache])await apiJson(`/workouts/${encodeURIComponent(w.id)}`,{method:"DELETE"});await refreshHistory();}
  catch(e){$("historyStatus").textContent=e.message;}
});
function downloadJson(data,name) {
  const blob=new Blob([JSON.stringify(data,null,2)],{type:"application/json"});
  const url=URL.createObjectURL(blob);const a=document.createElement("a");a.href=url;a.download=name;
  document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
$("downloadBtn").addEventListener("click",()=>{if(displayedWorkout)downloadJson(displayedWorkout,`workout-${displayedWorkout.date}.json`);});
$("exportHistoryBtn").addEventListener("click",()=>downloadJson({format:"running-analyzer-v2",workouts:historyCache},`running-analyzer-backup-${new Date().toISOString().slice(0,10)}.json`));
$("importHistoryBtn").addEventListener("click",()=>$("importHistoryFile").click());
$("importHistoryFile").addEventListener("change",async event=>{
  const file=event.target.files[0];if(!file)return;
  try {const data=JSON.parse(await file.text());const workouts=Array.isArray(data)?data:data.workouts;
    if(!Array.isArray(workouts))throw new Error("Expected a list or a backup with a workouts array.");
    const result=await apiJson("/workouts/import",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({workouts})});
    await refreshHistory();$("historyStatus").textContent=`Imported ${result.processed} record(s). ${result.errors.length} rejected.`;
  } catch(e){$("historyStatus").textContent=`Import failed: ${e.message}`;}
  finally{event.target.value="";}
});

// Import old localStorage history once, WITHOUT deleting the browser copy.
(async function initialize() {
  try {
    const raw=localStorage.getItem(HISTORY_KEY);
    const previous=raw?JSON.parse(raw):[];
    if(Array.isArray(previous)&&previous.length&&!localStorage.getItem("runningAnalyzerMigratedV2")) {
      const result=await apiJson("/workouts/import",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({workouts:previous})});
      if(result.errors.length===0) localStorage.setItem("runningAnalyzerMigratedV2","true");
      $("historyStatus").textContent=`Migrated ${result.processed} browser workout(s). ${result.errors.length} need correction.`;
    }
    await refreshHistory();
  } catch(e){$("historyStatus").textContent=`Database unavailable: ${e.message}`;}
})();
