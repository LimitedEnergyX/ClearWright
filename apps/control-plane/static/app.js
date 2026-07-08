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

function renderWorkflow(state) {
  const active = flowActivity(state);
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
      (active[n.key] ? " gnode-active" : "");
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
    holder.innerHTML =
      '<p class="muted">No incoming requests. Agents, tools, or integrations would ' +
      "normally submit packets here. Use Inject demo request to simulate one.</p>";
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
// Intake modal (New RTA)
// --------------------------------------------------------------------------- //

let intakeOptionsLoaded = false;

function fillSelect(id, options, selected) {
  const el = document.getElementById(id);
  el.innerHTML = "";
  (options || []).forEach((opt) => {
    const o = document.createElement("option");
    o.value = opt;
    o.textContent = opt;
    // defaultSelected (not just selected) so form.reset() returns to this
    // option rather than the first one.
    if (opt === selected) o.defaultSelected = true;
    el.appendChild(o);
  });
}

function loadIntakeOptions(intake) {
  if (intakeOptionsLoaded || !intake) return;
  fillSelect("rta-type", intake.packet_types, "analysis");
  fillSelect("rta-label", intake.target_labels, intake.target_labels[0]);
  fillSelect("rta-authority", intake.authority_classes, "WORKER");
  fillSelect("rta-clearance", intake.clearance_classes, "READ_ONLY");
  fillSelect("rta-priority", intake.priority_classes, "NORMAL");
  intakeOptionsLoaded = true;
}

function openIntake() {
  document.getElementById("intake-form").reset();
  document.getElementById("rta-agent").value = "agent/worker";
  document.getElementById("intake-modal").setAttribute("aria-hidden", "false");
  document.getElementById("rta-title").focus();
}

function closeIntake() {
  document.getElementById("intake-modal").setAttribute("aria-hidden", "true");
}

async function submitIntake(ev) {
  ev.preventDefault();
  const body = {
    title: document.getElementById("rta-title").value.trim(),
    packet_type: document.getElementById("rta-type").value,
    requesting_agent: document.getElementById("rta-agent").value.trim(),
    requested_action: document.getElementById("rta-action").value.trim(),
    target_label: document.getElementById("rta-label").value,
    allowed_scope: document.getElementById("rta-scope").value.trim(),
    test_command: document.getElementById("rta-test").value.trim(),
    risk_notes: document.getElementById("rta-risk").value.trim(),
    authority_class: document.getElementById("rta-authority").value,
    clearance_class: document.getElementById("rta-clearance").value,
    priority_class: document.getElementById("rta-priority").value,
  };
  if (!body.title || !body.requesting_agent || !body.requested_action) return;
  const result = await postJSON("/api/request", body);
  if (result.state) renderState(result.state);
  if (result.ok) {
    closeIntake();
    setActivity("Simulated agent request submitted\n" + (result.output || "").trim());
    feedPush("worker", "submitted a clearance request (simulated)");
    feedPush("clearwright", "operator decision required");
  } else {
    setActivity("Refused: " + (result.error || "") + "\n" + (result.output || "").trim());
  }
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
    btn.textContent = "Draft RTA from this";
    btn.addEventListener("click", () => {
      openIntake();
      document.getElementById("rta-title").value =
        summary.proposed_rta_title.slice(0, 140);
      document.getElementById("rta-action").value = summary.proposed_rta_action;
      document.getElementById("rta-type").value = "docs_change";
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
    body.innerHTML = html;
  }
  document.getElementById("audit-drawer").setAttribute("aria-hidden", "false");
}

function closeAudit() {
  document.getElementById("audit-drawer").setAttribute("aria-hidden", "true");
}

// --------------------------------------------------------------------------- //
// State plumbing
// --------------------------------------------------------------------------- //

function renderState(state) {
  renderMission(state.mission);
  renderWorkflow(state);
  renderOperatorCard(state);
  renderBoard(state);
  loadIntakeOptions(state.intake);
  feedStart(state);
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
  document.getElementById("inject-btn").addEventListener("click", openIntake);
  document.getElementById("intake-cancel").addEventListener("click", closeIntake);
  document.getElementById("intake-form").addEventListener("submit", submitIntake);
  document.getElementById("results-cancel").addEventListener("click", closeResults);
  document.getElementById("results-form").addEventListener("submit", submitResults);
  document.getElementById("zoom-in").addEventListener("click", () => zoomCanvas(0.1));
  document.getElementById("zoom-out").addEventListener("click", () => zoomCanvas(-0.1));
  document.getElementById("zoom-fit").addEventListener("click", fitCanvas);
  document.getElementById("convo-form").addEventListener("submit", submitConvo);
  fitCanvas();
}

wire();
refresh();
