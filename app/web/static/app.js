// Situation Guard web demo — plain JavaScript, no framework, no build step.
// You type or speak -> POST /api/chat -> the server runs Alexa (run_turn) over the MCP tools
// -> the reply and every tool step come back -> chat, plan timeline and activity feed update.

const $ = (id) => document.getElementById(id);

// --- scenarios: what people can try ------------------------------------------------------
const SCENARIOS = [
  {
    id: "real", icon: "star", title: "Your real day", live: true,
    text: "Your location, real weather, your Google Calendar, live public transport.",
    steps: [
      "What's the temperature today?",
      "Schedule a meeting with my boss tomorrow at 2pm for an hour.",
      "Tomorrow I also want to talk with one of my teammates at 2pm.",
      "Tomorrow at 2pm I want to go from here to Sydney Airport by public transport.",
    ],
  },
  {
    id: "tv", icon: "tv", title: "Two people, one TV",
    text: "No times given, one TV. Watch the clash get caught and fixed.",
    steps: ["I want to watch Traitors 2 on the drawing room TV.", "My wife wants to watch Traitors 1 on the same TV.", "Yes, do that."],
  },
  {
    id: "meetings", icon: "users", title: "The boss adds a meeting",
    text: "You can't be in two meetings at once.",
    steps: ["I have a team meeting tomorrow at 2pm for an hour.", "My boss just booked me into a meeting tomorrow at 1pm for two hours.", "Yes, go ahead."],
  },
  {
    id: "rain", icon: "rain", title: "Rain on dinner",
    text: "An outdoor dinner meets the forecast.",
    steps: ["Plan dinner outside at Central Station on Monday 12 October at 9pm for 90 minutes, then check the weather.", "Yes, move it."],
  },
  {
    id: "holiday", icon: "flag", title: "Public holiday",
    text: "The office is closed on a public holiday.",
    steps: ["I have a quarterly review at the office on Friday 25 December at 10am for an hour. The office closes on public holidays, please check.", "Yes."],
  },
  {
    id: "calendar", icon: "calendar", title: "Import my calendar",
    text: "Bring in a day of meetings, then add one more.",
    steps: ["Import my work calendar for Monday 12 October.", "My boss booked me into a meeting at 1pm for two hours that day.", "Yes."],
  },
  {
    id: "trip", icon: "pin", title: "Plan a trip from scratch",
    text: "Dad's flight, real Sydney train routes, then a delay.",
    steps: [
      "It's 9am on Friday 2 October. My dad has a flight at 6pm from Sydney Airport and I'll take him from Central Station.",
      "Yes, add it.",
    ],
  },
  {
    id: "airport", icon: "train", title: "Train running late", scripted: true, situation: "travel_friday",
    text: "A saved airport trip, then the train is 45 minutes late.",
    steps: ["It's 3:05pm on Friday 2 October and my train is running 45 minutes late.", "Yes, do it."],
  },
];

// --- small icon set (stroke icons, drawn with SVG) -------------------------------------------
const ICON_PATHS = {
  tv: "M3 6h18v11H3z M8 21h8 M12 17v4",
  users: "M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z M2 21a7 7 0 0 1 14 0 M17 11a3 3 0 1 0 0-6 M22 21a5 5 0 0 0-4-4.9",
  rain: "M7 15a4 4 0 1 1 1-7.9A5 5 0 0 1 17.5 9 3.5 3.5 0 0 1 17 15z M8 18l-1 2.5 M12 18l-1 2.5 M16 18l-1 2.5",
  flag: "M5 21V4 M5 4h11l-2 4 2 4H5",
  calendar: "M4 6h16v14H4z M4 10h16 M8 3v4 M16 3v4",
  pin: "M12 21s-6-5.6-6-11a6 6 0 0 1 12 0c0 5.4-6 11-6 11z M12 12.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z",
  train: "M6 4h12v11H6z M6 11h12 M8 19l-2 2 M16 19l2 2 M9 15v.01 M15 15v.01",
  pencil: "M4 20h4L19 9l-4-4L4 16z",
  search: "M10.5 17a6.5 6.5 0 1 0 0-13 6.5 6.5 0 0 0 0 13z M15.5 15.5L20 20",
  check: "M5 12.5l4.5 4.5L19 7.5",
  cross: "M6 6l12 12 M18 6L6 18",
  swap: "M4 8h14l-3-3 M20 16H6l3 3",
  link: "M9 15l6-6 M7.5 12.5l-2 2a3 3 0 0 0 4.2 4.2l2-2 M16.5 11.5l2-2a3 3 0 0 0-4.2-4.2l-2 2",
  clock: "M12 7v5l3 2 M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z",
  eye: "M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z",
  star: "M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z",
  plus: "M12 5v14 M5 12h14",
};

function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("width", "18");
  svg.setAttribute("height", "18");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", ICON_PATHS[name] || ICON_PATHS.check);
  for (const [k, v] of Object.entries({ fill: "none", stroke: "currentColor", "stroke-width": "2", "stroke-linecap": "round", "stroke-linejoin": "round" })) path.setAttribute(k, v);
  svg.append(path);
  return svg;
}

// --- state ----------------------------------------------------------------------------------
const saved = {
  get(k) { try { return sessionStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { sessionStorage.setItem(k, v); } catch { /* private mode */ } },
  remove(k) { try { sessionStorage.removeItem(k); } catch { /* ignore */ } },
};
let sessionId = saved.get("sg-session");
let situationId = null; // no plan open until the user picks one or Alexa works on one
let busy = false;
let config = null;
let scenario = null;
let scenarioStep = 0;
let speakReplies = saved.get("sg-speak") !== "off";
let position = null; // { latitude, longitude } once the user allows location
let inflight = null; // AbortController of the message being answered
const planStatus = {}; // plan id -> "ok" | "bad", for the sidebar dots
let unseenSteps = 0;
// Timeline zoom in pixels per hour; 0 fits the whole plan in the panel (and does not scroll).
const ZOOMS = [0, 40, 80, 160, 320];
const savedZoom = saved.get("sg-zoom");
let zoom = savedZoom !== null && ZOOMS.includes(Number(savedZoom)) ? Number(savedZoom) : 80;
let lastView = null;
let scrolledFor = null; // "plan|zoom" the timeline last jumped to the next thing for

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (v !== undefined && v !== null) node.setAttribute(k, v);
  }
  for (const child of children) {
    if (child == null || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

async function api(path, options = {}) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `${response.status} ${response.statusText}`);
  return body;
}

// --- time helpers: show times in the plan's own time zone -----------------------------------
const pretty = (s) => String(s || "").replace(/^c_/, "").replaceAll("_", " ");
// Engine reasons name ids and ISO times; show the action words and clock times instead.
const humanize = (text) => String(text || "")
  .replace(/'[^']+' \(([^)]+)\)/g, (_, action) => pretty(action))
  .replace(/\d{4}-\d{2}-\d{2}T(\d{2}:\d{2})(:\d{2})?([+-]\d{2}:\d{2}|Z)?/g, "$1");
const isClock = (v) => /^\d{2}:\d{2}$/.test(v || "");
const isDate = (v) => /^\d{4}-\d{2}-\d{2}$/.test(v || "");

function toMs(v) {
  if (!v) return null;
  if (isClock(v)) return (Number(v.slice(0, 2)) * 60 + Number(v.slice(3))) * 60000;
  if (isDate(v)) return Date.parse(`${v}T00:00:00Z`);
  const ms = Date.parse(v);
  return Number.isNaN(ms) ? null : ms;
}

function fmtTime(v, tz) {
  if (!v) return "–";
  if (isClock(v)) return v;
  if (isDate(v)) return v;
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return v;
  return d.toLocaleTimeString("en-AU", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: tz || undefined });
}

function fmtDay(v, tz) {
  if (!v || isClock(v)) return "";
  const d = new Date(isDate(v) ? `${v}T00:00:00Z` : v);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString("en-AU", { weekday: "short", day: "numeric", month: "short", timeZone: isDate(v) ? "UTC" : tz || undefined });
}

// --- Alexa: chat, voice, script --------------------------------------------------------------
const READY = "Ready";

function setRing(state, text) {
  $("ring").className = `orb ${state}`;
  $("ring-status").textContent = text;
}

function toast(text) {
  const t = $("toast");
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { t.hidden = true; }, 2600);
}

function addMessage(kind, text) {
  $("welcome").hidden = true;
  const item = el("li", { class: `msg ${kind}` }, text);
  $("messages").append(item);
  item.scrollIntoView({ block: "end", behavior: "smooth" });
  return item;
}

function speak(text) {
  if (!speakReplies || !("speechSynthesis" in window)) return setRing("idle", READY);
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.onstart = () => setRing("speaking", "Speaking");
  u.onend = u.onerror = () => setRing("idle", READY);
  window.speechSynthesis.speak(u);
}

async function send(text) {
  text = text.trim();
  if (!text || busy) return;
  busy = true;
  $("send").disabled = true;
  $("input").value = "";
  addMessage("user", text);
  const typing = addMessage("typing", "");
  typing.append(el("span", { class: "dots" }, el("i"), el("i"), el("i")));
  setRing("thinking", "Thinking…");
  const controller = new AbortController();
  inflight = controller;
  try {
    const result = await api("/api/chat", { method: "POST", signal: controller.signal, body: JSON.stringify({ text, session_id: sessionId, location: position }) });
    sessionId = result.session_id;
    saved.set("sg-session", sessionId);
    typing.remove();
    addMessage("assistant", result.reply);
    renderActivity(text, result.steps);
    const tokens = result.input_tokens + result.output_tokens;
    $("turn-meta").textContent = `${brainName(result.brain)}${tokens ? ` · ${tokens.toLocaleString()} tokens` : ""}${result.stopped_early ? " · stopped early" : ""}`;
    await followSituation(result.steps);
    advanceScript(text);
    speak(result.reply);
  } catch (error) {
    typing.remove();
    if (controller.signal.aborted) return; // cancelled by New conversation or Reset
    addMessage("error", `Something went wrong: ${error.message}`);
    setRing("idle", READY);
  } finally {
    if (inflight === controller) {
      inflight = null;
      busy = false;
      $("send").disabled = false;
      refreshSituation();
    }
  }
}

function setUpMicrophone() {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  const mic = $("mic");
  if (!Recognition) {
    mic.disabled = true;
    mic.title = "Voice input needs Chrome or Edge. You can still type.";
    return;
  }
  // Keep listening through short pauses; show the words as they come; send after a quiet moment.
  const SILENCE_MS = 1500;
  const rec = new Recognition();
  rec.lang = "en-AU";
  rec.continuous = true;
  rec.interimResults = true;
  let listening = false;
  let heard = "";
  let silence = null;

  const finish = () => {
    clearTimeout(silence);
    if (listening) rec.stop(); // onend sends what was heard
  };
  rec.onresult = (e) => {
    heard = Array.from(e.results, (r) => r[0].transcript).join("").trim();
    $("input").value = heard;
    setRing("listening", "Listening… click the mic to send now");
    clearTimeout(silence);
    silence = setTimeout(finish, SILENCE_MS);
  };
  rec.onend = () => {
    clearTimeout(silence);
    listening = false;
    mic.classList.remove("listening");
    const text = heard;
    heard = "";
    if (text) send(text);
    else if (!busy) setRing("idle", READY);
  };
  rec.onerror = (e) => { if (!["no-speech", "aborted"].includes(e.error)) addMessage("error", `Microphone: ${e.error}`); };
  mic.addEventListener("click", () => {
    if (listening) return finish();
    if ("speechSynthesis" in window) window.speechSynthesis.cancel();
    heard = "";
    $("input").value = "";
    listening = true;
    mic.classList.add("listening");
    setRing("listening", "Listening…");
    rec.start();
  });
}

function needsRealModel(s) {
  return config && config.brain.startsWith("scripted") && !s.scripted;
}

function renderGallery() {
  $("gallery").replaceChildren(
    ...SCENARIOS.map((s) => {
      const tag = needsRealModel(s) ? ["", "needs AI model"] : s.live ? ["live", "Live"] : s.scripted ? ["offline", "Offline"] : null;
      const card = el(
        "button", { class: `card${s.live ? " featured" : ""}`, type: "button" },
        el("span", { class: "card-icon" }, icon(s.icon)),
        el("span", { class: "card-title" }, s.title),
        el("span", { class: "card-text" }, s.text),
        tag ? el("span", { class: `tag ${tag[0]}` }, tag[1]) : null,
      );
      card.addEventListener("click", () => startScenario(s));
      return card;
    })
  );
}

async function startScenario(s) {
  await newChat(false);
  scenario = s;
  scenarioStep = 0;
  if (s.situation) await selectSituation(s.situation);
  renderScript();
  $("input").focus();
}

function renderScript() {
  const box = $("script");
  if (!scenario) { box.hidden = true; return; }
  box.hidden = false;
  $("script-title").textContent = `${scenario.title}: tap each line in order, or say it`;
  $("script-steps").replaceChildren(
    ...scenario.steps.map((text, i) => {
      const b = el("button", { class: `say${i === scenarioStep ? " next" : ""}${i < scenarioStep ? " done" : ""}`, type: "button" },
        el("span", { class: "say-num" }, i < scenarioStep ? "✓" : `${i + 1}`), el("span", {}, `“${text}”`));
      b.addEventListener("click", () => send(text));
      return b;
    })
  );
}

function advanceScript(sentText) {
  if (!scenario) return;
  const i = scenario.steps.indexOf(sentText);
  if (i >= 0) scenarioStep = Math.max(scenarioStep, i + 1);
  renderScript();
}

function cancelReply() {
  if (inflight) inflight.abort();
  inflight = null;
  busy = false;
  $("send").disabled = false;
}

async function newChat(clearScenario = true) {
  cancelReply();
  sessionId = null;
  saved.remove("sg-session");
  $("messages").replaceChildren();
  $("welcome").hidden = false;
  $("activity").replaceChildren(el("li", { class: "empty" }, "Nothing yet. Start a conversation and each step will appear here."));
  $("turn-meta").textContent = "";
  unseenSteps = 0;
  showCount();
  if (clearScenario) { scenario = null; renderScript(); }
  if ("speechSynthesis" in window) window.speechSynthesis.cancel();
  setRing("idle", READY);
}

// --- activity feed: every MCP tool call, in plain words ---------------------------------------
function describe(step) {
  const a = step.arguments || {};
  const r = step.result || {};
  const d = { cls: "", icon: "check", title: pretty(step.tool), details: [], reason: null, notes: [], options: [], badge: null };
  if (step.is_error) {
    return { ...d, cls: "bad", icon: "cross", title: `Couldn’t ${pretty(step.tool)}`, reason: r.error || "The tool refused", badge: ["bad", "refused"] };
  }
  const when = (v) => (v === "now" ? "now" : `${fmtDay(v)} ${fmtTime(v)}`.trim());
  switch (step.tool) {
    case "list_situations": return { ...d, icon: "eye", title: "Looked at your plans", details: [`${(r.result || []).length} plans`] };
    case "get_situation": return { ...d, icon: "eye", title: "Read the plan", badge: r.feasible ? ["ok", "works"] : ["bad", "needs attention"] };
    case "create_situation": return { ...d, icon: "plus", title: `Started a new plan: ${a.name}`, details: a.now ? [`Current time ${when(a.now)}`] : [] };
    case "add_place": {
      const marks = [a.outdoor && "outdoor", a.closes_on_public_holidays && "closes on public holidays"].filter(Boolean);
      return { ...d, icon: "pin", title: `Noted a place: ${a.name}`, details: marks.length ? [marks.join(" · ")] : [], notes: r.notes || [] };
    }
    case "add_resource": return { ...d, icon: "tv", title: `Noted a shared thing: ${a.name}`, details: [`only ${a.capacity || 1} at a time`] };
    case "add_commitment": {
      const parts = [a.owner, pretty(a.action)];
      if (a.start) parts.push(when(a.start) + (a.duration_minutes ? ` for ${a.duration_minutes} min` : ""));
      else if (a.from_place) parts.push("time to be found");
      const out = { ...d, icon: "pencil", title: "Wrote down", details: [parts.join(" · ")] };
      if (r.feasible === false) return { ...out, cls: "bad", badge: ["bad", "clash"], reason: (r.reasons || [])[0] };
      return { ...out, badge: ["ok", "fits"] };
    }
    case "add_goal": return { ...d, icon: "star", title: "Marked what matters most", details: [a.description] };
    case "add_dependency": return { ...d, icon: "link", title: "Linked two steps", details: [`${pretty(a.commitment_id)} needs ${pretty(a.needs)} first`] };
    case "report_change": {
      const what = a.kind === "delay" ? `${pretty(a.commitment_id)} delayed ${a.minutes} min` : `${pretty(a.commitment_id)} cancelled`;
      const out = { ...d, icon: "clock", title: "Recorded a change", details: [what] };
      return r.feasible_after ? { ...out, cls: "ok", badge: ["ok", "still works"] } : { ...out, cls: "bad", badge: ["bad", "plan broken"], reason: (r.reasons || [])[0] };
    }
    case "find_options":
      return { ...d, cls: "find", icon: "search", title: `Found ${(r.options || []).length} alternatives`, details: [r.source || ""], options: (r.options || []).map((o) => o.summary), notes: r.notes || [] };
    case "try_option":
      return r.feasible
        ? { ...d, cls: "ok", icon: "check", title: `Engine checked option ${a.label}`, details: [r.summary], badge: ["ok", "works"] }
        : { ...d, cls: "bad", icon: "cross", title: `Engine checked option ${a.label}`, details: [r.summary], badge: ["bad", r.applied === false ? "can't apply" : "doesn't work"], reason: (r.reasons || [])[0] };
    case "confirm_option": {
      const ok = r.confirmed && r.verified;
      return { ...d, cls: ok ? "ok" : "bad", icon: ok ? "swap" : "cross", title: ok ? `Switched the plan to option ${a.label}` : `Didn’t switch to option ${a.label}`, details: r.calendar_updates || [], badge: ok ? ["ok", "verified"] : ["bad", "refused"], reason: ok ? null : (r.reasons || [])[0] };
    }
    case "get_weather": {
      const day = r.day;
      const lines = [`${r.temperature_c ?? "–"}°C now, ${r.conditions}${r.feels_like_c != null ? ` (feels like ${r.feels_like_c}°C)` : ""}`];
      if (day) lines.push(`${day.date}: ${day.conditions}, ${day.min_c ?? "–"}–${day.max_c ?? "–"}°C, ${day.rain_chance ?? "–"}% chance of rain`);
      return { ...d, cls: "world", icon: "rain", title: `Weather in ${r.place}`, details: lines, badge: r.live ? ["ok", "live"] : null, notes: [r.attribution].filter(Boolean) };
    }
    case "add_to_calendar":
      return { ...d, cls: "ok", icon: "calendar", title: "Added to your Google Calendar", details: [`${r.title} · ${fmtDay(r.start)} ${fmtTime(r.start)}–${fmtTime(r.end)}`], badge: ["ok", "saved"] };
    case "check_weather":
    case "check_public_holidays":
    case "import_calendar": {
      const names = { check_weather: ["rain", "Checked the weather"], check_public_holidays: ["flag", "Checked public holidays"], import_calendar: ["calendar", r.source === "google-calendar" ? "Read your Google Calendar" : "Imported your calendar"] };
      const [ic, title] = names[step.tool];
      const broke = r.feasible_before && !r.feasible_after;
      return {
        ...d, cls: broke ? "bad" : "world", icon: ic, title, details: [...(r.changes && r.changes.length ? r.changes : r.advice && r.advice.length ? [] : ["nothing new"]), ...(r.advice || [])],
        badge: broke ? ["bad", "plan broken"] : null, reason: broke ? (r.reasons || [])[0] : null,
        notes: [...(r.notes || []), r.attribution].filter(Boolean),
      };
    }
    default: return d;
  }
}

function showCount() {
  const c = $("activity-count");
  c.hidden = !unseenSteps;
  c.textContent = unseenSteps;
}

function renderActivity(said, steps) {
  if (!$("tab-activity").classList.contains("active")) { unseenSteps += steps.length; showCount(); }
  const list = $("activity");
  list.querySelector(".empty")?.remove();
  list.append(el("li", { class: "turn" }, `You: “${said}”`));
  if (!steps.length) {
    list.append(el("li", { class: "empty" }, "Answered without needing any tools."));
  }
  list.append(
    ...steps.map((step) => {
      const d = describe(step);
      const body = el("div", { class: "act-body" },
        el("div", { class: "act-title" }, d.title, d.badge ? el("span", { class: `badge ${d.badge[0]}` }, d.badge[1]) : null, el("span", { class: "act-tool" }, step.tool)));
      d.details.filter(Boolean).forEach((t) => body.append(el("p", { class: "act-detail" }, humanize(t))));
      if (d.options.length) body.append(el("ul", { class: "act-options" }, ...d.options.map((o) => el("li", {}, o))));
      if (d.reason) body.append(el("p", { class: "act-reason" }, humanize(d.reason)));
      d.notes.forEach((n) => body.append(el("p", { class: "act-note" }, n)));
      return el("li", { class: `act ${d.cls}` }, el("span", { class: "act-icon" }, icon(d.icon)), body);
    })
  );
}

// --- the plan: status, goals, timeline -------------------------------------------------------
async function followSituation(steps) {
  const touched = [...steps].reverse().map((s) => (s.result && s.result.situation_id) || (s.arguments && s.arguments.situation_id)).find(Boolean);
  if (touched && touched !== situationId) await selectSituation(touched);
}

async function selectSituation(id) {
  situationId = id;
  await loadConfig();
}

function closePlan() {
  situationId = null;
  if (config) renderPlanList();
  refreshSituation();
}

function currentPlan() {
  return (config && config.situations.find((p) => p.id === situationId)) || null;
}

function renderPlanList() {
  $("plan-list").replaceChildren(
    ...config.situations.map((p) => {
      const b = el("button", { class: `plan-item${p.id === situationId ? " active" : ""}`, type: "button" },
        el("span", { class: `pi-dot ${planStatus[p.id] || ""}` }),
        el("span", { class: "pi-name" }, p.name),
        p.predefined ? el("span", { class: "pi-tag" }, "sample") : null);
      b.addEventListener("click", () => {
        situationId = p.id;
        closeNav();
        renderPlanList();
        refreshSituation();
      });
      return el("li", {}, b);
    })
  );
  const plan = currentPlan();
  $("reset").hidden = !plan;
  $("reset").textContent = plan && !plan.predefined ? "Delete plan" : "Reset plan";
}

function renderNoPlan() {
  lastView = null;
  $("plan-name").textContent = "No plan open";
  $("sit-status").hidden = true;
  $("plan-dot").className = "mini-dot";
  $("sit-now").textContent = "Pick a plan on the left, or tell Alexa what you’re planning.";
  $("goals").replaceChildren();
  $("unscheduled").hidden = true;
  $("tl-hint").textContent = "";
  $("timeline").style.removeProperty("--track-w");
  $("timeline").replaceChildren(el("div", { class: "tl-empty" }, "No plan open yet."));
}

async function refreshSituation() {
  if (!situationId) { renderNoPlan(); return; }
  try {
    const { view } = await api(`/api/situations/${encodeURIComponent(situationId)}`);
    renderPlan(view);
  } catch (error) {
    $("sit-status").textContent = "Unavailable";
    $("sit-status").className = "verdict";
    $("timeline").replaceChildren(el("div", { class: "tl-empty" }, error.message));
  }
}

function renderPlan(view) {
  const tz = view.timezone;
  const live = view.commitments.filter((c) => c.status !== "cancelled");
  const pill = $("sit-status");
  pill.hidden = false;
  const n = view.broken_commitment_ids.length;
  const status = !live.length ? "" : view.feasible ? "ok" : "bad";
  pill.textContent = !live.length ? "Empty" : view.feasible ? "Works" : `${n} problem${n === 1 ? "" : "s"}`;
  pill.className = `verdict ${status}`;
  $("plan-dot").className = `mini-dot ${status}`;
  $("plan-name").textContent = view.name;
  if (planStatus[view.id] !== status) {
    planStatus[view.id] = status;
    if (config) renderPlanList();
  }
  $("sit-now").textContent = view.now ? `${fmtDay(view.now, tz)} · ${fmtTime(view.now, tz)}${tz ? ` · ${tz.replace("_", " ")}` : ""}` : "No current time set";

  $("goals").replaceChildren(
    ...view.goals.map((g) => el("span", { class: `goal ${g.at_risk ? "at-risk" : "safe"}` }, icon("star"), g.description, g.at_risk ? " — at risk" : ""))
  );
  renderTimeline(view);
}

function renderTimeline(view) {
  lastView = view;
  const tz = view.timezone;
  const broken = new Set(view.broken_commitment_ids);
  const timed = view.commitments.filter((c) => toMs(c.start) != null);
  const unscheduled = view.commitments.filter((c) => toMs(c.start) == null && c.status !== "cancelled");
  const blocks = (view.places || []).flatMap((p) => p.unavailable.map((w) => ({ place: p, ...w })));

  $("unscheduled").hidden = !unscheduled.length;
  $("unscheduled").replaceChildren(el("span", {}, "Not scheduled yet:"), ...unscheduled.map((c) => el("span", { class: "chip" }, `${c.owner_id} · ${pretty(c.title || c.action)}`)));

  const box = $("timeline");
  $("tl-hint").textContent = "";
  if (!timed.length && !blocks.length) {
    box.style.removeProperty("--track-w");
    box.replaceChildren(el("div", { class: "tl-empty" }, "Nothing on the timeline yet. Tell Alexa what you’re planning."));
    return;
  }

  // Range: everything planned, the place closures that touch it, and now. Padded a little.
  const points = timed.flatMap((c) => [toMs(c.start), toMs(c.end) ?? toMs(c.start)]);
  let lo = Math.min(...points), hi = Math.max(...points);
  if (!points.length) { lo = Math.min(...blocks.map((b) => toMs(b.start))); hi = Math.max(...blocks.map((b) => toMs(b.end))); }
  const nowMs = toMs(view.now);
  if (nowMs != null && nowMs >= lo - 6 * 3600e3 && nowMs <= hi) lo = Math.min(lo, nowMs);
  const span0 = Math.max(hi - lo, 3600e3);
  lo -= span0 * 0.06; hi += span0 * 0.06;
  const span = hi - lo;
  const pct = (ms) => `${Math.max(0, Math.min(100, ((ms - lo) / span) * 100))}%`;
  const width = (a, b) => `${Math.max(0.8, ((Math.min(b, hi) - Math.max(a, lo)) / span) * 100)}%`;

  // Track width: the panel's width, or wider (scrolls sideways) at the chosen pixels per hour.
  const fitPx = Math.max(200, (box.clientWidth || 600) - 98);
  const trackPx = zoom ? Math.max(fitPx, Math.round((span / 3600e3) * zoom)) : fitPx;
  box.style.setProperty("--track-w", `${trackPx}px`);
  if (trackPx > fitPx) $("tl-hint").textContent = "Scroll sideways to see the whole day →";

  // Ticks: a round step with about 80px between labels.
  const steps = [15, 30, 60, 120, 180, 360, 720, 1440, 2880].map((m) => m * 60000);
  const maxTicks = Math.max(2, Math.floor(trackPx / 80));
  const step = steps.find((s) => span / s <= maxTicks) || steps[steps.length - 1];
  const naive = timed.every((c) => isClock(c.start));
  const ticks = [];
  for (let t = Math.ceil(lo / step) * step; t <= hi; t += step) ticks.push(t);
  let lastDay = null;
  const tickLabel = (t) => {
    if (naive) { const m = Math.round(t / 60000) % 1440; return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`; }
    const iso = new Date(t).toISOString();
    const day = fmtDay(iso, tz);
    if (step >= 1440 * 60000) return day;
    const showDay = span > 20 * 3600e3 && day !== lastDay;
    lastDay = day;
    return showDay ? `${day} ${fmtTime(iso, tz)}` : fmtTime(iso, tz);
  };
  const labels = ticks.map(tickLabel);
  const tickLines = (withLabels) => ticks.map((t, i) => el("div", { class: "tick", style: `left:${pct(t)}` }, withLabels ? el("span", {}, labels[i]) : null));
  const nowLine = () => (nowMs != null && nowMs >= lo && nowMs <= hi ? el("div", { class: "now-marker", style: `left:${pct(nowMs)}` }) : null);

  const rows = [el("div", { class: "tl-axis" }, el("div", { class: "tl-label" }, "Who"), el("div", { class: "tl-track" }, ...tickLines(true)))];

  const owners = [...new Set(timed.map((c) => c.owner_id))];
  for (const owner of owners) {
    const items = timed.filter((c) => c.owner_id === owner).map((c) => {
      const s = toMs(c.start), e = toMs(c.end) ?? s;
      const cls = c.status === "cancelled" ? "off" : broken.has(c.id) ? "bad" : "";
      const label = pretty(c.title || c.action);
      const tip = `${owner} · ${label}\n${fmtDay(c.start, tz)} ${fmtTime(c.start, tz)}${e > s ? `–${fmtTime(c.end, tz)}` : ""}\n${c.status}${c.resource_ids.length ? ` · uses ${c.resource_ids.join(", ")}` : ""}`;
      if (e <= s) return el("div", { class: `instant ${cls}`, style: `left:${pct(s)}`, title: tip });
      return el("div", { class: `bar ${cls}`, style: `left:${pct(s)};width:${width(s, e)}`, title: tip }, label);
    });
    rows.push(el("div", { class: "tl-row" }, el("div", { class: "tl-label", title: owner }, owner), el("div", { class: "tl-track" }, ...tickLines(false), ...items, nowLine())));
  }

  const blockedPlaces = [...new Set(blocks.map((b) => b.place.id))];
  for (const pid of blockedPlaces) {
    const mine = blocks.filter((b) => b.place.id === pid);
    const items = mine.map((b) => el("div", { class: "bar blocked", style: `left:${pct(toMs(b.start))};width:${width(toMs(b.start), toMs(b.end))}`, title: `${b.place.name}: ${b.reason}` }, b.reason));
    rows.push(el("div", { class: "tl-row" }, el("div", { class: "tl-label place", title: mine[0].place.name }, mine[0].place.name), el("div", { class: "tl-track" }, ...tickLines(false), ...items, nowLine())));
  }
  box.replaceChildren(...rows);

  // Open on what is coming up next (not on an empty evening), once per plan and zoom.
  const key = `${view.id}|${zoom}`;
  if (scrolledFor !== key && box.clientWidth > 0) {
    scrolledFor = key;
    const ahead = timed
      .filter((c) => c.status !== "cancelled" && (nowMs == null || (toMs(c.end) ?? toMs(c.start)) >= nowMs))
      .map((c) => toMs(c.start));
    const focus = ahead.length ? Math.min(...ahead) : nowMs ?? lo;
    box.scrollLeft = Math.max(0, ((focus - lo) / span) * trackPx - 60);
  }
}

function setZoom(next) {
  zoom = next;
  saved.set("sg-zoom", String(zoom));
  const i = ZOOMS.indexOf(zoom);
  $("zoom-out").disabled = i <= 1;
  $("zoom-in").disabled = i === ZOOMS.length - 1;
  $("zoom-fit").setAttribute("aria-pressed", String(zoom === 0));
  if (lastView) renderTimeline(lastView);
}

function setWide(on) {
  $("app").dataset.wide = String(on);
  const button = $("expand-plan");
  button.setAttribute("aria-pressed", String(on));
  const label = on ? "Make the plan panel narrower" : "Make the plan panel wider";
  button.setAttribute("aria-label", label);
  button.title = label;
  saved.set("sg-wide", on ? "on" : "off");
  if (lastView) renderTimeline(lastView);
}

// --- settings and start-up ----------------------------------------------------------------------
function brainName(brain) {
  if (!brain) return "";
  if (brain.startsWith("scripted")) return "Free demo brain";
  if (brain.startsWith("openai:")) return brain.slice(7).toUpperCase().replace("GPT-", "GPT-");
  if (brain.startsWith("bedrock:")) return brain.includes("nova") ? "Amazon Nova" : "Amazon Bedrock";
  return brain;
}

function setConn(rowId, labelId, text, state) {
  $(labelId).textContent = text;
  $(rowId).className = state;
}

async function loadConfig() {
  try {
    config = await api("/api/config");
  } catch (error) {
    addMessage("error", `Could not load settings: ${error.message}`);
    return;
  }
  setConn("conn-brain", "brain-label", brainName(config.brain), config.brain_live ? "on" : "");
  const modes = config.modes || {};
  const live = ["weather", "travel", "places"].filter((k) => modes[k] === "live");
  setConn("conn-data", "data-label", live.length === 3 ? "Live" : live.length ? `Live: ${live.join(", ")}` : "Recorded samples", live.length ? "on" : "");
  const cal = { google: ["Google Calendar", "on"], "google-signed-out": ["Google: not signed in", "warn"], off: ["Not connected", ""] }[config.calendar] || ["Not connected", ""];
  setConn("conn-cal", "cal-label", cal[0], cal[1]);

  const note = $("brain-note");
  if (config.brain.startsWith("scripted")) {
    note.hidden = false;
    note.replaceChildren("Alexa is running on the free demo brain, which only knows the “Train running late” story. For everything else set ", el("code", {}, "SITUATION_GUARD_LLM=openai"), " in .env and restart.");
  } else note.hidden = true;
  renderGallery();

  if (situationId && !config.situations.some((p) => p.id === situationId)) situationId = null;
  renderPlanList();
  refreshSituation();
}

function setUpControls() {
  $("composer").addEventListener("submit", (e) => { e.preventDefault(); send($("input").value); });
  $("new-chat").addEventListener("click", async () => {
    await newChat(true);
    closePlan();
    closeNav();
    setView("chat");
    toast("New conversation started. Your plans are kept.");
    $("input").focus();
  });
  for (const [id, panel] of [["tab-timeline", "panel-timeline"], ["tab-activity", "panel-activity"]]) {
    $(id).addEventListener("click", () => {
      for (const t of ["tab-timeline", "tab-activity"]) {
        $(t).classList.toggle("active", t === id);
        $(t).setAttribute("aria-selected", String(t === id));
      }
      $("panel-timeline").hidden = panel !== "panel-timeline";
      $("panel-activity").hidden = panel !== "panel-activity";
      if (panel === "panel-activity") { unseenSteps = 0; showCount(); }
      else refreshSituation();
    });
  }
  $("zoom-in").addEventListener("click", () => setZoom(ZOOMS[Math.min(ZOOMS.length - 1, ZOOMS.indexOf(zoom) + 1)]));
  $("zoom-out").addEventListener("click", () => setZoom(ZOOMS[Math.max(1, ZOOMS.indexOf(zoom) - 1)]));
  $("zoom-fit").addEventListener("click", () => setZoom(zoom === 0 ? 80 : 0));
  setZoom(zoom);
  $("expand-plan").addEventListener("click", () => setWide($("app").dataset.wide !== "true"));
  setWide(saved.get("sg-wide") === "on");
  let resizeTimer = null;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => { if (lastView) renderTimeline(lastView); }, 150);
  });
  $("show-plan").addEventListener("click", () => setView("plan"));
  $("back-chat").addEventListener("click", () => setView("chat"));
  $("nav-open").addEventListener("click", () => $("app").classList.add("nav-open"));
  $("nav-close").addEventListener("click", closeNav);
  $("scrim").addEventListener("click", closeNav);
  $("script-close").addEventListener("click", () => { scenario = null; renderScript(); });
  $("how-btn").addEventListener("click", () => $("how").showModal());
  const toggle = $("speak-toggle");
  toggle.setAttribute("aria-pressed", String(speakReplies));
  toggle.addEventListener("click", () => {
    speakReplies = !speakReplies;
    toggle.setAttribute("aria-pressed", String(speakReplies));
    saved.set("sg-speak", speakReplies ? "on" : "off");
    if (!speakReplies && "speechSynthesis" in window) window.speechSynthesis.cancel();
  });
  $("reset").addEventListener("click", async () => {
    const plan = currentPlan();
    if (!plan) return;
    const deleting = !plan.predefined;
    $("confirm-title").textContent = deleting ? `Delete “${plan.name}”?` : `Reset “${plan.name}”?`;
    $("confirm-text").textContent = deleting
      ? "This plan was made in conversation. It will be removed, and the conversation starts over. Your Google Calendar is not changed."
      : "This sample plan goes back to how it started, and the conversation starts over.";
    $("confirm-ok").textContent = deleting ? "Delete plan" : "Reset plan";
    const answer = await ask($("confirm"));
    if (answer !== "ok") return;
    cancelReply();
    try {
      const r = await api("/api/reset", { method: "POST", body: JSON.stringify({ situation_id: plan.id, session_id: sessionId }) });
      if (r.outcome === "deleted") { delete planStatus[plan.id]; situationId = null; }
    } catch (error) {
      toast(`Couldn’t reset: ${error.message}`);
      return;
    }
    await newChat(false);
    await loadConfig();
    toast(deleting ? "Plan deleted." : "Plan reset to its starting point.");
  });
}

// Resolves with the value of the button pressed ("cancel" for Escape or a click outside).
function ask(dialog) {
  return new Promise((resolve) => {
    const done = (value) => {
      dialog.removeEventListener("click", onClick);
      dialog.removeEventListener("cancel", onCancel);
      if (dialog.open) dialog.close(value);
      resolve(value);
    };
    const onClick = (e) => {
      const button = e.target.closest("button[value]");
      if (button) { e.preventDefault(); done(button.value); }
      else if (e.target === dialog) done("cancel");
    };
    const onCancel = (e) => { e.preventDefault(); done("cancel"); };
    dialog.addEventListener("click", onClick);
    dialog.addEventListener("cancel", onCancel);
    dialog.showModal();
  });
}

function setView(view) { $("app").dataset.view = view; if (view === "plan") refreshSituation(); }
function closeNav() { $("app").classList.remove("nav-open"); }

// Where the user is: the browser asks once; Alexa uses it for "here", trips and weather.
function setUpLocation() {
  if (!("geolocation" in navigator)) { setConn("conn-loc", "loc-label", "Not available", ""); return; }
  setConn("conn-loc", "loc-label", "Asking…", "warn");
  navigator.geolocation.watchPosition(
    (p) => {
      position = { latitude: p.coords.latitude, longitude: p.coords.longitude };
      setConn("conn-loc", "loc-label", `Shared · ±${Math.round(p.coords.accuracy)} m`, "on");
    },
    () => { position = null; setConn("conn-loc", "loc-label", "Off: allow it in the browser", ""); },
    { enableHighAccuracy: false, maximumAge: 300000, timeout: 20000 }
  );
}

setUpControls();
setUpMicrophone();
setUpLocation();
loadConfig();
