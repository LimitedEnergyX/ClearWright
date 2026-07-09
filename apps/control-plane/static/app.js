"use strict";

// ClearWright control plane demo, front end.
// Vanilla JS only. Renders the clearance board from /api/state and runs operator
// actions through /api/action, which invokes the real ClearWright tools locally.

const LANE_TITLES = {
  clearance_outbox: "clearance_outbox",
  clearance_in_progress: "clearance_in_progress",
  clearance_done: "clearance_done",
  clearance_failed: "clearance_failed",
};

const ACTION_LABELS = {
  cta: "Grant CTA",
  dta: "Deny DTA",
  rfi: "Request RFI",
  claim: "Claim cleared work",
  complete: "Mark DONE",
  fail: "Mark FAILED",
};

const ACTION_CLASS = {
  cta: "btn-ok",
  dta: "btn-deny",
  rfi: "btn-pending",
  claim: "btn-primary",
  complete: "btn-ok",
  fail: "btn-deny",
};

const REASON_ACTIONS = new Set(["dta", "rfi", "fail"]);

// Operator mode is the live local console: no demo seeding, no reset, real
// local agent events as the primary feed. Demo mode is the walkthrough.
const OPERATOR_EMPTY_REQUESTS = "No active clearance requests.";
const OPERATOR_EMPTY_EVENTS =
  "No local agent events yet. Agents and tools can submit events through the local adapter.";
let currentMode = "demo";

function esc(value) {
  if (value === null || value === undefined) return "";
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

async function getJSON(url) {
  const res = await fetch(url);
  return res.json();
}

async function postJSON(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  return res.json();
}

function setActivity(text) {
  document.getElementById("activity-log").textContent = text;
}

// --------------------------------------------------------------------------- //
// Mission intake
// --------------------------------------------------------------------------- //

function renderMission(mission) {
  const grid = document.getElementById("mission-grid");
  if (!mission || !Object.keys(mission).length) {
    grid.innerHTML = '<p class="muted">No mission loaded.</p>';
    return;
  }
  const list = (arr) =>
    "<ul>" + (arr || []).map((x) => "<li>" + esc(x) + "</li>").join("") + "</ul>";
  grid.innerHTML = [
    field("Mission name", '<span class="val">' + esc(mission.mission_name) + "</span>"),
    field("Target project label", '<span class="val">' + esc(mission.target_project_label) + "</span>"),
    field("Allowed scope", list(mission.allowed_scope)),
    field("Disallowed scope", list(mission.disallowed_scope)),
    field("Test command", '<span class="val mono">' + esc(mission.test_command) + "</span>"),
    field("Risk notes", '<span class="val">' + esc(mission.risk_notes) + "</span>"),
  ].join("");
}

function field(title, inner) {
  return '<div class="mission-field"><h3>' + esc(title) + "</h3>" + inner + "</div>";
}

// --------------------------------------------------------------------------- //
// Clearance workflow canvas (node graph)
// --------------------------------------------------------------------------- //

// The fixed clearance path every request travels, drawn as a vertical node
// graph. The first three stages are what agents would do before submitting;
// they are shown dashed as simulated context. Later stages light up from the
// live queue state. This is a visual aid, not a workflow editor: nodes are
// fixed, nothing is draggable, and nothing is imported.
const CANVAS_W = 700;
const CANVAS_H = 840;

const GRAPH_NODES = [
  { key: "start", label: "Mission Start", icon: "▶", cls: "gicon-start", x: 350, y: 50, sim: true },
  { key: "planner", label: "Planner Review", icon: "◇", cls: "gicon-agent", x: 350, y: 140, sim: true },
  { key: "scope", label: "Scope/Risk Check", icon: "?", cls: "gicon-cond", x: 350, y: 230, sim: true },
  { key: "incoming", label: "Incoming Clearance Request", icon: "◇", cls: "gicon-agent", x: 350, y: 325 },
  { key: "decision", label: "Operator Decision", icon: "⌘", cls: "gicon-op", x: 350, y: 420,
    sub: "human-commanded" },
  { key: "dta", label: "DTA", icon: "✕", cls: "gicon-deny", x: 145, y: 520, sub: "denied · clearance_done" },
  { key: "cta", label: "CTA", icon: "✓", cls: "gicon-start", x: 350, y: 520, sub: "bounded lease" },
  { key: "rfi", label: "RFI", icon: "?", cls: "gicon-cond", x: 545, y: 520, sub: "needs information" },
  { key: "claimed", label: "Claimed Work", icon: "◇", cls: "gicon-agent", x: 350, y: 620, sub: "IN_PROGRESS" },
  { key: "verify", label: "Verification", icon: "?", cls: "gicon-cond", x: 350, y: 705 },
  { key: "done", label: "DONE", icon: "■", cls: "gicon-end", x: 350, y: 790, sub: "clearance_done" },
];

const GRAPH_EDGES = [
  ["start", "planner"], ["planner", "scope"], ["scope", "incoming"],
  ["incoming", "decision"],
  ["decision", "dta"], ["decision", "cta"], ["decision", "rfi"],
  ["cta", "claimed"], ["claimed", "verify"], ["verify", "done"],
  // RFI returns to the incoming request for clarification (pre-decision only).
  ["rfi", "incoming", "loop"],
];

function flowActivity(state) {
  const lanes = state.lanes || {};
  const outbox = lanes.clearance_outbox || [];
  const inprog = lanes.clearance_in_progress || [];
  const done = lanes.clearance_done || [];
  const hasDecidable = outbox.some((c) => (c.allowed_actions || []).includes("cta"));
  return {
    start: true, planner: true, scope: true,
    incoming: hasDecidable,
    decision: hasDecidable,
    cta: outbox.some((c) => c.status === "CTA"),
    dta: done.some((c) => c.status === "DTA"),
    rfi: outbox.some((c) => c.status === "RFI_PENDING"),
    claimed: inprog.length > 0,
    verify: inprog.length > 0,
    done: done.some((c) => c.status === "DONE"),
  };
}

// Which stages should pulse, driven only by real queue and message state. A
// stage pulses when it is the live focus of work; there is no fake activity.
function flowPulse(state) {
  const lanes = state.lanes || {};
  const outbox = lanes.clearance_outbox || [];
  const inprog = lanes.clearance_in_progress || [];
  const done = lanes.clearance_done || [];
  const inProgIds = new Set(inprog.map((c) => c.packet_id));
  const hasProgressMsg = (lastMessages || []).some(
    (m) => !m.simulated && m.packet_id && inProgIds.has(m.packet_id));
  return {
    incoming: outbox.some((c) => ["RTA", "IN_REVIEW", "RFI_PENDING", "CTA"].includes(c.status)),
    decision: outbox.some((c) => ["RTA", "IN_REVIEW"].includes(c.status)),
    claimed: inprog.length > 0,
    verify: inprog.length > 0 && hasProgressMsg,
    done: done.some((c) => c.status === "DONE"),
  };
}

function edgePath(from, to, kind) {
  const x1 = from.x, y1 = from.y + 24, x2 = to.x, y2 = to.y - 24;
  if (kind === "loop") {
    // Curve out to the right and back up to the target's right side.
    const outX = Math.max(x1, x2) + 118;
    return "M " + (from.x + 52) + " " + (from.y - 12) +
      " C " + outX + " " + (from.y - 55) + ", " + outX + " " + (to.y + 55) +
      ", " + (to.x + 128) + " " + to.y;
  }
  const bend = Math.min(46, Math.abs(y2 - y1) / 2 + 14);
  return "M " + x1 + " " + y1 +
    " C " + x1 + " " + (y1 + bend) + ", " + x2 + " " + (y2 - bend) + ", " + x2 + " " + y2;
}

let lastFlowSig = "";

function renderWorkflow(state) {
  const active = flowActivity(state);
  const pulse = flowPulse(state);
  // Skip the rebuild when nothing changed, so the pulse animation is not
  // restarted on every fast poll.
  const sig = JSON.stringify(active) + "|" + JSON.stringify(pulse);
  if (sig === lastFlowSig) return;
  lastFlowSig = sig;
  const canvas = document.getElementById("gcanvas");
  const svg = document.getElementById("gedges");
  const byKey = {};
  GRAPH_NODES.forEach((n) => { byKey[n.key] = n; });

  let paths = "";
  GRAPH_EDGES.forEach(([a, b, kind]) => {
    const on = active[a] && active[b];
    paths += '<path class="gedge' + (on ? " edge-active" : "") + '" d="' +
      edgePath(byKey[a], byKey[b], kind) + '" />';
  });
  svg.innerHTML = paths;

  canvas.querySelectorAll(".gnode").forEach((el) => el.remove());
  GRAPH_NODES.forEach((n) => {
    const el = document.createElement("div");
    el.className = "gnode" + (n.sim ? " gnode-sim" : "") +
      (active[n.key] ? " gnode-active" : "") +
      (pulse[n.key] ? " gnode-pulse" : "");
    el.style.left = n.x + "px";
    el.style.top = n.y + "px";
    let inner = '<span class="gicon ' + n.cls + '">' + n.icon + '</span>' +
      '<span class="glabel">' + esc(n.label);
    if (n.sub) inner += '<span class="gnode-sub">' + esc(n.sub) + "</span>";
    inner += "</span>";
    el.innerHTML = inner;
    if (n.sim) el.title = "Simulated agent stage (before packet intake)";
    canvas.appendChild(el);
  });
}

// Canvas zoom (view-only; the graph itself is fixed). At fit scale the wrap
// has no scrollbars; panning only becomes available when zoomed past fit, so
// the page never has competing scroll regions by default.
let canvasScale = 1.0;
let fittedScale = 1.0;

function applyCanvasScale() {
  const wrap = document.getElementById("canvas-wrap");
  const canvas = document.getElementById("gcanvas");
  const tx = Math.max(0, (wrap.clientWidth - CANVAS_W * canvasScale) / 2);
  canvas.style.transform = "translate(" + tx + "px, 8px) scale(" + canvasScale + ")";
  wrap.style.overflow = canvasScale > fittedScale + 0.001 ? "auto" : "hidden";
}

function zoomCanvas(delta) {
  canvasScale = Math.min(1.4, Math.max(0.45, canvasScale + delta));
  applyCanvasScale();
}

function fitCanvas() {
  const wrap = document.getElementById("canvas-wrap");
  fittedScale = Math.min(1.0, (wrap.clientHeight - 16) / CANVAS_H,
    (wrap.clientWidth - 16) / CANVAS_W);
  canvasScale = fittedScale;
  applyCanvasScale();
}

// --------------------------------------------------------------------------- //
// Incoming clearance request (operator card)
// --------------------------------------------------------------------------- //

function renderOperatorCard(state) {
  const holder = document.getElementById("operator-card");
  const outbox = (state.lanes && state.lanes.clearance_outbox) || [];
  const card = outbox.find((c) => (c.allowed_actions || []).includes("cta"));
  const waiting = outbox.filter((c) => (c.allowed_actions || []).includes("cta")).length;

  if (!card) {
    if (currentMode === "operator") {
      holder.innerHTML =
        '<p class="muted">' + esc(OPERATOR_EMPTY_REQUESTS) +
        " Clearance packets arrive from agents, tools, scripts, or integrations " +
        "through the request tool or POST /api/request.</p>";
    } else {
      holder.innerHTML =
        '<p class="muted">No incoming requests. Packets arrive here from agents, ' +
        "tools, scripts, or integrations. In this demo, ask the agents a question " +
        "below and send the condensed recommendation to the clearance queue.</p>";
    }
    return;
  }

  const mission = state.mission || {};
  const disallowed = (mission.disallowed_scope || [])
    .map((x) => "<li>" + esc(x) + "</li>").join("");
  const why = [];
  if (card.clearance_class) why.push("clearance class " + esc(card.clearance_class));
  if (card.authority) why.push("authority required: " + esc(card.authority));
  why.push("all agent actions require operator clearance before work begins");

  let html = "";
  html += '<div class="incoming">';
  html += '<div class="incoming-head"><span class="pid">' + esc(card.packet_id) +
    '</span><span class="badge status-' + esc(card.status) + '">' + esc(card.status) + "</span></div>";
  html += '<div class="incoming-row"><span class="k">Agent wants to:</span> ' +
    esc(card.requested_action || card.action) + "</div>";
  html += '<div class="incoming-row"><span class="k">Requested by:</span> ' + esc(card.role) + "</div>";
  html += '<div class="incoming-row"><span class="k">Why clearance is required:</span> ' + why.join("; ") + "</div>";
  if (card.allowed_scope) {
    html += '<div class="incoming-row"><span class="k">Allowed scope:</span> ' + esc(card.allowed_scope) + "</div>";
  }
  if (disallowed) {
    html += '<div class="incoming-row"><span class="k">Disallowed scope (mission):</span><ul>' + disallowed + "</ul></div>";
  }
  if (card.risk_notes) {
    html += '<div class="incoming-row incoming-risk"><span class="k">Risk:</span> ' + esc(card.risk_notes) + "</div>";
  }
  html += '<div class="incoming-row"><span class="k">Requested decision:</span> clear (CTA), deny (DTA), or request information (RFI)</div>';
  html += "</div>";
  holder.innerHTML = html;

  const buttons = document.createElement("div");
  buttons.className = "card-buttons";
  ["cta", "dta", "rfi"].forEach((action) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "btn btn-lg " + (ACTION_CLASS[action] || "");
    b.textContent = ACTION_LABELS[action] || action;
    b.addEventListener("click", () => onAction(action, card));
    buttons.appendChild(b);
  });
  const audit = document.createElement("button");
  audit.type = "button";
  audit.className = "audit-link";
  audit.textContent = "View audit trail";
  audit.addEventListener("click", () => openAudit(card.filename));
  buttons.appendChild(audit);
  holder.appendChild(buttons);

  if (waiting > 1) {
    const more = document.createElement("p");
    more.className = "muted incoming-more";
    more.textContent = (waiting - 1) + " more request(s) waiting in the queue below.";
    holder.appendChild(more);
  }
}

// --------------------------------------------------------------------------- //
// Live agent feed (simulated; no real streaming or agent integration)
// --------------------------------------------------------------------------- //

// Real local agent events: read from the server (durable), refreshed on a
// poll. These are actual events sent by agents/tools/scripts through the local
// adapter (CLI/curl/HTTP), distinct from the simulated demo lines below.
function renderRealEvents(events) {
  const el = document.getElementById("feed-real");
  if (!el) return;
  if (!events || !events.length) {
    const empty = currentMode === "operator"
      ? OPERATOR_EMPTY_EVENTS
      : "No local events yet. Send one with tools/clearwright_agent_event.py " +
        "or POST /api/agent-events.";
    el.innerHTML = '<p class="muted feed-empty">' + esc(empty) + "</p>";
    return;
  }
  el.innerHTML = "";
  events.slice(-40).forEach((ev) => {
    const line = document.createElement("div");
    line.className = "feed-line";
    const badge = ev.simulated
      ? '<span class="feed-badge sim">simulated</span>'
      : '<span class="feed-badge local">local</span>';
    const pkt = ev.packet_id
      ? ' <span class="feed-pkt">[' + esc(ev.packet_id) + "]</span>" : "";
    const role = ev.role ? "/" + esc(ev.role) : "";
    line.innerHTML = badge + ' <span class="feed-actor">' + esc(ev.actor) +
      role + ":</span> " + esc(ev.message) + pkt;
    el.appendChild(line);
  });
  el.scrollTop = el.scrollHeight;
}

let lastEvents = [];

async function refreshAgentEvents() {
  try {
    const data = await getJSON("/api/agent-events");
    lastEvents = data.events || [];
    renderRealEvents(lastEvents);
  } catch (e) {
    // Leave the prior content in place on a transient fetch error.
  }
}

// --------------------------------------------------------------------------- //
// Local communications (real, durable, packet-linked)
//
// Threads of real messages posted by agents, tools, or scripts through the
// local adapter (CLI/curl/HTTP at /api/messages). These are actual local
// communication, not simulated conversation. Grouped by thread, oldest first.
// --------------------------------------------------------------------------- //

const COMMS_EMPTY =
  "No local messages yet. Send one with tools/clearwright_message.py or POST /api/messages.";

function renderMessages(messages) {
  const el = document.getElementById("comms");
  if (!el) return;
  if (!messages || !messages.length) {
    el.innerHTML = '<p class="muted comms-empty">' + esc(COMMS_EMPTY) + "</p>";
    return;
  }
  // Group by thread, preserving arrival order of threads and messages.
  const threads = {};
  const order = [];
  messages.forEach((m) => {
    const t = m.thread_id || "thr-unknown";
    if (!threads[t]) { threads[t] = []; order.push(t); }
    threads[t].push(m);
  });
  el.innerHTML = "";
  order.forEach((tid) => {
    const msgs = threads[tid];
    const wrap = document.createElement("div");
    wrap.className = "comm-thread";
    const pkt = msgs.find((m) => m.packet_id);
    let head = '<div class="comm-thread-head"><span class="comm-tid">' + esc(tid) + "</span>";
    if (pkt) head += ' <span class="comm-pkt">[' + esc(pkt.packet_id) + "]</span>";
    head += "</div>";
    let body = "";
    msgs.forEach((m) => {
      const dir = m.direction || "inbound";
      const badge = m.simulated
        ? '<span class="feed-badge sim">simulated</span>'
        : '<span class="feed-badge local">' + esc(dir) + "</span>";
      const meta = [m.status, m.source, m.at].filter(Boolean).map(esc).join(" · ");
      body += '<div class="comm-msg comm-' + esc(dir) + '">' + badge +
        ' <span class="comm-actor">' + esc(m.actor) +
        (m.role ? "/" + esc(m.role) : "") + ":</span> " + esc(m.message) +
        (meta ? ' <span class="comm-meta">' + meta + "</span>" : "") + "</div>";
    });
    wrap.innerHTML = head + body;
    el.appendChild(wrap);
  });
  el.scrollTop = el.scrollHeight;
}

let lastMessages = [];

async function refreshMessages() {
  try {
    const data = await getJSON("/api/messages");
    lastMessages = data.messages || [];
    renderMessages(lastMessages);
  } catch (e) {
    // Leave the prior content in place on a transient fetch error.
  }
}

// Operator chat: the operator types a request in the console and it becomes a
// real inbound message (OPERATOR-0001, role operator, source operator-ui). No
// fake agent reply is ever generated; real workers pick it up as a work item
// and respond through the work-item loop.
async function submitOperatorChat(ev) {
  ev.preventDefault();
  const input = document.getElementById("operator-chat-input");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  await postJSON("/api/messages", {
    actor: "OPERATOR-0001", role: "operator", source: "operator-ui",
    direction: "inbound", simulated: false, message: text,
  });
  refreshMessages();
  refreshWorkItems();
}

// --------------------------------------------------------------------------- //
// Work items (derived live from packets + messages; claim/respond via CLI/API)
// --------------------------------------------------------------------------- //

const WORK_KIND_LABEL = {
  message: "request", packet: "CTA packet", in_progress: "in progress", rfi: "RFI",
};

function renderWorkItems(items) {
  const el = document.getElementById("work-items");
  if (!el) return;
  if (!items || !items.length) {
    el.innerHTML = '<p class="muted work-empty">No open work items.</p>';
    return;
  }
  el.innerHTML = "";
  items.forEach((it) => {
    const row = document.createElement("div");
    row.className = "work-item work-" + esc(it.kind);
    let html = '<div class="work-top"><span class="work-kind">' +
      esc(WORK_KIND_LABEL[it.kind] || it.kind) + '</span><span class="work-badge">' +
      esc(it.status || "open") + "</span></div>";
    html += '<div class="work-title">' + esc(it.title || it.summary || "") + "</div>";
    const meta = [];
    if (it.packet_id) meta.push("packet " + esc(it.packet_id));
    if (it.thread_id) meta.push("thread " + esc(it.thread_id));
    if (it.actor) meta.push("from " + esc(it.actor) + (it.source ? " (" + esc(it.source) + ")" : ""));
    if (it.claimed_by) meta.push("claimed by " + esc(it.claimed_by));
    if (meta.length) html += '<div class="work-meta">' + meta.join(" · ") + "</div>";
    html += '<div class="work-next">next: ' + esc(it.next_action || "") + "</div>";
    html += '<div class="work-id mono">' + esc(it.work_item_id) + "</div>";
    row.innerHTML = html;
    el.appendChild(row);
  });
}

let lastWorkItems = [];

async function refreshWorkItems() {
  try {
    const data = await getJSON("/api/work-items");
    lastWorkItems = data.work_items || [];
    renderWorkItems(lastWorkItems);
  } catch (e) {
    // Leave the prior content in place on a transient fetch error.
  }
}

// --------------------------------------------------------------------------- //
// History (read-only view of the durable record)
// --------------------------------------------------------------------------- //

function historyQuery() {
  const params = [];
  const add = (key, id) => {
    const v = document.getElementById(id).value.trim();
    if (v) params.push(key + "=" + encodeURIComponent(v));
  };
  add("packet_id", "hf-packet");
  add("thread_id", "hf-thread");
  add("actor", "hf-actor");
  const lane = document.getElementById("hf-lane").value;
  if (lane) params.push("lane=" + encodeURIComponent(lane));
  add("status", "hf-status");
  return params.length ? "?" + params.join("&") : "";
}

async function loadHistory() {
  let data;
  try {
    data = await getJSON("/api/history" + historyQuery());
  } catch (e) {
    return;
  }
  const packets = data.packets || [], messages = data.messages || [], events = data.events || [];
  document.getElementById("hc-packets").textContent = packets.length;
  document.getElementById("hc-messages").textContent = messages.length;
  document.getElementById("hc-events").textContent = events.length;
  const none = '<p class="muted">None.</p>';
  document.getElementById("history-packets").innerHTML = packets.map((p) =>
    '<div class="hrow"><span class="mono">' + esc(p.packet_id || p.filename) +
    '</span> <span class="badge status-' + esc(p.status) + '">' + esc(p.status) +
    '</span><div class="hrow-sub">' + esc(p.lane) +
    (p.action ? " · " + esc(p.action) : "") + "</div></div>").join("") || none;
  document.getElementById("history-messages").innerHTML = messages.map((m) =>
    '<div class="hrow"><span class="work-badge">' + esc(m.direction || "") +
    '</span> <span class="comm-actor">' + esc(m.actor) + "</span>: " + esc(m.message) +
    '<div class="hrow-sub mono">' + esc(m.thread_id || "") +
    (m.packet_id ? " · " + esc(m.packet_id) : "") + "</div></div>").join("") || none;
  document.getElementById("history-events").innerHTML = events.map((e) =>
    '<div class="hrow"><span class="comm-actor">' + esc(e.actor) + "</span>: " + esc(e.message) +
    '<div class="hrow-sub mono">' + esc(e.at || "") +
    (e.packet_id ? " · " + esc(e.packet_id) : "") + "</div></div>").join("") || none;
}

function openHistory() {
  document.body.classList.add("history-open");
  document.getElementById("history-view").hidden = false;
  loadHistory();
}

function closeHistory() {
  document.body.classList.remove("history-open");
  document.getElementById("history-view").hidden = true;
}

let feedStarted = false;

function feedPush(actor, text) {
  const feed = document.getElementById("feed");
  const line = document.createElement("div");
  line.className = "feed-line feed-" + actor;
  line.innerHTML = '<span class="feed-actor">' + esc(actor) + ":</span> " + esc(text);
  feed.appendChild(line);
  while (feed.children.length > 40) feed.removeChild(feed.firstChild);
  feed.scrollTop = feed.scrollHeight;
}

function feedStart(state) {
  if (feedStarted) return;
  feedStarted = true;
  const script = [
    ["planner", "reviewing sample project scope"],
    ["reviewer", "checking risk and allowed scope"],
    ["worker", "requesting clearance"],
  ];
  script.forEach((entry, i) => {
    setTimeout(() => feedPush(entry[0], entry[1]), 350 * (i + 1));
  });
  const outbox = (state.lanes && state.lanes.clearance_outbox) || [];
  if (outbox.some((c) => (c.allowed_actions || []).includes("cta"))) {
    setTimeout(() => feedPush("clearwright", "operator decision required"), 350 * (script.length + 1));
  }
}

const FEED_ACTION_LINES = {
  cta: (f) => ["clearwright", "CTA granted for " + f + "; bounded lease recorded"],
  dta: (f) => ["clearwright", "DTA recorded for " + f + " (governance outcome, archived to clearance_done)"],
  rfi: (f) => ["clearwright", "RFI recorded for " + f + "; awaiting requester clarification"],
  claim: (f) => ["worker", "claimed " + f + "; work is now IN_PROGRESS"],
  complete: (f) => ["worker", "completed " + f + "; results recorded on the DONE audit event"],
  fail: (f) => ["clearwright", f + " marked FAILED (execution failure after claim)"],
};

// --------------------------------------------------------------------------- //
// Board
// --------------------------------------------------------------------------- //

function renderBoard(state) {
  const board = document.getElementById("board");
  board.innerHTML = "";
  document.getElementById("actor-label").textContent = state.actor || "operator";
  Object.keys(LANE_TITLES).forEach((lane) => {
    const cards = state.lanes[lane] || [];
    const laneEl = document.createElement("div");
    laneEl.className = "lane";
    laneEl.innerHTML =
      '<div class="lane-head"><h3>' + esc(LANE_TITLES[lane]) +
      '</h3><span class="lane-count">' + cards.length + "</span></div>";
    if (!cards.length) {
      const empty = document.createElement("div");
      empty.className = "lane-empty";
      empty.textContent = "empty";
      laneEl.appendChild(empty);
    } else {
      cards.forEach((c) => laneEl.appendChild(renderCard(c)));
    }
    board.appendChild(laneEl);
  });
}

function renderCard(c) {
  const el = document.createElement("div");
  el.className = "card status-" + esc(c.status);

  let html = "";
  html += '<div class="card-top"><span class="pid">' + esc(c.packet_id || c.filename) +
    '</span><span class="badge status-' + esc(c.status) + '">' + esc(c.status) + "</span></div>";
  html += '<div class="card-action">' + esc(c.action || c.requested_action || "") + "</div>";
  html += '<div class="card-meta">';
  html += '<div><span class="k">role:</span> ' + esc(c.role || "") + "</div>";
  html += '<div><span class="k">authority:</span> ' + esc(c.authority || "n/a") + "</div>";
  html += '<div><span class="k">audit events:</span> ' + esc(c.audit_event_count) + "</div>";
  html += "</div>";
  if (c.risk_notes) html += '<div class="card-risk">risk: ' + esc(c.risk_notes) + "</div>";
  if (c.clearance_expires_at) {
    html += '<div class="card-lease">CTA lease until ' + esc(c.clearance_expires_at) + "</div>";
  }
  if (c.status === "RFI_PENDING") {
    html += '<div class="card-note">Awaiting requester clarification. Pre-decision only; stays in clearance_outbox.</div>';
  }
  el.innerHTML = html;

  const buttons = document.createElement("div");
  buttons.className = "card-buttons";
  (c.allowed_actions || []).forEach((action) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "btn " + (ACTION_CLASS[action] || "");
    b.textContent = ACTION_LABELS[action] || action;
    b.addEventListener("click", () => onAction(action, c));
    buttons.appendChild(b);
  });
  const audit = document.createElement("button");
  audit.type = "button";
  audit.className = "audit-link";
  audit.textContent = "View audit trail";
  audit.addEventListener("click", () => openAudit(c.filename));
  buttons.appendChild(audit);

  el.appendChild(buttons);
  return el;
}

// --------------------------------------------------------------------------- //
// Actions
// --------------------------------------------------------------------------- //

async function onAction(action, card) {
  if (action === "complete") {
    openResults(card);
    return;
  }
  if (REASON_ACTIONS.has(action)) {
    const reason = await askReason(action, card);
    if (reason === null) return;
    await runAction(action, card.filename, reason);
  } else {
    await runAction(action, card.filename, "");
  }
}

async function runAction(action, filename, reason, results) {
  const result = await postJSON("/api/action", { action, filename, reason, results });
  if (result.state) renderState(result.state);
  if (result.ok) {
    setActivity((ACTION_LABELS[action] || action) + " on " + filename + "\n" + (result.output || "").trim());
    const line = FEED_ACTION_LINES[action];
    if (line) feedPush.apply(null, line(filename));
  } else {
    setActivity("Refused: " + (result.error || "") + "\n" + (result.output || "").trim());
  }
  return result;
}

async function resetDemo() {
  const result = await postJSON("/api/reset", {});
  if (result.state) renderState(result.state);
  setActivity("Demo queue reset to the seed clearance packets.");
  closeAudit();
}

// --------------------------------------------------------------------------- //
// Background packet creation
//
// Packets normally arrive from agents, tools, scripts, or integrations via
// the request tool and POST /api/request. Nobody fills out packet paperwork
// in this console: the demo derives a packet from the condensed conversation
// recommendation and sends it to the clearance queue directly.
// --------------------------------------------------------------------------- //

async function sendRecommendationToQueue(summary) {
  const body = {
    title: (summary.proposed_rta_title || "").slice(0, 140),
    packet_type: "docs_change",
    requesting_agent: "agent/worker",
    requested_action: summary.proposed_rta_action || "",
    target_label: "sample software project",
    allowed_scope: summary.scope_boundary || "",
    risk_notes: "Risk level " + (summary.risk_level || "unknown") +
      " (from simulated deliberation).",
  };
  const result = await postJSON("/api/request", body);
  if (result.state) renderState(result.state);
  if (result.ok) {
    setActivity("Clearance request created from the recommendation\n" +
      (result.output || "").trim());
    feedPush("clearwright",
      "clearance request created from the recommendation; operator decision required");
    // Bridge: record a real, durable local agent event for the recommendation
    // (no model call; this is the condenser's own note).
    postJSON("/api/agent-events", {
      actor: "clearwright", role: "condenser", source: "control-plane",
      simulated: false,
      message: "Recommendation " + (summary.recommended || "") +
        " sent to clearance queue: " + (summary.proposed_rta_title || ""),
    }).then(refreshAgentEvents);
  } else {
    setActivity("Refused: " + (result.error || "") + "\n" + (result.output || "").trim());
  }
  return result;
}

// --------------------------------------------------------------------------- //
// Results modal (complete with results)
// --------------------------------------------------------------------------- //

let resultsCard = null;

function openResults(card) {
  resultsCard = card;
  document.getElementById("results-form").reset();
  document.getElementById("results-context").textContent =
    (card.packet_id || card.filename) + " : " + (card.action || "");
  document.getElementById("results-modal").setAttribute("aria-hidden", "false");
  document.getElementById("res-summary").focus();
}

function closeResults() {
  document.getElementById("results-modal").setAttribute("aria-hidden", "true");
  resultsCard = null;
}

async function submitResults(ev) {
  ev.preventDefault();
  if (!resultsCard) return;
  const summary = document.getElementById("res-summary").value.trim();
  if (!summary) return;
  const files = document.getElementById("res-files").value
    .split("\n").map((s) => s.trim()).filter(Boolean);
  const results = { summary };
  const verification = document.getElementById("res-verification").value.trim();
  const findings = document.getElementById("res-findings").value.trim();
  if (verification) results.verification = verification;
  if (files.length) results.changed_files = files;
  if (findings) results.findings = findings;
  const card = resultsCard;
  closeResults();
  await runAction("complete", card.filename, "", results);
}

// --------------------------------------------------------------------------- //
// Agent conversation console (SIMULATED — no real external model integration)
// --------------------------------------------------------------------------- //

const CONVO_ROLE_LABELS = {
  claude: "claude", gpt: "gpt", codex: "codex", clearwright: "clearwright",
};

function convoBubble(turn) {
  const el = document.createElement("div");
  el.className = "convo-turn convo-" + esc(turn.role);
  let html = '<span class="convo-role">' +
    esc(CONVO_ROLE_LABELS[turn.role] || turn.role) + '</span> ' + esc(turn.text);
  if (turn.code_impact) {
    html += '<pre class="convo-code">' + esc(turn.code_impact) + "</pre>";
  }
  el.innerHTML = html;
  return el;
}

function renderConvoSummary(summary) {
  const holder = document.getElementById("convo-summary");
  let html = '<div class="convo-card convo-card-' + esc(summary.recommended) + '">';
  html += '<div class="convo-card-head">ClearWright condensed decision ' +
    '<span class="badge status-' +
    esc(summary.recommended === "RFI" ? "RFI_PENDING" : summary.recommended) + '">' +
    esc(summary.recommended) + "</span></div>";
  html += '<div class="incoming-row"><span class="k">Decision needed:</span> ' +
    esc(summary.decision_needed) + "</div>";
  html += '<div class="incoming-row"><span class="k">Risk level:</span> ' +
    esc(summary.risk_level) + "</div>";
  html += '<div class="incoming-row"><span class="k">Risks:</span><ul>' +
    (summary.risks || []).map((r) => "<li>" + esc(r) + "</li>").join("") + "</ul></div>";
  html += '<div class="incoming-row"><span class="k">Scope boundary:</span> ' +
    esc(summary.scope_boundary) + "</div>";
  html += '<div class="incoming-row"><span class="k">Proposed next action:</span> ' +
    esc(summary.proposed_next_action) + "</div>";
  html += "</div>";
  holder.innerHTML = html;

  if (summary.proposed_rta_title && summary.proposed_rta_action) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn-primary convo-draft-btn";
    btn.textContent = "Send to clearance queue";
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      const result = await sendRecommendationToQueue(summary);
      if (result.ok) btn.textContent = "Sent — awaiting operator decision";
      else btn.disabled = false;
    });
    holder.firstChild.appendChild(btn);
  }
}

async function submitConvo(ev) {
  ev.preventDefault();
  const input = document.getElementById("convo-input");
  const question = input.value.trim();
  if (!question) return;
  const transcript = document.getElementById("convo-transcript");
  const summaryHolder = document.getElementById("convo-summary");
  transcript.innerHTML = "";
  summaryHolder.innerHTML = "";

  const operator = document.createElement("div");
  operator.className = "convo-turn convo-operator";
  operator.innerHTML = '<span class="convo-role">operator</span> ' + esc(question);
  transcript.appendChild(operator);

  const result = await postJSON("/api/converse", { question });
  if (!result.ok) {
    setActivity("Refused: " + (result.error || ""));
    return;
  }
  // Stagger the simulated turns so the deliberation reads as a conversation.
  (result.turns || []).forEach((turn, i) => {
    setTimeout(() => {
      transcript.appendChild(convoBubble(turn));
      transcript.scrollTop = transcript.scrollHeight;
    }, 380 * (i + 1));
  });
  setTimeout(() => {
    renderConvoSummary(result.summary);
    feedPush("clearwright",
      "deliberation condensed (max " + result.max_rounds + " rounds); " +
      "recommendation: " + result.summary.recommended);
  }, 380 * ((result.turns || []).length + 1));
  input.value = "";
}

// --------------------------------------------------------------------------- //
// Reason modal
// --------------------------------------------------------------------------- //

let reasonResolver = null;

function askReason(action, card) {
  return new Promise((resolve) => {
    reasonResolver = resolve;
    document.getElementById("reason-title").textContent =
      (ACTION_LABELS[action] || action) + " requires a reason";
    document.getElementById("reason-context").textContent =
      esc(card.packet_id) + " : " + (card.action || "");
    const input = document.getElementById("reason-input");
    input.value = "";
    document.getElementById("reason-modal").setAttribute("aria-hidden", "false");
    input.focus();
  });
}

function closeReason(value) {
  document.getElementById("reason-modal").setAttribute("aria-hidden", "true");
  if (reasonResolver) {
    reasonResolver(value);
    reasonResolver = null;
  }
}

// --------------------------------------------------------------------------- //
// Audit drawer
// --------------------------------------------------------------------------- //

async function openAudit(filename) {
  const data = await getJSON("/api/audit?filename=" + encodeURIComponent(filename));
  const body = document.getElementById("audit-body");
  if (!data.found) {
    body.innerHTML = '<p class="muted">Packet not found.</p>';
  } else {
    let html = '<p class="muted mono">' + esc(data.packet_id) + " &middot; " + esc(data.status) +
      " &middot; " + esc(data.lane) + "</p>";
    if (!data.events.length) {
      html += '<p class="muted">No audit events recorded.</p>';
    } else {
      data.events.forEach((ev) => {
        html += '<div class="event">';
        html += '<div class="ev-top"><span class="ev-name">' + esc(ev.event) +
          '</span><span class="ev-time">' + esc(ev.at) + "</span></div>";
        html += '<div class="ev-actor">actor: ' + esc(ev.actor) + "</div>";
        const note = ev.note || ev.reason || "";
        if (note) html += '<div class="ev-note">' + esc(note) + "</div>";
        if (ev.results && typeof ev.results === "object") {
          html += '<div class="ev-results">';
          if (ev.results.summary) {
            html += '<div><span class="k">summary:</span> ' + esc(ev.results.summary) + "</div>";
          }
          if (ev.results.verification) {
            html += '<div><span class="k">verification:</span> ' + esc(ev.results.verification) + "</div>";
          }
          if (Array.isArray(ev.results.changed_files) && ev.results.changed_files.length) {
            html += '<div><span class="k">changed files:</span><ul>' +
              ev.results.changed_files.map((f) => "<li>" + esc(f) + "</li>").join("") +
              "</ul></div>";
          }
          if (ev.results.findings) {
            html += '<div><span class="k">findings:</span> ' + esc(ev.results.findings) + "</div>";
          }
          html += "</div>";
        }
        html += "</div>";
      });
    }
    html += await relatedContextHtml(data.packet_id);
    body.innerHTML = html;
  }
  document.getElementById("audit-drawer").setAttribute("aria-hidden", "false");
}

// Working context tied to a packet: real (non-simulated) agent events and local
// messages. The packet stays the authority record; these are the surrounding
// conversation and activity, shown but clearly secondary.
async function relatedContextHtml(packetId) {
  if (!packetId) return "";
  let html = "";
  try {
    const ev = await getJSON("/api/agent-events?packet_id=" + encodeURIComponent(packetId));
    const evs = (ev.events || []).filter((e) => !e.simulated);
    if (evs.length) {
      html += '<h3 class="audit-sub">Related agent events</h3>';
      evs.forEach((e) => {
        html += '<div class="ev-actor">' + esc(e.actor) +
          (e.role ? "/" + esc(e.role) : "") + ": " + esc(e.message) + "</div>";
      });
    }
  } catch (e) { /* omit related events on a transient fetch error */ }
  try {
    const mg = await getJSON("/api/messages?packet_id=" + encodeURIComponent(packetId));
    const msgs = (mg.messages || []).filter((m) => !m.simulated);
    if (msgs.length) {
      html += '<h3 class="audit-sub">Related messages</h3>';
      msgs.forEach((m) => {
        html += '<div class="ev-actor"><span class="comm-tid">' + esc(m.thread_id) +
          "</span> " + esc(m.actor) + (m.role ? "/" + esc(m.role) : "") +
          " (" + esc(m.direction || "inbound") + "): " + esc(m.message) + "</div>";
      });
    }
  } catch (e) { /* omit related messages on a transient fetch error */ }
  return html;
}

function closeAudit() {
  document.getElementById("audit-drawer").setAttribute("aria-hidden", "true");
}

// --------------------------------------------------------------------------- //
// State plumbing
// --------------------------------------------------------------------------- //

// Switch the console between live operator mode and the demo walkthrough based
// on state.mode from /api/state. Operator mode hides the demo-only affordances
// (Reset demo, the demo mission panel, and the simulated feed as a primary
// live feed) and leans on real local agent events.
function applyMode(state) {
  currentMode = state.mode === "operator" ? "operator" : "demo";
  const operator = currentMode === "operator";
  document.body.classList.toggle("mode-operator", operator);
  document.body.classList.toggle("mode-demo", !operator);

  const badge = document.getElementById("mode-badge");
  if (badge) {
    badge.textContent = operator ? "Operator mode" : "Demo mode";
    badge.hidden = false;
    badge.classList.toggle("mode-badge-operator", operator);
    badge.classList.toggle("mode-badge-demo", !operator);
  }
  const posture = document.getElementById("posture");
  if (posture) {
    posture.textContent = operator
      ? "Live local operator console · durable queue · early alpha · human-commanded, operator-controlled"
      : "Demo walkthrough · local reference implementation · early alpha · human-commanded, operator-controlled";
  }
  // Reset demo is only meaningful in demo mode; operator runs a live durable queue.
  const reset = document.getElementById("reset-btn");
  if (reset) reset.hidden = operator;
  const mission = document.getElementById("mission-panel");
  if (mission) mission.hidden = operator;
  // No simulated feed as the primary live feed in operator mode.
  const simGroup = document.getElementById("feed-sim-group");
  if (simGroup) simGroup.hidden = operator;
  // The simulated agent conversation is a demo-only aid; operator mode shows
  // real local communications instead, never fake agent replies.
  const convo = document.getElementById("convo-panel");
  if (convo) convo.hidden = operator;
  // The operator chat is the real operator input; it belongs to operator mode.
  const chat = document.getElementById("operator-chat-form");
  if (chat) chat.hidden = !operator;

  // Re-render the real-events empty-state with the correct wording for the mode.
  renderRealEvents(lastEvents);
}

function renderState(state) {
  applyMode(state);
  renderMission(state.mission);
  renderWorkflow(state);
  renderOperatorCard(state);
  renderBoard(state);
  // The simulated demo feed is a walkthrough aid only, never the primary live
  // feed; in operator mode the real local agent events stand alone.
  if (currentMode !== "operator") feedStart(state);
}

async function refresh() {
  const state = await getJSON("/api/state");
  renderState(state);
}

function wire() {
  document.getElementById("reset-btn").addEventListener("click", resetDemo);
  document.getElementById("audit-close").addEventListener("click", closeAudit);
  document.getElementById("reason-cancel").addEventListener("click", () => closeReason(null));
  document.getElementById("reason-confirm").addEventListener("click", () => {
    const value = document.getElementById("reason-input").value.trim();
    if (!value) return;
    closeReason(value);
  });
  document.getElementById("results-cancel").addEventListener("click", closeResults);
  document.getElementById("results-form").addEventListener("submit", submitResults);
  document.getElementById("zoom-in").addEventListener("click", () => zoomCanvas(0.1));
  document.getElementById("zoom-out").addEventListener("click", () => zoomCanvas(-0.1));
  document.getElementById("zoom-fit").addEventListener("click", fitCanvas);
  document.getElementById("convo-form").addEventListener("submit", submitConvo);
  document.getElementById("operator-chat-form").addEventListener("submit", submitOperatorChat);
  document.getElementById("history-btn").addEventListener("click", openHistory);
  document.getElementById("history-close").addEventListener("click", closeHistory);
  document.getElementById("history-filters").addEventListener("submit", (e) => {
    e.preventDefault();
    loadHistory();
  });
  document.getElementById("hf-clear").addEventListener("click", () => {
    ["hf-packet", "hf-thread", "hf-actor", "hf-status"].forEach(
      (id) => { document.getElementById(id).value = ""; });
    document.getElementById("hf-lane").value = "";
    loadHistory();
  });
  fitCanvas();
  // Live console: fast polling (every 2s) of all real sources. No WebSockets.
  const LIVE_MS = 2000;
  refreshAgentEvents();
  refreshMessages();
  refreshWorkItems();
  setInterval(refresh, LIVE_MS);
  setInterval(refreshAgentEvents, LIVE_MS);
  setInterval(refreshMessages, LIVE_MS);
  setInterval(refreshWorkItems, LIVE_MS);
}

wire();
refresh();
