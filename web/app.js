// Situation Guard web demo. Plain JavaScript: no framework, no build step.
// Flow: you type or speak -> POST /api/chat -> server runs Alexa (run_turn) over the MCP tools
// -> reply + steps come back -> chat shows the reply, the proof panel shows every step.

const $ = (id) => document.getElementById(id);

const SUGGESTIONS = [
  "It's 3:05pm on Friday 2 October and my train is running 45 minutes late.",
  "What's my current plan?",
  "Yes, do it.",
];

const store = {
  get(key) { try { return sessionStorage.getItem(key); } catch { return null; } },
  set(key, value) { try { sessionStorage.setItem(key, value); } catch { /* private mode: fine */ } },
  remove(key) { try { sessionStorage.removeItem(key); } catch { /* ignore */ } },
};

let sessionId = store.get("sg-session");
let situationId = store.get("sg-situation") || "travel_friday";
let busy = false;

// --- small helpers ------------------------------------------------------------

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else node.setAttribute(k, v);
  }
  for (const child of children) {
    if (child == null) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function clock(iso) {
  if (!iso) return "–";
  return iso.length >= 16 && iso[10] === "T" ? iso.slice(11, 16) : iso;
}

function day(iso) {
  if (!iso || iso.length < 10) return "";
  const d = new Date(iso);
  return isNaN(d) ? iso.slice(0, 10) : d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
}

function setRing(state, text) {
  $("ring").className = `ring ${state}`;
  $("ring-status").textContent = text;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `${response.status} ${response.statusText}`);
  return body;
}

// --- chat ---------------------------------------------------------------------

function addMessage(kind, text) {
  const item = el("li", { class: `msg ${kind}` }, text);
  $("messages").append(item);
  item.scrollIntoView({ block: "end", behavior: "smooth" });
}

function speak(text) {
  if (!$("speak-replies").checked || !("speechSynthesis" in window)) {
    setRing("idle", "Ready");
    return;
  }
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.onstart = () => setRing("speaking", "Speaking");
  utterance.onend = () => setRing("idle", "Ready");
  utterance.onerror = () => setRing("idle", "Ready");
  window.speechSynthesis.speak(utterance);
}

async function send(text) {
  text = text.trim();
  if (!text || busy) return;
  busy = true;
  $("send").disabled = true;
  $("input").value = "";
  addMessage("user", text);
  setRing("thinking", "Thinking…");

  try {
    const result = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({ text, session_id: sessionId }),
    });
    sessionId = result.session_id;
    store.set("sg-session", sessionId);
    addMessage("assistant", result.reply);
    renderSteps(result.steps);
    $("turn-meta").textContent =
      `${result.brain} · ${result.input_tokens + result.output_tokens} tokens` + (result.stopped_early ? " · stopped early" : "");
    speak(result.reply);
  } catch (error) {
    addMessage("error", `Something went wrong: ${error.message}`);
    setRing("idle", "Ready");
  } finally {
    busy = false;
    $("send").disabled = false;
    refreshSituation();
  }
}

// --- proof panel: steps -------------------------------------------------------

function verdict(ok, text) {
  return el("span", { class: `verdict ${ok ? "ok" : "bad"}` }, `${ok ? "✓" : "✗"} ${text}`);
}

function describeStep(step) {
  const a = step.arguments || {};
  const r = step.result || {};
  const head = el("div", { class: "step-head" }, el("span", { class: "tool" }, step.tool));
  const item = el("li", { class: "step info" }, head);

  if (step.is_error) {
    item.className = "step bad";
    head.append(verdict(false, "error"));
    item.append(el("p", { class: "reason" }, r.error || "Tool failed"));
    return item;
  }

  switch (step.tool) {
    case "report_change": {
      const what = a.kind === "delay" ? `${a.commitment_id} delayed ${a.minutes} min` : `${a.commitment_id} ${a.kind}`;
      item.append(el("p", {}, `Recorded: ${what}, noticed at ${clock(a.observed_at)}.`));
      if (r.feasible_after) {
        item.className = "step ok";
        head.append(verdict(true, "plan still works"));
      } else {
        item.className = "step bad";
        head.append(verdict(false, "plan broken"));
        if (r.reasons && r.reasons[0]) item.append(el("p", { class: "reason" }, r.reasons[0]));
        if (r.goals_at_risk && r.goals_at_risk.length) item.append(el("p", {}, `Goals at risk: ${r.goals_at_risk.join(", ")}`));
      }
      break;
    }
    case "find_options": {
      const options = r.options || [];
      head.append(el("span", { class: "muted small" }, `${options.length} options · ${r.live ? "live" : "recorded"} ${r.source || ""}`));
      item.append(el("ul", { class: "options" }, ...options.map((o) => el("li", {}, o.summary))));
      for (const note of r.notes || []) item.append(el("p", { class: "note" }, note));
      break;
    }
    case "try_option": {
      item.className = `step ${r.feasible ? "ok" : "bad"}`;
      head.append(verdict(r.feasible === true, r.feasible ? "works" : r.applied === false ? "could not apply" : "does not work"));
      item.append(el("p", {}, r.summary || `Option ${a.label}`));
      if (!r.feasible && r.reasons && r.reasons[0]) item.append(el("p", { class: "reason" }, r.reasons[0]));
      break;
    }
    case "confirm_option": {
      const ok = r.confirmed && r.verified;
      item.className = `step ${ok ? "ok" : "bad"}`;
      head.append(verdict(ok, ok ? "switched and verified" : r.confirmed ? "switched, re-check failed" : "refused"));
      if (!ok && r.reasons && r.reasons[0]) item.append(el("p", { class: "reason" }, r.reasons[0]));
      break;
    }
    case "get_situation":
      item.append(el("p", { class: "muted" }, `Read the plan: ${r.feasible ? "works" : "needs attention"}.`));
      break;
    case "list_situations":
      item.append(el("p", { class: "muted" }, `Listed ${(r.result || []).length} situations.`));
      break;
    default:
      item.append(el("p", { class: "muted" }, JSON.stringify(r).slice(0, 200)));
  }
  return item;
}

function renderSteps(steps) {
  const list = $("steps");
  list.replaceChildren();
  if (!steps.length) {
    list.append(el("li", { class: "empty" }, "Alexa answered without calling any tools."));
    return;
  }
  steps.forEach((step) => list.append(describeStep(step)));
}

// --- proof panel: situation ---------------------------------------------------

async function refreshSituation() {
  try {
    const { view } = await api(`/api/situations/${encodeURIComponent(situationId)}`);
    $("sit-name").textContent = view.name;
    const status = $("sit-status");
    status.textContent = view.feasible ? "Plan works" : "Plan broken";
    status.className = `pill ${view.feasible ? "ok" : "bad"}`;
    $("sit-now").textContent = view.now ? `Now: ${day(view.now)} ${clock(view.now)}` : "Time: not set yet";

    $("goals").replaceChildren(
      ...view.goals.map((g) =>
        el("li", {}, el("span", { class: `verdict ${g.at_risk ? "bad" : "ok"}` }, g.at_risk ? "✗ at risk" : "✓ safe"), g.description)
      )
    );

    const broken = new Set(view.broken_commitment_ids);
    const commitments = [...view.commitments].sort((x, y) => String(x.start).localeCompare(String(y.start)));
    $("plan").replaceChildren(
      ...commitments.map((c) => {
        const cls = c.status === "cancelled" ? "cancelled" : broken.has(c.id) ? "broken" : c.id.startsWith("c_travel_") ? "new" : "";
        const span = c.end && c.end !== c.start ? `${clock(c.start)}–${clock(c.end)}` : clock(c.start);
        return el(
          "li",
          { class: cls },
          el("span", { class: "time" }, span),
          el("span", { class: "what" }, c.action.replaceAll("_", " ")),
          el("span", { class: "status" }, c.status)
        );
      })
    );
  } catch (error) {
    $("sit-name").textContent = "Situation unavailable";
    $("sit-now").textContent = error.message;
  }
}

// --- voice input --------------------------------------------------------------

function setUpMicrophone() {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  const mic = $("mic");
  if (!Recognition) {
    mic.disabled = true;
    mic.title = "Voice input needs Chrome or Edge. You can still type.";
    return;
  }
  const recognition = new Recognition();
  recognition.lang = "en-AU";
  recognition.interimResults = false;
  let listening = false;

  recognition.onresult = (event) => send(event.results[0][0].transcript);
  recognition.onend = () => {
    listening = false;
    mic.classList.remove("listening");
    if (!busy) setRing("idle", "Ready");
  };
  recognition.onerror = (event) => {
    if (event.error !== "no-speech" && event.error !== "aborted") addMessage("error", `Microphone: ${event.error}`);
  };

  mic.addEventListener("click", () => {
    if (listening) return recognition.stop();
    if ("speechSynthesis" in window) window.speechSynthesis.cancel();
    listening = true;
    mic.classList.add("listening");
    setRing("listening", "Listening…");
    recognition.start();
  });
}

// --- start --------------------------------------------------------------------

async function loadConfig() {
  try {
    const config = await api("/api/config");
    const brain = $("brain-badge");
    brain.textContent = `brain: ${config.brain}`;
    brain.classList.toggle("live", config.brain_live);
    const travel = $("travel-badge");
    travel.textContent = `travel: ${config.travel}`;
    travel.classList.toggle("live", config.travel === "live");

    const select = $("situation-select");
    select.replaceChildren(...config.situations.map((s) => el("option", { value: s.id }, s.name)));
    if (!config.situations.some((s) => s.id === situationId) && config.situations.length) situationId = config.situations[0].id;
    select.value = situationId;
  } catch (error) {
    addMessage("error", `Could not load settings: ${error.message}`);
  }
}

function setUpControls() {
  $("composer").addEventListener("submit", (event) => {
    event.preventDefault();
    send($("input").value);
  });
  $("chips").replaceChildren(
    ...SUGGESTIONS.map((text) => {
      const chip = el("button", { class: "chip", type: "button" }, text);
      chip.addEventListener("click", () => send(text));
      return chip;
    })
  );
  $("situation-select").addEventListener("change", (event) => {
    situationId = event.target.value;
    store.set("sg-situation", situationId);
    refreshSituation();
  });
  $("reset").addEventListener("click", async () => {
    if (busy) return;
    try {
      await api("/api/reset", { method: "POST", body: JSON.stringify({ situation_id: situationId, session_id: sessionId }) });
    } catch (error) {
      addMessage("error", `Reset failed: ${error.message}`);
      return;
    }
    sessionId = null;
    store.remove("sg-session");
    $("messages").replaceChildren();
    $("steps").replaceChildren(el("li", { class: "empty" }, "Nothing yet. Tell Alexa what changed."));
    $("turn-meta").textContent = "";
    if ("speechSynthesis" in window) window.speechSynthesis.cancel();
    setRing("idle", "Ready");
    refreshSituation();
  });
}

setUpControls();
setUpMicrophone();
loadConfig().then(refreshSituation);
