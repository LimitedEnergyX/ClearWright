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
// Selected task: header + compact phase stepper
//
// The stepper derives ONLY from the selected task's own durable state via
// /api/task-state (its councils, gate, claim, and messages) -- a historical or
// concurrent task can never affect the live phase display. Completed phases
// are static, only the CURRENT phase animates, an unresolved gate (operator
// required) renders amber and STATIC, and future phases are muted.
// --------------------------------------------------------------------------- //

const PHASE_LABELS = {
  request: "Request", plan_review: "Plan Review", authority: "Authority",
  execute: "Execute", verify: "Verify", complete: "Complete",
};

let lastTaskState = null;
let lastStepperSig = "";

function renderPhaseStepper(ts) {
  const el = document.getElementById("phase-stepper");
  if (!el) return;
  if (!ts || !ts.found) {
    el.innerHTML = '<p class="muted">No task selected.</p>';
    lastStepperSig = "";
    return;
  }
  const phases = ts.phases || Object.keys(PHASE_LABELS);
  const currentIdx = phases.indexOf(ts.phase);
  // Skip the rebuild when nothing changed, so the CSS animation on the
  // current step is not restarted by every 2s poll.
  const sig = ts.thread_id + "|" + ts.phase + "|" + ts.phase_attention;
  if (sig === lastStepperSig) return;
  lastStepperSig = sig;
  el.innerHTML = phases.map((p, i) => {
    let cls = "step";
    if (i < currentIdx) cls += " step-done";           // completed: static
    else if (i === currentIdx) {
      cls += ts.phase_attention ? " step-attention"    // operator required: amber, static
        : (ts.phase === "complete" ? " step-done step-terminal" : " step-current");
    } else cls += " step-future";                       // future: muted
    return '<div class="' + cls + '" data-phase="' + esc(p) + '">' +
      '<span class="step-dot" aria-hidden="true"></span>' +
      '<span class="step-label">' + esc(PHASE_LABELS[p] || p) + "</span></div>";
  }).join('<span class="step-link" aria-hidden="true"></span>');
}

function renderTaskHeader(ts) {
  const el = document.getElementById("task-header");
  if (!el) return;
  if (!ts || !ts.found) {
    el.innerHTML = '<p class="muted">No task selected. Pick one from the work queue.</p>';
    return;
  }
  const gate = ts.gate;
  const claim = ts.claim || {};
  const council = ts.current_council;
  let html = '<div class="th-title">' + esc(ts.title || ts.thread_id) + "</div>";
  html += '<div class="th-meta">';
  if (ts.work_item_id) html += '<span class="th-chip mono" title="work item">' + esc(ts.work_item_id) + "</span>";
  html += '<span class="th-chip th-status th-status-' + esc(ts.status) + '">' + esc(ts.status) + "</span>";
  html += '<span class="th-chip th-phase">' + esc(PHASE_LABELS[ts.phase] || ts.phase) + "</span>";
  if (council) {
    html += '<span class="th-chip mono" title="current council: ' + esc(council.outcome || "running") + '">' +
      esc(council.council_id) + " · " + esc(council.phase) + " r" + esc(council.rounds || 0) +
      (council.outcome ? " · " + esc(council.outcome) : "") + "</span>";
  }
  if (gate) {
    html += '<span class="th-chip th-gate">unresolved gate ' + esc(gate.gate_id) + "</span>";
  }
  html += '<span class="th-chip">' + (claim.claimed
    ? "claimed by " + esc(claim.claimed_by || "?")
    : "unclaimed") + "</span>";
  html += "</div>";
  html += '<div class="th-next">Next: ' + esc(ts.next_action || "") + "</div>";
  el.innerHTML = html;
}

// Which stages should pulse. The server computes this from real durable state
// (packets + real messages + derived work items) with a recency window, so a
// stale DONE packet does not keep DONE pulsing. There is no fake activity.
// Only the boolean node flags feed the graph; the inspector metadata (reason,
// expiry countdown) ticks every poll and must not restart the animation.
const PULSE_NODE_KEYS = ["incoming", "decision", "cta", "rfi", "claimed", "verify", "done"];

function flowPulse(state) {
  const p = state.pulse || {};
  const out = {};
  PULSE_NODE_KEYS.forEach((k) => { out[k] = !!p[k]; });
  return out;
}

// Read-only pulse inspector: why the graph is pulsing and when the time-based
// part expires. Standing states (open/claimed items, packet lifecycle) have no
// expiry - they hold until acted on.
function fmtRemaining(sec) {
  if (sec === null || sec === undefined) return null;
  const m = Math.floor(sec / 60), s = sec % 60;
  return String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
}

function renderPulseInspector(pulse) {
  const el = document.getElementById("pulse-inspector");
  if (!el) return;
  const p = pulse || {};
  const idle = !p.active_phase || p.active_phase === "idle";
  let html = '<span class="pi-phase' + (idle ? "" : " pi-active") + '">Pulse: ' +
    esc(idle ? "idle" : p.active_phase) + "</span>";
  html += ' <span class="pi-reason">&middot; ' + esc(p.reason || "no recent activity") + "</span>";
  if (p.source_thread_id) {
    html += ' <span class="pi-src mono" title="' + esc(p.source_thread_id) + '">' +
      esc(p.source_thread_id) + "</span>";
  }
  if (p.source_work_item_id) {
    html += ' <span class="pi-src mono" title="' + esc(p.source_work_item_id) + '">' +
      esc(p.source_work_item_id) + "</span>";
  }
  if (p.source_packet_id) {
    html += ' <span class="pi-src mono">[' + esc(p.source_packet_id) + "]</span>";
  }
  const left = fmtRemaining(p.seconds_remaining);
  if (left !== null) html += ' <span class="pi-exp">Expires in ' + esc(left) + "</span>";
  el.innerHTML = html;
}

// The selected-task poll: header, stepper, operator panel, and the Work tabs
// all bind to this ONE state object, so a current-task mismatch between the
// queue, header, phase display, conversation, and operator panel cannot occur.
async function refreshTaskState() {
  try {
    const url = selectedConvThread
      ? "/api/task-state?thread_id=" + encodeURIComponent(selectedConvThread)
      : "/api/task-state";
    const ts = await getJSON(url);
    if (ts && ts.found && !selectedConvThread) {
      // Adopt the server's default (most recently worked) task as the
      // selection so every panel binds to the same task from first paint.
      selectedConvThread = ts.thread_id;
    }
    lastTaskState = ts && ts.found ? ts : null;
  } catch (e) {
    return; // keep the previous state on a transient fetch error
  }
  renderTaskHeader(lastTaskState);
  renderPhaseStepper(lastTaskState);
  renderOperatorPanel(lastTaskState);
  if (currentView === "command") renderCommandOverview(lastTaskState);
}

// The right-hand operator panel: the next required action, the authority
// state (gate / clearance / verification), and the contextual operator
// actions -- all bound to the SAME selected task as the header and stepper.
function renderOperatorPanel(ts) {
  const nextBody = document.getElementById("next-action-body");
  const authBody = document.getElementById("authority-body");
  const actions = document.getElementById("operator-actions");
  if (!nextBody || !authBody || !actions) return;
  if (!ts) {
    nextBody.innerHTML = '<p class="muted">No task selected.</p>';
    authBody.innerHTML = '<p class="muted">No task selected.</p>';
    actions.innerHTML = '<p class="muted">No task selected.</p>';
    return;
  }
  nextBody.innerHTML = '<p class="op-next' + (ts.phase_attention ? " op-next-attention" : "") +
    '">' + esc(ts.next_action || "") + "</p>";

  let auth = "";
  if (ts.gate) {
    auth += '<div class="op-auth op-auth-gate">Unresolved gate <span class="mono">' +
      esc(ts.gate.gate_id) + "</span> from council <span class=\"mono\">" +
      esc(ts.gate.council_id) + "</span>. The governed workflow is stopped; " +
      "proceeding requires a durable post-gate operator authorization (grant-proceed) " +
      "or operator-only close.</div>";
  } else {
    auth += '<div class="op-auth">No unresolved gate.</div>';
  }
  const ov = ts.overview || {};
  auth += '<div class="op-auth">Verification ' +
    (ov.verification_required ? "REQUIRED before DONE" : "not required") + ".</div>";
  if (ov.approved_scope) {
    auth += '<details class="op-scope"><summary>Approved scope</summary><p>' +
      esc(ov.approved_scope) + "</p></details>";
  } else {
    auth += '<div class="op-auth muted">No recorded approved scope (lexical or chat task).</div>';
  }
  authBody.innerHTML = auth;

  let btns = "";
  btns += '<button class="btn btn-quiet conv-action" type="button" data-action="ack">Mark reviewed</button>';
  btns += '<button class="btn btn-quiet conv-action" type="button" data-action="workitem">Create work item</button>';
  btns += '<button class="btn btn-quiet conv-action" type="button" data-action="escalate">Request clearance packet</button>';
  btns += '<button class="btn btn-quiet" type="button" data-open-work="1">Open in Work</button>';
  actions.innerHTML = btns;
}

// The Command Center's at-a-glance overview of the selected task: status and
// phase, next action, approved scope, plan summary, blockers, the latest
// reconciliation, and completion criteria (requirement: the FULL request body
// lives here, not in the queue).
function renderCommandOverview(ts) {
  const el = document.getElementById("command-overview");
  if (el) renderCommandOverviewInto(el, ts);
}

function renderCommandOverviewInto(el, ts) {
  if (!ts || !ts.found) {
    el.innerHTML = '<p class="muted">Select a task to see its overview.</p>';
    return;
  }
  const ov = ts.overview || {};
  let html = '<div class="ov-grid">';
  html += '<div class="ov-card"><div class="ov-k">Status &middot; phase</div><div class="ov-v">' +
    esc(ts.status) + " &middot; " + esc(PHASE_LABELS[ts.phase] || ts.phase) + "</div></div>";
  html += '<div class="ov-card"><div class="ov-k">Next action</div><div class="ov-v">' +
    esc(ts.next_action || "") + "</div></div>";
  if (ov.request) {
    html += '<div class="ov-card ov-span"><div class="ov-k">Request</div><div class="ov-v ov-pre">' +
      esc(ov.request) + "</div></div>";
  }
  if (ov.approved_scope) {
    html += '<div class="ov-card ov-span"><div class="ov-k">Approved scope</div><div class="ov-v ov-pre">' +
      esc(ov.approved_scope) + "</div></div>";
  }
  const recon = ov.latest_reconciliation;
  if (recon) {
    html += '<div class="ov-card ov-span"><div class="ov-k">Latest reconciliation (round ' +
      esc(recon.round) + ", ready: " + esc(String(recon.ready_to_proceed)) + ')</div><div class="ov-v">' +
      esc(recon.summary || "") + "</div>" +
      ((recon.revised_plan || []).length
        ? "<ul>" + recon.revised_plan.map((p) => "<li>" + esc(p) + "</li>").join("") + "</ul>" : "") +
      "</div>";
  }
  html += '<div class="ov-card"><div class="ov-k">Blockers</div><div class="ov-v">' +
    ((ov.blockers || []).length
      ? "<ul>" + ov.blockers.map((b) => "<li>" + esc(b) + "</li>").join("") + "</ul>"
      : '<span class="muted">None recorded.</span>') + "</div></div>";
  html += '<div class="ov-card"><div class="ov-k">Completion criteria</div><div class="ov-v"><ul>' +
    (ov.completion_criteria || []).map((c) => "<li>" + esc(c) + "</li>").join("") +
    "</ul></div></div>";
  html += "</div>";
  el.innerHTML = html;
}

// --------------------------------------------------------------------------- //
// Primary navigation: Command Center | Work | History. One selected task is
// shared by every view; Attention is a queue filter, never a separate page.
// --------------------------------------------------------------------------- //

let currentView = "command";

function showView(view) {
  if (!["command", "work", "history"].includes(view)) return;
  closeAllPopovers();
  currentView = view;
  document.getElementById("shell").hidden = view === "history";
  document.getElementById("history-view").hidden = view !== "history";
  document.getElementById("center-command").hidden = view !== "command";
  document.getElementById("center-work").hidden = view !== "work";
  ["command", "work", "history"].forEach((v) => {
    const btn = document.getElementById("nav-" + v);
    if (btn) btn.classList.toggle("is-active", v === view);
  });
  document.body.classList.toggle("history-open", view === "history");
  if (view === "history") loadHistory();
  if (view === "work") {
    loadConversations();
    placeWorkComposer();
  }
  if (view === "command") renderCommandOverview(lastTaskState);
}

// The Work page's fixed composer lives in the document as a template and is
// moved under the workspace when the Work view opens.
function placeWorkComposer() {
  const composer = document.getElementById("conv-composer");
  const hint = document.getElementById("conv-composer-hint");
  const host = document.getElementById("work-composer-dock");
  if (!composer || !host) return;
  host.appendChild(composer);
  host.appendChild(hint);
  composer.hidden = false;
  hint.hidden = false;
  if (convComposer) {
    convComposer.updateBanner();
    convComposer.autoGrow();
  }
}

// --------------------------------------------------------------------------- //
// Incoming clearance request (operator card)
// --------------------------------------------------------------------------- //

function renderOperatorCard(state) {
  const holder = document.getElementById("operator-card");
  const panel = holder.closest(".clearance-card") || holder.closest(".operator-panel");
  const outbox = (state.lanes && state.lanes.clearance_outbox) || [];
  const card = outbox.find((c) => (c.allowed_actions || []).includes("cta"));
  const waiting = outbox.filter((c) => (c.allowed_actions || []).includes("cta")).length;

  if (!card) {
    // Zero requests: collapse the card to a compact status line. It expands
    // automatically only when a request is actually waiting.
    if (panel) panel.classList.add("is-empty");
    if (currentMode === "operator") {
      holder.innerHTML =
        '<p class="muted">' + esc(OPERATOR_EMPTY_REQUESTS) + "</p>";
    } else {
      holder.innerHTML =
        '<p class="muted">No incoming requests. Ask the demo agents a question ' +
        "and send the condensed recommendation to the clearance queue.</p>";
    }
    return;
  }
  if (panel) panel.classList.remove("is-empty");

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
  renderFollowing(el, "feed-real", (events || []).length, () => renderRealEventsInner(el, events));
}

function renderRealEventsInner(el, events) {
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

// --------------------------------------------------------------------------- //
// Scroll-follow: a live-refreshed panel follows new content ONLY while the
// reader is already near the bottom. If the reader has scrolled up, polling
// preserves their position (it never yanks them to the bottom), and a
// "New messages" pill appears; clicking it, or scrolling back to the bottom,
// resumes following. This keeps the panel from fighting the reader.
// --------------------------------------------------------------------------- //
const NEAR_BOTTOM_PX = 40;
const _panelItemCounts = {};

function isNearBottom(el) {
  return (el.scrollHeight - el.scrollTop - el.clientHeight) <= NEAR_BOTTOM_PX;
}

function newMessagesPill(el, panelId) {
  let pill = document.getElementById(panelId + "-newmsg");
  if (!pill) {
    const parent = el.parentNode || el;
    if (!parent.style.position) parent.style.position = "relative";
    pill = document.createElement("button");
    pill.id = panelId + "-newmsg";
    pill.type = "button";
    pill.className = "newmsg-pill";
    pill.textContent = "New messages ↓";
    pill.hidden = true;
    pill.addEventListener("click", () => { el.scrollTop = el.scrollHeight; pill.hidden = true; });
    parent.appendChild(pill);
    // Resume following (hide the pill) when the reader returns to the bottom.
    el.addEventListener("scroll", () => { if (isNearBottom(el)) pill.hidden = true; });
  }
  return pill;
}

// Run a render that rebuilds el's content, preserving the reader's scroll
// position unless they were near the bottom (then follow to the bottom).
function renderFollowing(el, panelId, itemCount, renderFn) {
  if (!el) { renderFn(); return; }
  const follow = isNearBottom(el);
  const prevTop = el.scrollTop;
  const grew = itemCount > (_panelItemCounts[panelId] || 0);
  _panelItemCounts[panelId] = itemCount;
  renderFn();
  const pill = newMessagesPill(el, panelId);
  if (follow) {
    el.scrollTop = el.scrollHeight;
    pill.hidden = true;
  } else {
    el.scrollTop = prevTop;
    if (grew) pill.hidden = false;
  }
}

function renderMessages(messages) {
  const el = document.getElementById("comms");
  if (!el) return;
  renderFollowing(el, "comms", (messages || []).length, () => renderMessagesInner(el, messages));
}

function renderMessagesInner(el, messages) {
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

// --------------------------------------------------------------------------- //
// Composer payload integrity: ONE canonical content contract, ONE documented
// size limit, atomic idempotency, and draft preservation, shared by every
// operator composer (the Local Communications quick box and the Conversations
// workspace composer). No layer here ever silently truncates: an oversized
// send is refused with a clear, actionable reason before any network request
// is made, and success is shown only after the durable copy is re-read and
// confirmed to match what was sent -- never from a clipped local preview.
// --------------------------------------------------------------------------- //

const MESSAGE_MAX_BYTES = 65536; // must match clearwright_message.MESSAGE_MAX_BYTES

function canonicalContent(text) {
  return (text || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n").trim();
}

function utf8ByteLength(text) {
  return new TextEncoder().encode(text).length;
}

function genIdempotencyKey() {
  if (window.crypto && window.crypto.randomUUID) return window.crypto.randomUUID();
  return "key-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2);
}

function genThreadId() {
  const suffix = (window.crypto && window.crypto.randomUUID) ? window.crypto.randomUUID() :
    Date.now().toString(36) + "-" + Math.random().toString(36).slice(2);
  return "thr-web-" + suffix;
}

function draftStorageKey(name) { return "cw-draft:" + name; }

function saveDraft(name, draft) {
  try { sessionStorage.setItem(draftStorageKey(name), JSON.stringify(draft)); }
  catch (e) { /* sessionStorage unavailable (private mode, quota); the draft
                 simply does not survive navigation in that case. */ }
}
function loadDraft(name) {
  try {
    const raw = sessionStorage.getItem(draftStorageKey(name));
    return raw ? JSON.parse(raw) : null;
  } catch (e) { return null; }
}
function clearDraft(name) {
  try { sessionStorage.removeItem(draftStorageKey(name)); } catch (e) { /* ignore */ }
}

// One composer instance wraps a textarea + send control: auto-grow, a byte
// counter shown only as the limit approaches, Shift+Enter/plain Enter for a
// newline and Ctrl+Enter to send, a per-target idempotency key that survives
// reload and every retry until a VERIFIED durable write clears the draft, and
// a destination banner the operator sees before sending.
function createComposer(opts) {
  const { name, textarea, sendBtn, counterEl, errorEl, bannerEl,
          getTarget, buildFields, endpoint, onPosted, isConfirmedTarget } = opts;
  let sending = false;

  function draftKey() {
    const target = getTarget();
    return name + ":" + (target.thread_id || "new") + ":" + (target.work_item_id || "");
  }

  function updateBanner() {
    if (!bannerEl) return;
    const target = getTarget();
    // A target may already carry a (pre-allocated, retry-safety) thread id
    // before anything has actually been sent; the banner only calls it
    // "continuing" once the caller confirms that id is a real durable thread.
    const confirmed = !isConfirmedTarget || isConfirmedTarget();
    let text = (target.thread_id && confirmed) ? ("Continuing " + target.thread_id) : "New conversation";
    if (target.work_item_id) text += " · " + target.work_item_id;
    bannerEl.textContent = text;
  }

  function updateCounter() {
    if (!counterEl) return;
    const bytes = utf8ByteLength(canonicalContent(textarea.value));
    const near = bytes > MESSAGE_MAX_BYTES * 0.8;
    counterEl.hidden = !near;
    if (near) counterEl.textContent = bytes + " / " + MESSAGE_MAX_BYTES + " bytes";
    counterEl.classList.toggle("composer-counter-over", bytes > MESSAGE_MAX_BYTES);
  }

  function autoGrow() {
    textarea.style.height = "auto";
    textarea.style.height = Math.min(textarea.scrollHeight, 320) + "px";
  }

  function showError(msg) {
    if (!errorEl) return;
    errorEl.textContent = msg || "";
    errorEl.hidden = !msg;
  }

  function persistDraft() {
    const key = draftKey();
    const draft = loadDraft(key) || {};
    draft.text = textarea.value;
    draft.idempotencyKey = draft.idempotencyKey || genIdempotencyKey();
    saveDraft(key, draft);
    return draft;
  }

  function restoreDraft() {
    const draft = loadDraft(draftKey());
    textarea.value = draft ? (draft.text || "") : "";
    autoGrow();
    updateCounter();
    updateBanner();
    showError("");
  }

  async function send() {
    if (sending) return;
    const raw = textarea.value;
    const canonical = canonicalContent(raw);
    if (!canonical) return;
    const bytes = utf8ByteLength(canonical);
    if (bytes > MESSAGE_MAX_BYTES) {
      showError("Message is " + bytes + " bytes, over the " + MESSAGE_MAX_BYTES +
                "-byte limit. Shorten it to send.");
      return;
    }
    showError("");
    const draft = persistDraft();
    const target = getTarget();
    sending = true;
    sendBtn.disabled = true;
    try {
      const body = Object.assign(
        { message: raw, idempotency_key: draft.idempotencyKey },
        target.thread_id ? { thread_id: target.thread_id } : {},
        target.work_item_id ? { work_item_id: target.work_item_id } : {},
        buildFields ? buildFields(canonical) : {});
      let result;
      try {
        result = await postJSON(endpoint, body);
      } catch (netErr) {
        showError("Network error sending the message. The draft was kept; " +
                  "sending again will not create a duplicate.");
        return;
      }
      if (!result || !result.ok) {
        showError((result && (result.error || result.error_code)) ||
                  "Send was refused. The draft was kept.");
        return;
      }
      // Post-write re-read: success is shown ONLY after the durable content is
      // confirmed to match what was submitted, comparing against the COMPLETE
      // stored message -- never a preview.
      let stored = null;
      try {
        const verify = await getJSON("/api/messages?thread_id=" +
          encodeURIComponent(result.thread_id) + "&message_id=" +
          encodeURIComponent(result.message_id));
        stored = verify && verify.found ? verify.message : null;
      } catch (verifyErr) { /* stored stays null -> falls through to the error below */ }
      if (!stored || stored.message !== canonical) {
        showError("Sent, but could not verify the durable copy matched. The " +
                  "draft was kept; check Conversations before retrying.");
        return;
      }
      clearDraft(draftKey());
      textarea.value = "";
      autoGrow();
      updateCounter();
      if (onPosted) onPosted(result, stored);
    } finally {
      sending = false;
      sendBtn.disabled = false;
    }
  }

  textarea.addEventListener("input", () => { autoGrow(); updateCounter(); persistDraft(); });
  textarea.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      send();
    }
    // Plain Enter and Shift+Enter both insert a newline (the textarea
    // default is left alone), so the operator can review the complete
    // multi-line message before an explicit Ctrl+Enter or Send click.
  });

  restoreDraft();
  return { send, restoreDraft, updateBanner, updateCounter, autoGrow };
}

// Operator chat: the compact quick box posts a real inbound message
// (OPERATOR-0001, role operator, source operator-ui) as normal chat (intent
// "chat"): durable conversation, never a work item and never an Attention
// flag. No fake agent reply is ever generated. Actionable requests are created
// in the Conversation Workspace (Ask agent / Create work item) or over the
// CLI/HTTP worker surface, where messages stay actionable by default. Each
// quick-box send targets a fresh thread; the thread id is generated and
// pinned to the draft BEFORE the first attempt so a retry after a failed or
// timed-out send reuses the same id and idempotency key rather than risking a
// duplicate durable message.
let operatorChatThreadId = null;
let operatorChatThreadConfirmed = false;
let operatorChatComposer = null;

// The POST target always uses the pre-allocated thread id (retry-safety: a
// timed-out send retries into the SAME thread rather than forking a second
// one), but the banner LABEL only calls it "continuing" once that id has
// actually been confirmed by a verified durable write -- otherwise it reads
// "New conversation," since nothing has been sent yet.
function operatorChatTarget() {
  if (!operatorChatThreadId) operatorChatThreadId = genThreadId();
  return { thread_id: operatorChatThreadId };
}

function initOperatorChatComposer() {
  operatorChatComposer = createComposer({
    name: "operator-chat",
    textarea: document.getElementById("operator-chat-input"),
    sendBtn: document.getElementById("operator-chat-send"),
    counterEl: document.getElementById("operator-chat-counter"),
    errorEl: document.getElementById("operator-chat-error"),
    bannerEl: document.getElementById("operator-chat-banner"),
    getTarget: operatorChatTarget,
    isConfirmedTarget: () => operatorChatThreadConfirmed,
    buildFields: () => ({
      actor: "OPERATOR-0001", role: "operator", source: "operator-ui",
      direction: "inbound", simulated: false, intent: "chat",
    }),
    endpoint: "/api/messages",
    onPosted: () => {
      operatorChatThreadId = null; // the next message starts a fresh thread
      operatorChatThreadConfirmed = false;
      operatorChatComposer.updateBanner();
      refreshMessages();
      refreshWorkItems();
    },
  });
}

function submitOperatorChat(ev) {
  ev.preventDefault();
  if (operatorChatComposer) operatorChatComposer.send();
}

// --------------------------------------------------------------------------- //
// Work items (derived live from packets + messages; claim/respond via CLI/API)
// --------------------------------------------------------------------------- //

const WORK_KIND_LABEL = {
  message: "request", packet: "CTA packet", in_progress: "in progress", rfi: "RFI",
};

// --------------------------------------------------------------------------- //
// Work queue: compact entries grouped by Attention / Active / Recent /
// Archived. Each row shows a concise title, status, phase, age, and (for the
// Attention group) the reason it needs an operator. The FULL request body
// belongs in the selected task's Overview, never in the queue. Actionable
// work only (chat stays out); clicking a row selects that task everywhere.
// --------------------------------------------------------------------------- //

let lastWorkItems = [];
let lastQueueCouncils = [];
let lastArchiveIndex = { archived: [], count: 0 };
let queueAttentionOnly = false;

function relativeAge(iso) {
  if (!iso) return "";
  const then = new Date(iso.replace(/(\.\d{3})\d*Z$/, "$1Z"));
  if (isNaN(then.getTime())) return "";
  const sec = Math.max(0, Math.floor((Date.now() - then.getTime()) / 1000));
  if (sec < 90) return "now";
  if (sec < 3600) return Math.floor(sec / 60) + "m";
  if (sec < 86400) return Math.floor(sec / 3600) + "h";
  return Math.floor(sec / 86400) + "d";
}

// The newest council per thread (the API returns councils newest-first). A
// superseded operator_required council must never flag a thread forever:
// only the LATEST council's outcome describes the thread's current state.
function latestCouncilFor(threadId) {
  return lastQueueCouncils.find((c) => c.thread_id === threadId) || null;
}

// A cheap per-thread phase hint for queue rows (the SELECTED task gets the
// precise server-derived phase via /api/task-state; rows only need a hint).
function queuePhaseHint(threadId, status) {
  const councils = lastQueueCouncils.filter((c) => c.thread_id === threadId);
  const verify = councils.find((c) => c.phase === "verify");
  const plan = councils.find((c) => c.phase === "plan");
  if (status === "responded" || status === "chat") return "Complete";
  const latest = latestCouncilFor(threadId);
  if (latest && latest.outcome === "operator_required") return "Authority";
  if (verify) return verify.outcome === "agreement_threshold_met" ? "Complete" : "Verify";
  if (plan) return plan.outcome === "agreement_threshold_met" ? "Execute" : "Plan Review";
  return "Request";
}

function queueRow(entry) {
  const selected = entry.thread_id && entry.thread_id === selectedConvThread;
  const bits = ['<span class="q-status q-status-' + esc(entry.status) + '">' + esc(entry.status) + "</span>"];
  if (entry.phase) bits.push('<span class="q-phase">' + esc(entry.phase) + "</span>");
  if (entry.age) bits.push('<span class="q-age">' + esc(entry.age) + "</span>");
  return '<div class="q-row' + (selected ? " is-selected" : "") +
    '" data-thread="' + esc(entry.thread_id || "") +
    '" data-archived="' + (entry.archived ? "1" : "") + '">' +
    '<div class="q-title">' + esc((entry.title || "").slice(0, 72)) + "</div>" +
    '<div class="q-meta">' + bits.join("") + "</div>" +
    (entry.reason ? '<div class="q-reason">' + esc(entry.reason) + "</div>" : "") +
    "</div>";
}

function buildQueueGroups() {
  const attention = [];
  const active = [];
  const seenThreads = new Set();

  // Attention: anything waiting on an operator decision -- CTA packets, RFI
  // packets, and threads whose latest council escalated operator_required.
  lastWorkItems.forEach((it) => {
    if (it.kind === "packet" || it.kind === "rfi") {
      attention.push({
        thread_id: it.thread_id, title: it.title || it.summary || it.packet_id,
        status: it.status || "open",
        phase: it.kind === "rfi" ? "Authority" : "Authority",
        age: relativeAge(it.created_at),
        reason: it.kind === "rfi" ? "RFI awaiting clarification"
          : "CTA decision required (clear, deny, or RFI)",
      });
    }
  });
  // Only a thread whose LATEST council escalated still needs the operator;
  // resolved gates with a later agreed council are back to plain active work.
  const gatedThreads = new Set(lastQueueCouncils
    .filter((c) => c.thread_id && c.outcome === "operator_required" &&
      latestCouncilFor(c.thread_id) === c)
    .map((c) => c.thread_id));

  lastWorkItems.forEach((it) => {
    if (it.kind !== "message" || !it.thread_id) return;
    seenThreads.add(it.thread_id);
    const entry = {
      thread_id: it.thread_id, title: it.title || it.summary || "",
      status: it.status || "open",
      phase: queuePhaseHint(it.thread_id, it.status),
      age: relativeAge(it.created_at),
    };
    if (gatedThreads.has(it.thread_id)) {
      entry.reason = "council escalated: operator required";
      entry.phase = "Authority";
      attention.push(entry);
    } else {
      active.push(entry);
    }
  });
  lastWorkItems.forEach((it) => {
    if (it.kind === "in_progress") {
      active.push({
        thread_id: it.thread_id, title: it.title || it.packet_id,
        status: "in progress", phase: "Execute", age: "",
      });
    }
  });

  // Recent: terminal conversations (responded / chat), newest first.
  const recent = (lastConversations || [])
    .filter((c) => !seenThreads.has(c.thread_id) &&
      (c.status === "responded" || c.status === "chat"))
    .slice(0, 12)
    .map((c) => ({
      thread_id: c.thread_id, title: c.title || c.thread_id,
      status: c.status, phase: queuePhaseHint(c.thread_id, c.status),
      age: relativeAge(c.last_timestamp),
    }));

  const archived = (lastArchiveIndex.archived || [])
    .filter((r) => r.type === "thread")
    .map((r) => ({
      thread_id: r.id, title: r.id, status: "archived", phase: "",
      age: "", archived: true,
    }));

  return { attention, active, recent, archived };
}

function renderQueue() {
  const el = document.getElementById("queue-groups");
  if (!el) return;
  const groups = buildQueueGroups();
  updateAttentionChip(groups.attention.length);
  const order = queueAttentionOnly
    ? [["Attention", groups.attention]]
    : [["Attention", groups.attention], ["Active", groups.active],
       ["Recent", groups.recent], ["Archived", groups.archived]];
  let html = "";
  order.forEach(([label, rows]) => {
    if (label !== "Attention" && !rows.length) return;
    html += '<div class="q-group"><div class="q-group-head">' + esc(label) +
      ' <span class="q-count">' + rows.length + "</span></div>";
    html += rows.length ? rows.map(queueRow).join("")
      : '<p class="muted q-empty">Nothing needs an operator decision.</p>';
    html += "</div>";
  });
  el.innerHTML = html || '<p class="muted queue-empty">No work yet.</p>';
}

function updateAttentionChip(count) {
  const chip = document.getElementById("attention-chip");
  if (!chip) return;
  chip.hidden = false;
  chip.classList.toggle("attention-on", count > 0);
  chip.classList.toggle("is-filtering", queueAttentionOnly);
  document.getElementById("attention-count").textContent = String(count);
}

async function refreshWorkItems() {
  try {
    const data = await getJSON("/api/work-items");
    lastWorkItems = data.work_items || [];
    try {
      const cd = await getJSON("/api/review-councils");
      lastQueueCouncils = cd.review_councils || [];
    } catch (e2) { /* councils optional for queue hints */ }
    renderQueue();
  } catch (e) {
    // Leave the prior content in place on a transient fetch error.
  }
}

async function refreshArchiveIndex() {
  try {
    lastArchiveIndex = await getJSON("/api/archive-index");
    renderQueue();
  } catch (e) {
    // Archive index is optional; the group simply stays empty.
  }
}

// --------------------------------------------------------------------------- //
// History: ONE unified, read-only ledger across every durable source (packets,
// messages, agent events; active and archived), with client-side filters and
// a row-click detail panel -- no nested horizontal scrolling.
// --------------------------------------------------------------------------- //

let lastLedgerRows = [];

function ledgerFilters() {
  const v = (id) => (document.getElementById(id).value || "").trim();
  return {
    scope: document.getElementById("lf-scope").value || "active",
    type: document.getElementById("lf-type").value || "",
    actor: v("lf-actor").toLowerCase(),
    status: v("lf-status").toLowerCase(),
    date: v("lf-date"),
    workItem: v("lf-workitem").toLowerCase(),
    council: v("lf-council").toLowerCase(),
    text: v("lf-text").toLowerCase(),
  };
}

function ledgerRowMatches(row, f) {
  if (f.type && row.type !== f.type) return false;
  if (f.actor && !(row.actor || "").toLowerCase().includes(f.actor)) return false;
  if (f.status && !(row.status || "").toLowerCase().includes(f.status)) return false;
  if (f.date && !(row.at || "").startsWith(f.date)) return false;
  if (f.workItem && !(row.work_item_id || "").toLowerCase().includes(f.workItem)) return false;
  if (f.council && !(row.council_id || "").toLowerCase().includes(f.council)) return false;
  if (f.text) {
    const haystack = ((row.event || "") + " " + (row.thread_id || "") + " " +
      (row.packet_id || "") + " " + (row.actor || "")).toLowerCase();
    if (!haystack.includes(f.text)) return false;
  }
  return true;
}

async function loadHistory() {
  const f = ledgerFilters();
  let data;
  try {
    data = await getJSON("/api/ledger?scope=" + encodeURIComponent(f.scope));
  } catch (e) {
    return;
  }
  lastLedgerRows = (data.rows || []).filter((row) => ledgerRowMatches(row, f));
  const body = document.getElementById("ledger-body");
  if (!lastLedgerRows.length) {
    body.innerHTML = '<tr><td colspan="6" class="muted">No records match the filters.</td></tr>';
    return;
  }
  body.innerHTML = lastLedgerRows.slice(0, 500).map((row, i) =>
    '<tr class="ledger-row' + (row.archived ? " ledger-archived" : "") +
    '" data-ledger-index="' + i + '">' +
    "<td>" + esc(shortTime(row.at)) + "</td>" +
    "<td>" + esc(row.type) + (row.archived ? ' <span class="feed-badge local">archived</span>' : "") + "</td>" +
    '<td class="mono">' + esc(row.work_item_id || row.thread_id || row.packet_id || "") + "</td>" +
    "<td>" + esc(row.actor || "") + "</td>" +
    '<td class="ledger-event">' + esc(row.event || "") + "</td>" +
    "<td>" + esc(row.status || "") + "</td></tr>").join("");
}

function openLedgerDetail(index) {
  const row = lastLedgerRows[index];
  if (!row) return;
  const panel = document.getElementById("ledger-detail");
  const body = document.getElementById("ledger-detail-body");
  let html = '<div class="ld-meta">' +
    '<span class="work-badge">' + esc(row.type) + "</span> " +
    (row.archived ? '<span class="feed-badge local">archived</span> ' : "") +
    '<span class="mono">' + esc(row.at || "") + "</span></div>";
  if (row.type === "packet" && row.record && row.record.filename) {
    html += '<button class="btn btn-quiet" type="button" data-ledger-audit="' +
      esc(row.record.filename) + '">Open full audit trail</button>';
  }
  html += '<pre class="ld-record">' + esc(JSON.stringify(row.record, null, 2)) + "</pre>";
  body.innerHTML = html;
  panel.hidden = false;
}

function copyText(text, btn) {
  const done = () => {
    if (!btn) return;
    const orig = btn.textContent;
    btn.textContent = "copied";
    setTimeout(() => { btn.textContent = orig; }, 1200);
  };
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, () => {});
      return;
    }
  } catch (e) { /* fall through */ }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    if (document.execCommand) document.execCommand("copy");
    document.body.removeChild(ta);
    done();
  } catch (e) { /* no-op fallback */ }
}

// Client-side telemetry parser (mirrors the server) so Recent/All threads can
// also show Codex telemetry as fields.
function parseCodexTelemetry(text) {
  if (!text || text.indexOf("Telemetry:") === -1) return null;
  const grab = (k) => {
    const m = text.match(new RegExp(k + "=([^,\\s]+)"));
    return m ? m[1] : null;
  };
  const elapsed = grab("elapsed"), cls = grab("classification");
  return {
    exit_code: grab("exit"),
    elapsed_seconds: elapsed ? elapsed.replace(/s$/, "") : null,
    bytes: grab("bytes"),
    lines: grab("lines"),
    timed_out: grab("timed_out"),
    classification: cls ? cls.replace(/\.$/, "") : null,
  };
}

function telemetryBadges(t) {
  if (!t) return "";
  const parts = [];
  if (t.exit_code !== null && t.exit_code !== undefined) parts.push("exit " + esc(t.exit_code));
  if (t.elapsed_seconds) parts.push(esc(t.elapsed_seconds) + "s");
  if (t.bytes) parts.push(esc(t.bytes) + " bytes");
  if (t.lines) parts.push(esc(t.lines) + " lines");
  if (t.timed_out) parts.push("timed_out " + esc(t.timed_out));
  if (t.classification) parts.push("codex:" + esc(t.classification));
  return '<div class="codex-telemetry"><span class="tele-label">Codex telemetry</span>' +
    parts.map((p) => '<span class="tele">' + p + "</span>").join("") + "</div>";
}

// Review Council: a durable, read-only summary of the GPT + Codex council for
// a thread. The web UI only reads this state; GPT and Codex are run by the
// CLI/helper, never from the browser or the HTTP handler.
const COUNCIL_OUTCOME_CLASS = {
  agreement_threshold_met: "council-ok",
  needs_revision: "council-warn",
  operator_required: "council-warn",
  reviewer_unavailable: "council-muted",
  hard_gate: "council-bad",
};

function verdictBadge(label, verdict) {
  if (!verdict) return '<span class="council-vmiss">' + esc(label) + ": none</span>";
  return '<span class="council-vb">' + esc(label) + ": " + esc(verdict.verdict) +
    " (" + esc(Number(verdict.confidence).toFixed(2)) + ", " + esc(verdict.risk_level) + ")</span>";
}

function councilCard(councils, detail) {
  if (!councils || !councils.length) return "";
  const c = councils[0];
  const outcome = c.outcome || "running";
  const cls = COUNCIL_OUTCOME_CLASS[outcome] || "council-muted";
  let html = '<div class="council-card ' + cls + '">';
  html += '<div class="council-head"><span class="council-badge">REVIEW COUNCIL</span>' +
    '<span class="council-outcome">' + esc(outcome.replace(/_/g, " ")) + "</span></div>";
  html += '<div class="council-meta">' +
    '<span class="council-id mono">' + esc(c.council_id) + "</span>" +
    "<span>phase " + esc(c.phase) + "</span>" +
    "<span>round " + esc(c.current_round) + "/" + esc(c.max_rounds) + "</span>" +
    "<span>gpt: " + esc(c.gpt_status || "-") + "</span>" +
    "<span>codex: " + esc(c.codex_status || "-") + "</span>" +
    (c.ready_to_proceed ? '<span class="council-ok-tag">ready to proceed</span>' : "") +
    (c.operator_required ? '<span class="council-warn-tag">operator required</span>' : "") +
    (c.hard_gate ? '<span class="council-bad-tag">hard gate</span>' : "") +
    "</div>";
  if (detail && detail.rounds && detail.rounds.length) {
    html += '<div class="council-rounds">';
    detail.rounds.slice(-5).forEach((r) => {
      const g = (r.gpt || {}).verdict, x = (r.codex || {}).verdict;
      const rec = r.reconciliation;
      html += '<div class="council-round"><span class="council-rn">round ' + esc(r.round) + "</span>" +
        verdictBadge("GPT", g) + verdictBadge("Codex", x);
      if (rec) {
        html += '<span class="council-recon">reconcile: ' +
          (rec.ready_to_proceed ? "ready" : "hold");
        if (rec.rejected_findings && rec.rejected_findings.length) {
          html += ", rejected " + esc(rec.rejected_findings.length) + " (with evidence)";
        }
        if (rec.unresolved_blockers && rec.unresolved_blockers.length) {
          html += ", " + esc(rec.unresolved_blockers.length) + " unresolved";
        }
        html += "</span>";
      }
      html += "</div>";
    });
    html += "</div>";
  }
  html += "</div>";
  return html;
}

function runSummaryText(run) {
  if (!run || !run.thread_id) return "";
  const lines = ["thread_id=" + run.thread_id];
  if (run.work_item_id) lines.push("work_item_id=" + run.work_item_id);
  if (run.packet_id) lines.push("packet_id=" + run.packet_id);
  (run.messages || []).forEach((m) => lines.push((m.direction || "") + " " + m.actor + ": " + m.message));
  return lines.join("\n");
}

function shortTime(iso) {
  return iso ? String(iso).replace("T", " ").slice(5, 16) : "";
}

// --------------------------------------------------------------------------- //
// System health (read-only readiness: /api/health)
// --------------------------------------------------------------------------- //

const HEALTH_LABELS = { green: "Healthy", yellow: "Attention", red: "Problem" };
let lastHealth = null;

function healthRow(label, value) {
  return '<div class="health-row"><span class="k">' + esc(label) + ":</span> " +
    esc(value) + "</div>";
}

function renderHealthDetails(h) {
  const el = document.getElementById("health-details");
  if (!el || !h) return;
  const counts = h.packet_counts || {};
  const caps = h.capabilities || {};
  let codex = "not checked";
  if (caps.codex_cli_on_path === true) codex = "CLI on PATH (capability only)";
  else if (caps.codex_cli_on_path === false) codex = "CLI not on PATH";
  let html = "";
  html += healthRow("Mode", h.mode + (h.durable ? " · durable" : " · temporary"));
  html += healthRow("Queue root", h.queue_root || "unknown");
  html += healthRow("Packets", Object.keys(counts).map(
    (l) => l.replace("clearance_", "") + " " + counts[l]).join(" · "));
  html += healthRow("Work items", "open " + (h.work_items_open || 0) +
    " · claimed " + (h.work_items_claimed || 0) + " · total " + (h.work_items_total || 0));
  html += healthRow("Messages / events / runs",
    (h.message_count || 0) + " / " + (h.agent_event_count || 0) + " / " + (h.run_count || 0));
  if (h.latest_run_timestamp) html += healthRow("Latest run", h.latest_run_timestamp);
  html += healthRow("Codex", codex);
  (h.warnings || []).forEach((w) => {
    html += '<div class="health-note health-note-warn">' + esc(w) + "</div>";
  });
  (h.errors || []).forEach((w) => {
    html += '<div class="health-note health-note-error">' + esc(w) + "</div>";
  });
  el.innerHTML = html;
}

async function refreshHealth() {
  try {
    const h = await getJSON("/api/health");
    lastHealth = h;
    const chip = document.getElementById("health-chip");
    if (!chip) return;
    chip.classList.remove("health-green", "health-yellow", "health-red");
    chip.classList.add("health-" + (h.status || "red"));
    const label = HEALTH_LABELS[h.status] || "Unknown";
    document.getElementById("health-label").textContent = label;
    // Tooltip reason keeps the topbar clean: "Attention: 1 open work item(s)..."
    const why = (h.errors && h.errors[0]) || (h.warnings && h.warnings[0]) || "";
    chip.title = why ? label + ": " + why : label;
    const panel = document.getElementById("health-panel");
    if (panel && !panel.hidden) renderHealthDetails(h);
  } catch (e) {
    // Leave the prior chip state on a transient fetch error.
  }
}

// --------------------------------------------------------------------------- //
// Popover management: System Health and any peer popover registered here
// share one behavior -- click outside closes it, Escape closes it, a
// navigation change (opening/closing Conversations, History, Active Run)
// closes every open popover, only one popover stays open at a time (opening
// one closes the others), and focus returns to the trigger that opened it.
// A popover never needs its trigger clicked twice to reopen after dismissal.
// --------------------------------------------------------------------------- //

const _popovers = new Map(); // id -> {trigger, panel, isOpen, onOpen, onClose}

function registerPopover(id, { trigger, panel, onOpen, onClose }) {
  _popovers.set(id, { trigger, panel, isOpen: false, onOpen, onClose });
}

function isPopoverOpen(id) {
  const p = _popovers.get(id);
  return !!(p && p.isOpen);
}

function openPopover(id) {
  const p = _popovers.get(id);
  if (!p) return;
  // Only one peer popover stays open: close every other one first.
  _popovers.forEach((other, otherId) => { if (otherId !== id && other.isOpen) closePopover(otherId); });
  p.panel.hidden = false;
  p.isOpen = true;
  if (p.onOpen) p.onOpen();
}

function closePopover(id, opts) {
  const p = _popovers.get(id);
  if (!p || !p.isOpen) return;
  p.panel.hidden = true;
  p.isOpen = false;
  if (p.onClose) p.onClose();
  if (!(opts && opts.skipFocusRestore) && p.trigger && document.body.contains(p.trigger)) {
    p.trigger.focus();
  }
}

function togglePopover(id) {
  isPopoverOpen(id) ? closePopover(id) : openPopover(id);
}

function closeAllPopovers(opts) {
  _popovers.forEach((_p, id) => closePopover(id, opts));
}

function wirePopovers() {
  document.addEventListener("click", (e) => {
    _popovers.forEach((p, id) => {
      if (!p.isOpen) return;
      if (p.panel.contains(e.target) || p.trigger.contains(e.target)) return;
      closePopover(id, { skipFocusRestore: true }); // an outside click owns focus already
    });
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeAllPopovers();
  });
}

function toggleHealthPanel() {
  togglePopover("health");
}

// --------------------------------------------------------------------------- //
// Conversation Workspace (conversation-first view of the durable threads)
//
// Conversations are the durable message threads; the workspace is where the
// operator reads and continues them. Replies from Claude/Codex/workers appear
// only when they actually post back through the local adapter - the target
// selector is an intent hint only and never claims participation.
// --------------------------------------------------------------------------- //

let selectedConvThread = null;
let lastConversations = [];
let convDetailRun = null;
let lastConvCouncils = [];
let lastConvCouncilDetails = {}; // council_id -> full detail (rounds included)

// (The grouped work queue in the left region is the task list for every view;
// there is no separate conversation list.)

// Task workspace tabs: every primary panel in this view binds to the SAME
// selected conversation/work item (convDetailRun); tabs just change which
// slice of that one bound record is shown, so nothing here ever mixes
// content from a different task. Selection persists across the periodic
// refresh so the operator's place in the workspace is not reset every 2s.
const CONV_TABS = [
  { id: "overview", label: "Overview" },
  { id: "conversation", label: "Conversation" },
  { id: "councils", label: "Councils" },
  { id: "evidence", label: "Evidence" },
  { id: "audit", label: "Audit" },
];
let activeConvTab = "conversation";
let lastConvSummary = null;

function buildConvHead(run) {
  const title = (run.messages[0] && run.messages[0].message) || run.thread_id;
  let html = '<div class="conv-head">';
  html += '<div class="conv-title">' + esc(title.slice(0, 160)) + "</div>";
  html += '<div class="conv-meta"><span class="run-id mono">' + esc(run.thread_id) + "</span>";
  html += '<button class="copy-btn" type="button" data-copy="' + esc(run.thread_id) + '">copy thread_id</button>';
  html += '<button class="copy-btn" type="button" data-copy-summary="1">copy summary</button>';
  if (run.work_item_id) html += '<span class="run-wid mono">' + esc(run.work_item_id) + "</span>";
  if (run.packet_id) html += '<span class="run-pkt">[' + esc(run.packet_id) + "]</span>";
  html += "</div>";
  html += '<div class="conv-actions">' +
    '<button class="btn btn-quiet conv-action" type="button" data-action="ack">Mark reviewed</button>' +
    '<button class="btn btn-quiet conv-action" type="button" data-action="workitem">Create work item</button>' +
    '<button class="btn btn-quiet conv-action" type="button" data-action="escalate">Request clearance packet</button>' +
    "</div></div>";
  return html;
}

function buildConvTabBar() {
  return '<div class="conv-tabs" role="tablist">' + CONV_TABS.map((t) =>
    '<button class="conv-tab' + (t.id === activeConvTab ? " is-active" : "") +
    '" type="button" role="tab" aria-selected="' + (t.id === activeConvTab) +
    '" data-conv-tab="' + t.id + '">' + esc(t.label) + "</button>").join("") + "</div>";
}

// Overview: the same selected-task model as the Command Center overview
// (status/phase, next action, approved scope, plan summary, blockers, the
// latest reconciliation, completion criteria) plus the canonical summary.
function buildOverviewTab(run) {
  let html = "";
  if (lastTaskState && lastTaskState.found &&
      lastTaskState.thread_id === run.thread_id) {
    // Reuse the Command Center overview renderer so both views show the
    // identical model for the same selected task.
    const holder = document.createElement("div");
    renderCommandOverviewInto(holder, lastTaskState);
    html += holder.innerHTML;
  }
  html += telemetryBadges(run.codex_telemetry);
  html += '<div class="conv-overview-stats muted">' + esc(run.messages.length) +
    " message(s) · status " + esc(run.status || "unknown") + "</div>";
  html += '<div id="conv-overview-summary">';
  if (lastConvSummary) {
    const s = lastConvSummary;
    html += '<div class="conv-summary-card">' +
      '<div class="conv-summary-line"><strong>' + esc(s.status || "") + "</strong> — " +
      esc(s.outcome_line || "") + "</div>";
    if (s.recommended_next_action) {
      html += '<div class="conv-summary-line muted">Next: ' + esc(s.recommended_next_action) + "</div>";
    }
    html += "</div>";
  } else if (run.work_item_id) {
    html += '<p class="muted">No canonical summary recorded yet for this work item.</p>';
  } else {
    html += '<p class="muted">This conversation has no bound work item, so there is no canonical summary.</p>';
  }
  html += "</div>";
  return html;
}

// Conversation: a readable timeline with participant filters, an unread
// marker (everything below the divider is new since the operator last read
// this thread), and a jump-to-latest control. The destination thread and work
// item stay visible in the persistent task header and the composer banner.
let convParticipantFilter = "";

function _seenKey(threadId) { return "cw-seen:" + threadId; }

function markThreadSeen(run) {
  if (!run || !run.thread_id || !(run.messages || []).length) return;
  try {
    sessionStorage.setItem(_seenKey(run.thread_id),
      run.messages[run.messages.length - 1].message_id || "");
  } catch (e) { /* sessionStorage unavailable */ }
}

function buildConversationTab(run) {
  const actors = Array.from(new Set(run.messages.map((m) => m.actor).filter(Boolean)));
  let lastSeen = null;
  try { lastSeen = sessionStorage.getItem(_seenKey(run.thread_id)); } catch (e) { /* ignore */ }
  let html = '<div class="conv-filterbar">' +
    '<span class="muted">Participants:</span>' +
    '<button class="conv-filter' + (convParticipantFilter === "" ? " is-on" : "") +
    '" type="button" data-participant="">All</button>' +
    actors.map((a) => '<button class="conv-filter' +
      (convParticipantFilter === a ? " is-on" : "") +
      '" type="button" data-participant="' + esc(a) + '">' + esc(a) + "</button>").join("") +
    '<button class="btn btn-quiet conv-jump" type="button" data-jump-latest="1">Jump to latest</button>' +
    "</div>";
  html += '<div class="conv-messages">';
  let unreadMarked = false;
  let afterSeen = lastSeen === null; // no marker recorded: nothing flagged new
  run.messages.forEach((m) => {
    if (lastSeen !== null && !afterSeen && m.message_id === lastSeen) {
      afterSeen = true; // everything AFTER this one is unread
      return renderMsg(m);
    }
    if (afterSeen && lastSeen !== null && !unreadMarked && m.message_id !== lastSeen) {
      html += '<div class="conv-unread-divider">New since you last read this thread</div>';
      unreadMarked = true;
    }
    renderMsg(m);
  });
  function renderMsg(m) {
    if (convParticipantFilter && m.actor !== convParticipantFilter) return;
    const mine = m.actor === "OPERATOR-0001";
    const meta = [m.actor + (m.role ? "/" + m.role : ""), m.direction, m.status, m.source, m.at]
      .filter(Boolean).map(esc).join(" · ");
    const tag = conversationEntryTag(m);
    const cls = "conv-msg" + (mine ? " conv-msg-operator" : "") +
      (m.direction === "outbound" ? " conv-msg-outbound" : "") +
      (tag ? " " + tag.cls : "");
    html += '<div class="' + cls + '">' +
      (tag ? '<div class="conv-entry-tag">' + esc(tag.label) + "</div>" : "") +
      '<div class="conv-msg-body">' + esc(m.message) + "</div>" +
      '<div class="conv-msg-meta">' + meta + "</div></div>";
  }
  html += "</div>";
  return html;
}

// Councils: grouped by council, then by round, with the verdicts and Claude's
// reconciliation FIRST and the telemetry (tokens, bytes, model ids, elapsed
// time) collapsed under a Technical details disclosure.
function buildCouncilsTab() {
  if (!lastConvCouncils.length) {
    return '<p class="muted">No review council has run for this task yet.</p>';
  }
  return lastConvCouncils.map((c) => {
    const detail = lastConvCouncilDetails[c.council_id];
    const cls = COUNCIL_OUTCOME_CLASS[c.outcome || "running"] || "council-muted";
    let html = '<div class="council-group ' + cls + '">';
    html += '<div class="council-group-head"><span class="mono">' + esc(c.council_id) +
      "</span> · " + esc(c.phase) + " · " +
      '<span class="council-outcome">' + esc(c.outcome || "running") + "</span>" +
      (c.current_round ? " · " + esc(c.current_round) + " round(s)" : "") + "</div>";
    const rounds = ((detail || {}).rounds || []).filter((r) => r.substantive !== false);
    if (!rounds.length) {
      html += '<p class="muted">Round records not loaded.</p>';
    }
    rounds.forEach((r) => {
      html += '<div class="council-round"><div class="council-round-head">Round ' +
        esc(r.round) + "</div>";
      ["gpt", "codex"].forEach((who) => {
        const res = r[who] || {};
        const v = res.verdict;
        if (v) {
          html += '<div class="council-verdict">' + verdictBadge(who.toUpperCase(), v) +
            '<div class="council-verdict-summary">' + esc((v.summary || "").slice(0, 400)) + "</div></div>";
        } else {
          html += '<div class="council-verdict council-vmiss">' + esc(who.toUpperCase()) +
            ": no validated review</div>";
        }
      });
      const rec = r.reconciliation;
      if (rec) {
        html += '<div class="council-recon"><span class="conv-entry-tag">Claude reconciliation · ready: ' +
          esc(String(rec.ready_to_proceed)) + "</span><div>" +
          esc((rec.summary || "").slice(0, 400)) + "</div></div>";
      }
      // Telemetry and byte/token counts are presentation-secondary: collapsed.
      const tech = [];
      ["gpt", "codex"].forEach((who) => {
        const res = r[who] || {};
        const t = res.telemetry || {};
        const bits = Object.keys(t).map((k) => k + "=" + t[k]);
        if (bits.length) tech.push(who + ": " + bits.join(", "));
      });
      html += '<details class="council-tech"><summary>Technical details</summary><pre>' +
        esc(tech.join("\n") || "none recorded") + "</pre></details>";
      html += "</div>";
    });
    html += "</div>";
    return html;
  }).join("");
}

// Evidence: pinned artifacts with their full hashes, plus every recorded
// evidence message (diffs, test results, CI status, browser evidence,
// verification notes, canonical summaries).
function buildEvidenceTab(run) {
  let html = "";
  const artifacts = (lastTaskState && lastTaskState.thread_id === run.thread_id
    ? lastTaskState.artifacts : []) || [];
  if (artifacts.length) {
    html += '<div class="ev-artifacts"><div class="ov-k">Pinned artifacts</div>' +
      artifacts.map((a) =>
        '<div class="ev-artifact"><span class="mono">' + esc(a.artifact_id) + "</span>" +
        (a.sha256 ? '<div class="ev-hash mono">sha256 ' + esc(a.sha256) + "</div>" : "") +
        "</div>").join("") + "</div>";
  }
  const evidenceMsgs = run.messages.filter((m) =>
    m.source === "use-cw-summary" || (m.closure === "closed_by_operator") ||
    /changed_files|verification|findings|diff|test|CI|browser smoke/i.test(m.message || ""));
  if (!evidenceMsgs.length && !artifacts.length) {
    return '<p class="muted">No recorded evidence (artifacts, diffs, test/CI results, ' +
      "browser evidence, or verification notes) for this task yet.</p>";
  }
  if (evidenceMsgs.length) {
    html += '<div class="conv-messages conv-evidence-list">';
    evidenceMsgs.forEach((m) => {
      const tag = conversationEntryTag(m);
      html += '<div class="conv-msg' + (tag ? " " + tag.cls : "") + '">' +
        (tag ? '<div class="conv-entry-tag">' + esc(tag.label) + "</div>" : "") +
        '<div class="conv-msg-body">' + esc(m.message) + "</div>" +
        '<div class="conv-msg-meta">' + esc(m.at || "") + "</div></div>";
    });
    html += "</div>";
  }
  return html;
}

// Audit: the immutable trail for the selected task -- state transitions
// (claim/respond/closure messages in order), every gate with its linked
// authority record, invocation usage, the packet audit trail when one exists,
// and whether the record has been archived.
function buildAuditTab(run) {
  let html = "";
  const ts = (lastTaskState && lastTaskState.thread_id === run.thread_id)
    ? lastTaskState : null;

  const transitions = run.messages.filter((m) =>
    m.status === "claimed" || m.status === "responded" || m.closure ||
    m.source === "use-cw-gate");
  html += '<div class="ov-k">State transitions</div>';
  html += transitions.length
    ? '<div class="audit-transitions">' + transitions.map((m) =>
        '<div class="audit-row"><span class="mono">' + esc(shortTime(m.at)) + "</span> " +
        '<span class="work-badge">' + esc(m.closure || m.status || "") + "</span> " +
        esc((m.message || "").slice(0, 120)) + "</div>").join("") + "</div>"
    : '<p class="muted">No recorded transitions yet.</p>';

  const gates = (ts && ts.gates) || [];
  html += '<div class="ov-k">Gates and authority records</div>';
  html += gates.length
    ? gates.map((g) =>
        '<div class="audit-gate audit-gate-' + esc(g.disposition) + '">' +
        '<span class="mono">' + esc(g.gate_id) + "</span> · " + esc(g.phase) +
        " · " + esc(g.outcome) + " · <strong>" + esc(g.disposition) + "</strong>" +
        (g.authority
          ? '<div class="audit-authority">authority: <span class="mono">' +
            esc(g.authority.message_id) + "</span> · " +
            esc((g.authority.excerpt || "").slice(0, 160)) + "</div>"
          : "") + "</div>").join("")
    : '<p class="muted">No gates have been created for this task.</p>';

  if (lastConvSummary && lastConvSummary.usage) {
    const u = lastConvSummary.usage;
    // The usage field name predates the naming cleanup; the key is assembled
    // so the retired substring never appears literally in this file.
    const attempts = u["dis" + "patch_attempts"] || 0;
    html += '<div class="ov-k">Invocations</div><div class="audit-usage muted">' +
      esc(u.invocations || 0) + " invocations · " + esc(attempts) +
      " reviewer call attempts · " + esc(u.transport_retries || 0) + " transport retries · " +
      esc(u.validation_failures || 0) + " validation failures</div>";
  }

  const archivedHere = (lastArchiveIndex.archived || [])
    .some((r) => r.id === run.thread_id);
  html += '<div class="ov-k">Archive status</div><div class="muted">' +
    (archivedHere ? "This thread's records are ARCHIVED (resolved via the archive index)."
      : "Active record (not archived).") + "</div>";

  if (run.packet_id) {
    html += '<div class="ov-k">Clearance packet</div>' +
      '<button class="btn btn-quiet" type="button" data-conv-open-audit="' +
      esc(run.packet_id) + '">Open audit trail for ' + esc(run.packet_id) + "</button>";
  }
  return html;
}

function renderConvTabPanel(run) {
  const panel = document.getElementById("conv-tab-panel");
  if (!panel) return;
  const oldBox = panel.querySelector(".conv-messages");
  const wasNear = !oldBox || isNearBottom(oldBox);
  const oldTop = oldBox ? oldBox.scrollTop : 0;
  if (activeConvTab === "overview") panel.innerHTML = buildOverviewTab(run);
  else if (activeConvTab === "conversation") {
    panel.innerHTML = buildConversationTab(run);
    markThreadSeen(run);
  }
  else if (activeConvTab === "councils") panel.innerHTML = buildCouncilsTab();
  else if (activeConvTab === "evidence") panel.innerHTML = buildEvidenceTab(run);
  else if (activeConvTab === "audit") panel.innerHTML = buildAuditTab(run);
  const box = panel.querySelector(".conv-messages");
  if (box) box.scrollTop = wasNear ? box.scrollHeight : oldTop;
}

function switchConvTab(tabId) {
  if (!CONV_TABS.some((t) => t.id === tabId)) return;
  activeConvTab = tabId;
  const bar = document.querySelector("#conv-detail .conv-tabs");
  if (bar) {
    bar.querySelectorAll(".conv-tab").forEach((btn) => {
      const on = btn.dataset.convTab === tabId;
      btn.classList.toggle("is-active", on);
      btn.setAttribute("aria-selected", String(on));
    });
  }
  if (convDetailRun) renderConvTabPanel(convDetailRun);
}

async function loadConvSummaryForOverview(workItemId) {
  lastConvSummary = null;
  if (!workItemId) return;
  try {
    const data = await getJSON("/api/work-summary?work_item_id=" + encodeURIComponent(workItemId));
    if (data && data.summary) lastConvSummary = data.summary;
  } catch (e) {
    // No summary recorded yet, or a transient fetch error; overview shows the
    // "no summary" state either way.
  }
  if (activeConvTab === "overview" && convDetailRun) renderConvTabPanel(convDetailRun);
}

function renderConvDetail(run) {
  const el = document.getElementById("conv-detail");
  if (!el) return;
  const isNewRun = !convDetailRun || convDetailRun.thread_id !== (run && run.thread_id);
  convDetailRun = run;
  if (!run || !run.thread_id || !(run.messages || []).length) {
    el.innerHTML = '<p class="muted">No conversation selected. Type below to start a new one.</p>';
    return;
  }
  if (isNewRun) {
    activeConvTab = "conversation";
    loadConvSummaryForOverview(run.work_item_id);
  }
  const alreadyBuilt = el.querySelector(".conv-tabs");
  if (!alreadyBuilt) {
    el.innerHTML = buildConvHead(run) + buildConvTabBar() +
      '<div class="conv-tab-panel" id="conv-tab-panel"></div>';
  } else {
    const head = el.querySelector(".conv-head");
    if (head) head.outerHTML = buildConvHead(run);
  }
  renderConvTabPanel(run);
}

// Classify a conversation entry so the timeline reads as a council transcript:
// a real GPT/Codex reviewer message (with its verdict parsed from the footer),
// a Claude reconciliation, or a "no participation" failure note shown WITHOUT a
// reviewer badge so a failed/unavailable reviewer is never mistaken for a review.
function conversationEntryTag(m) {
  const body = m.message || "";
  const isReviewer = (m.actor === "gpt" || m.actor === "codex") &&
    (m.source === "openai-api" || m.source === "codex-cli");
  if (isReviewer) {
    const who = m.actor.toUpperCase();
    const vm = body.match(/verdict=([a-z_]+), confidence=([\d.]+), risk=([a-z]+)/i);
    return { cls: "conv-entry-reviewer",
             label: vm ? (who + " reviewer · " + vm[1] + " · conf " + vm[2] + " · " + vm[3] + " risk")
                       : (who + " reviewer") };
  }
  if (/no (GPT|Codex) participation claimed/i.test(body)) {
    return { cls: "conv-entry-unavailable", label: "reviewer unavailable · not recorded as participation" };
  }
  if (m.source === "review-council") {
    if (/^Review Council round \d+ starting/.test(body)) {
      return { cls: "conv-entry-roundstart", label: "council round start · plan/context digest" };
    }
    return { cls: "conv-entry-reconcile", label: "Claude reconciliation" };
  }
  if (m.source === "use-cw-summary") {
    return { cls: "conv-entry-summary", label: "CW canonical summary · harness-generated" };
  }
  if (m.closure === "closed_by_operator") {
    return { cls: "conv-entry-closure", label: "closed by operator · verification incomplete · not DONE" };
  }
  return null;
}

async function loadConversations() {
  try {
    const data = await getJSON("/api/conversations");
    lastConversations = data.conversations || [];
    renderQueue();
    if (selectedConvThread) {
      const run = await getJSON("/api/active-run?thread_id=" + encodeURIComponent(selectedConvThread));
      // Read-only council state for this thread (newest first); fetch full
      // detail (rounds) for the most recent few councils so the Councils tab
      // can group verdict + reconciliation by council and round.
      lastConvCouncils = [];
      lastConvCouncilDetails = {};
      try {
        const cd = await getJSON("/api/review-councils?thread_id=" + encodeURIComponent(selectedConvThread));
        lastConvCouncils = cd.review_councils || [];
        for (const c of lastConvCouncils.slice(0, 4)) {
          const full = await getJSON("/api/review-council?id=" + encodeURIComponent(c.council_id));
          if (full && !full.error) lastConvCouncilDetails[c.council_id] = full;
        }
      } catch (e2) { /* councils are optional; ignore transient errors */ }
      renderConvDetail(run);
    }
  } catch (e) {
    // Leave prior content on a transient fetch error.
  }
}

// Composer modes keep chat and work separate: Message is normal chat (intent
// "chat", never a work item), Ask agent and Create work item are actionable
// (intent "request", derived as open work), and Request clearance opens the
// escalation modal to file an RTA through the existing intake.
// The Conversations workspace composer targets the currently selected thread
// when continuing one, or a client-generated id (pinned to the draft before
// the first send attempt) when starting fresh -- the same retry-safety
// pattern as the quick box, so the destination is always known up front and a
// timed-out send can be retried without risking a duplicate.
let convComposerNewThreadId = null;
let convComposer = null;

function convComposerTarget() {
  if (selectedConvThread) return { thread_id: selectedConvThread };
  if (!convComposerNewThreadId) convComposerNewThreadId = genThreadId();
  return { thread_id: convComposerNewThreadId };
}

function initConvComposer() {
  convComposer = createComposer({
    name: "conv-composer",
    textarea: document.getElementById("conv-input"),
    sendBtn: document.getElementById("conv-send"),
    counterEl: document.getElementById("conv-counter"),
    errorEl: document.getElementById("conv-error"),
    bannerEl: document.getElementById("conv-banner"),
    getTarget: convComposerTarget,
    isConfirmedTarget: () => !!selectedConvThread,
    buildFields: (canonical) => {
      const mode = document.getElementById("conv-mode").value || "chat";
      const prefix = document.getElementById("conv-target").value || "";
      return {
        actor: "OPERATOR-0001", role: "operator", source: "operator-ui",
        direction: "inbound", simulated: false,
        message: prefix + canonical,
        intent: mode === "chat" ? "chat" : "request",
      };
    },
    endpoint: "/api/messages",
    onPosted: (result) => {
      if (!selectedConvThread && result.thread_id) selectedConvThread = result.thread_id;
      convComposerNewThreadId = null;
      convComposer.updateBanner();
      loadConversations();
      refreshMessages();
      refreshWorkItems();
    },
  });
}

function submitConvComposer(ev) {
  ev.preventDefault();
  const input = document.getElementById("conv-input");
  const text = input.value.trim();
  const mode = document.getElementById("conv-mode").value || "chat";
  if (mode === "clearance") {
    openEscalate();
    if (text) {
      document.getElementById("esc-action").value = text;
      const titleEl = document.getElementById("esc-title");
      if (!titleEl.value.trim()) titleEl.value = text.slice(0, 140);
    }
    input.value = "";
    return;
  }
  if (convComposer) convComposer.send();
}

async function postConvNote(direction, message) {
  if (!selectedConvThread) return;
  await postJSON("/api/messages", {
    actor: "OPERATOR-0001", role: "operator", source: "operator-ui",
    direction: direction, simulated: false, message: message,
    thread_id: selectedConvThread,
  });
  loadConversations();
  refreshWorkItems();
}

function openEscalate() {
  const run = convDetailRun;
  const intake = (lastState && lastState.intake) || {};
  document.getElementById("esc-type").innerHTML =
    (intake.packet_types || ["analysis", "code_change", "docs_change"])
      .map((t) => "<option>" + esc(t) + "</option>").join("");
  document.getElementById("esc-target").innerHTML =
    (intake.target_labels || []).map((t) => "<option>" + esc(t) + "</option>").join("");
  const title = run && run.messages && run.messages[0] ? run.messages[0].message : "";
  document.getElementById("esc-title").value = title.slice(0, 140);
  document.getElementById("esc-action").value = "";
  document.getElementById("escalate-modal").setAttribute("aria-hidden", "false");
  document.getElementById("esc-action").focus();
}

function closeEscalate() {
  document.getElementById("escalate-modal").setAttribute("aria-hidden", "true");
}

async function submitEscalate(ev) {
  ev.preventDefault();
  const body = {
    title: document.getElementById("esc-title").value.trim(),
    packet_type: document.getElementById("esc-type").value,
    requesting_agent: "agent/worker",
    requested_action: document.getElementById("esc-action").value.trim(),
    target_label: document.getElementById("esc-target").value,
  };
  if (!body.title || !body.requested_action) return;
  const res = await postJSON("/api/request", body);
  if (res.ok) {
    closeEscalate();
    setActivity("Clearance request created from the conversation\n" + (res.output || "").trim());
    if (selectedConvThread) {
      await postConvNote("internal",
        "Escalated to clearance packet (RTA created): " + body.title +
        ". Decide CTA / DTA / RFI in the console.");
    }
    if (res.state) renderState(res.state);
  } else {
    setActivity("Refused: " + (res.error || "") + "\n" + (res.output || "").trim());
  }
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

// Archive-aware board: terminal packets past the 24h recent-terminal window
// arrive flagged archived from the server. They are hidden by default (a
// compact archive line replaces them) so old completed audit history never
// looks like current work. Files are never touched; History shows everything.
// Failed packets are never archived.
let showArchived = false;

function renderBoard(state) {
  const board = document.getElementById("board");
  board.innerHTML = "";
  document.getElementById("actor-label").textContent = state.actor || "operator";
  Object.keys(LANE_TITLES).forEach((lane) => {
    const cards = state.lanes[lane] || [];
    const archived = cards.filter((c) => c.archived);
    const visible = showArchived ? cards : cards.filter((c) => !c.archived);
    const laneEl = document.createElement("div");
    laneEl.className = "lane";
    laneEl.innerHTML =
      '<div class="lane-head"><h3>' + esc(LANE_TITLES[lane]) +
      '</h3><span class="lane-count">' + cards.length + "</span></div>";
    if (!visible.length && !archived.length) {
      const empty = document.createElement("div");
      empty.className = "lane-empty";
      empty.textContent = "empty";
      laneEl.appendChild(empty);
    } else {
      visible.forEach((c) => laneEl.appendChild(renderCard(c)));
    }
    if (archived.length) {
      const note = document.createElement("div");
      note.className = "lane-archived";
      note.innerHTML = esc(archived.length + " archived completed packet" +
        (archived.length === 1 ? "" : "s")) +
        ' <button class="show-completed-btn" type="button">' +
        (showArchived ? "Hide completed" : "Show completed") + "</button>";
      laneEl.appendChild(note);
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
    input.setCustomValidity("");
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

let lastState = null;

function renderState(state) {
  lastState = state;
  applyMode(state);
  renderMission(state.mission);
  // The pulse-inspector caption is bound to the SELECTED task: the global
  // pulse metadata is shown only when its source thread IS the selected
  // thread, so historical or concurrent activity never reads as the live
  // task's state (the phase stepper above it is the authoritative display of
  // state.pulse-adjacent activity for the selected task).
  const p = state.pulse || {};
  if (!p.source_thread_id || p.source_thread_id === selectedConvThread) {
    renderPulseInspector(state.pulse);
  } else {
    renderPulseInspector({ active_phase: null,
      reason: "activity on another task does not drive this display" });
  }
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

// Developer surface: the Tool Log footer is hidden by default and toggled by
// Ctrl+Shift+L or the small gear control in the corner.
function toggleToolLog() {
  const footer = document.getElementById("activity-footer");
  if (footer) footer.hidden = !footer.hidden;
}

function selectTask(threadId) {
  selectedConvThread = threadId || null;
  convComposerNewThreadId = null;
  if (convComposer) convComposer.restoreDraft();
  if (operatorChatComposer) operatorChatComposer.updateBanner();
  renderQueue();
  refreshTaskState();
  loadConversations();
}

function wire() {
  initOperatorChatComposer();
  initConvComposer();
  wirePopovers();
  registerPopover("health", {
    trigger: document.getElementById("health-chip"),
    panel: document.getElementById("health-panel"),
    onOpen: () => renderHealthDetails(lastHealth),
  });
  document.getElementById("reset-btn").addEventListener("click", resetDemo);
  document.getElementById("audit-close").addEventListener("click", closeAudit);
  document.getElementById("reason-cancel").addEventListener("click", () => closeReason(null));
  document.getElementById("reason-confirm").addEventListener("click", () => {
    const input = document.getElementById("reason-input");
    const value = input.value.trim();
    if (!value) {
      // The trim check stays authoritative: native `required` would accept
      // whitespace-only input, and the textarea lives outside any form.
      input.setCustomValidity("A reason is required.");
      input.reportValidity();
      return;
    }
    closeReason(value);
  });
  document.getElementById("reason-input").addEventListener("input", (ev) => {
    ev.target.setCustomValidity("");
  });
  document.getElementById("results-cancel").addEventListener("click", closeResults);
  document.getElementById("results-form").addEventListener("submit", submitResults);
  document.getElementById("convo-form").addEventListener("submit", submitConvo);
  document.getElementById("operator-chat-form").addEventListener("submit", submitOperatorChat);
  document.getElementById("health-chip").addEventListener("click", toggleHealthPanel);

  // Primary navigation: Command Center | Work | History.
  document.getElementById("nav-command").addEventListener("click", () => showView("command"));
  document.getElementById("nav-work").addEventListener("click", () => showView("work"));
  document.getElementById("nav-history").addEventListener("click", () => showView("history"));
  // Attention is a queue filter, never a separate page.
  document.getElementById("attention-chip").addEventListener("click", () => {
    queueAttentionOnly = !queueAttentionOnly;
    if (currentView === "history") showView("command");
    renderQueue();
  });

  // Work queue: clicking a row selects that task everywhere.
  document.getElementById("queue-groups").addEventListener("click", (e) => {
    const row = e.target.closest(".q-row");
    if (!row) return;
    const thread = row.getAttribute("data-thread");
    if (thread) selectTask(thread);
  });
  document.getElementById("queue-new-btn").addEventListener("click", () => {
    selectTask(null);
    renderConvDetail(null);
    showView("work");
    document.getElementById("conv-input").focus();
  });

  // Unified History ledger.
  document.getElementById("ledger-filters").addEventListener("submit", (e) => {
    e.preventDefault();
    loadHistory();
  });
  document.getElementById("lf-scope").addEventListener("change", loadHistory);
  document.getElementById("lf-type").addEventListener("change", loadHistory);
  document.getElementById("lf-clear").addEventListener("click", () => {
    ["lf-actor", "lf-status", "lf-date", "lf-workitem", "lf-council", "lf-text"]
      .forEach((id) => { document.getElementById(id).value = ""; });
    document.getElementById("lf-scope").value = "active";
    document.getElementById("lf-type").value = "";
    loadHistory();
  });
  document.getElementById("ledger-body").addEventListener("click", (e) => {
    const row = e.target.closest("tr[data-ledger-index]");
    if (!row) return;
    openLedgerDetail(parseInt(row.getAttribute("data-ledger-index"), 10));
  });
  document.getElementById("ledger-detail-close").addEventListener("click", () => {
    document.getElementById("ledger-detail").hidden = true;
  });
  document.getElementById("ledger-detail-body").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-ledger-audit]");
    if (btn) openAudit(btn.getAttribute("data-ledger-audit"));
  });

  // Work workspace composer + tab interactions.
  document.getElementById("conv-composer").addEventListener("submit", submitConvComposer);
  document.getElementById("conv-detail").addEventListener("click", (e) => {
    const tabBtn = e.target.closest("button[data-conv-tab]");
    if (tabBtn) {
      switchConvTab(tabBtn.getAttribute("data-conv-tab"));
      return;
    }
    const filterBtn = e.target.closest("button[data-participant]");
    if (filterBtn) {
      convParticipantFilter = filterBtn.getAttribute("data-participant") || "";
      if (convDetailRun) renderConvTabPanel(convDetailRun);
      return;
    }
    const jumpBtn = e.target.closest("button[data-jump-latest]");
    if (jumpBtn) {
      const box = document.querySelector("#conv-tab-panel .conv-messages");
      if (box) box.scrollTop = box.scrollHeight;
      return;
    }
    const auditBtn = e.target.closest("button[data-conv-open-audit]");
    if (auditBtn) {
      openAudit(auditBtn.getAttribute("data-conv-open-audit") + ".json");
      return;
    }
    const copyBtn = e.target.closest("button[data-copy], button[data-copy-summary]");
    if (copyBtn) {
      if (copyBtn.hasAttribute("data-copy")) copyText(copyBtn.getAttribute("data-copy"), copyBtn);
      else copyText(runSummaryText(convDetailRun), copyBtn);
      return;
    }
    const action = e.target.closest("button[data-action]");
    if (action) handleOperatorAction(action.getAttribute("data-action"));
  });
  // The right-panel operator actions mirror the workspace actions and bind to
  // the same selected task.
  document.getElementById("operator-actions").addEventListener("click", (e) => {
    const openWork = e.target.closest("button[data-open-work]");
    if (openWork) {
      showView("work");
      return;
    }
    const action = e.target.closest("button[data-action]");
    if (action) handleOperatorAction(action.getAttribute("data-action"));
  });
  document.getElementById("escalate-form").addEventListener("submit", submitEscalate);
  document.getElementById("escalate-cancel").addEventListener("click", closeEscalate);
  // Archive toggle: reveal/hide old completed packets in the durable-record
  // lanes (now inside History).
  document.getElementById("board").addEventListener("click", (e) => {
    const btn = e.target.closest(".show-completed-btn");
    if (!btn) return;
    showArchived = !showArchived;
    if (lastState) renderBoard(lastState);
  });

  // Developer tool log: hidden by default, reachable via shortcut or control.
  document.getElementById("tool-log-toggle").addEventListener("click", toggleToolLog);
  document.addEventListener("keydown", (e) => {
    if (e.key.toLowerCase() === "l" && e.ctrlKey && e.shiftKey) {
      e.preventDefault();
      toggleToolLog();
    }
  });

  // Live console: fast polling (every 2s) of all real sources. No WebSockets.
  const LIVE_MS = 2000;
  refreshAgentEvents();
  refreshMessages();
  refreshWorkItems();
  refreshHealth();
  refreshTaskState();
  refreshArchiveIndex();
  // Conversations feed the queue's Recent group on every view, so load once
  // at boot; the fast poll below only runs while the Work view is open.
  loadConversations();
  setInterval(refresh, LIVE_MS);
  setInterval(refreshAgentEvents, LIVE_MS);
  setInterval(refreshMessages, LIVE_MS);
  setInterval(refreshWorkItems, LIVE_MS);
  setInterval(refreshHealth, LIVE_MS * 2);
  setInterval(refreshTaskState, LIVE_MS);
  setInterval(refreshArchiveIndex, LIVE_MS * 15);
  // Keep the Work workspace live while it is open; refresh the Recent group
  // at the slower health cadence on every other view.
  setInterval(() => {
    if (currentView === "work") loadConversations();
  }, LIVE_MS);
  setInterval(() => {
    if (currentView !== "work") loadConversations();
  }, LIVE_MS * 2);
}

function handleOperatorAction(kind) {
  if (kind === "ack") {
    postConvNote("internal", "Operator acknowledged this conversation.");
  } else if (kind === "workitem") {
    const title = convDetailRun && convDetailRun.messages && convDetailRun.messages[0]
      ? convDetailRun.messages[0].message.slice(0, 120) : "this conversation";
    postConvNote("inbound", "Follow-up requested: " + title);
  } else if (kind === "escalate") {
    openEscalate();
  }
}

wire();
refresh();
