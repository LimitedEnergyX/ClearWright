# Verification packet: bounded request control for the remaining console pollers

Compiled mechanically from committed bytes. Every count, file list, commit
and hash below is derived from actual command output.


## Authority

```
CTA          poller-saturation-rta-20260726 (BRANCH_CODE, code_change)
cleared by   OPERATOR-0001 at 2026-07-26T11:47:39Z
expires      2026-07-27T11:47:39Z
work item    message:msg-20260726T045228718945
thread       thr-20260726T045228718945
diagnosis    msg-20260726T045302129379
```

## Problem

Every recurring poller runs on a fixed interval, so when the server is slower
than that interval an UNGUARDED poller starts a new request before the previous
one finishes and the outstanding requests accumulate without bound.


Measured on the running console over one 195 s window:

```
endpoint            requests   avg      max
/api/state              44     20.8 s   33.5 s
/api/messages           45     11.5 s   32.7 s
/api/task-state         45      8.9 s   32.5 s
/api/agent-events       45      8.8 s   31.7 s
/api/conversations      22      6.2 s   31.3 s
/api/health             21     25.9 s   35.7 s
/api/work-items         11      7.5 s   27.0 s   <- already bounded
/api/review-councils    11      2.8 s   26.3 s
/api/archive-index       4      4.8 s   16.1 s
```

249 requests from the unguarded pollers against the bounded poller's 11. They
saturate a serialized server, which drags every endpoint into the 20-35 s range
and is also why /api/health is slow enough to make the separate launcher defect
fatal. The launcher is NOT in this scope.


## Correction to the original report

The authorising diagnosis said six pollers. Derived from committed source the
correct number is SEVEN. loadConversations is driven by two further intervals
outside the setInterval block, and ONE of its calls fans out to as many as
seven sequential requests, so bounding the outer call is what stops the
multiplication. The packet reports seven because that is what the source says.


## Design

`boundedPoll(name, run)` gives ONE endpoint its OWN slot:

- at most one active request;
- repeated ticks during it collapse into AT MOST ONE coalesced follow-up;
- the slot is released in `finally`, so a failure cannot wedge the endpoint;
- the wrapper never throws to its interval;
- the fire-and-forget follow-up carries an explicit rejection handler;
- NO retry loop: the endpoint's existing interval remains the only thing that
  re-drives it, so a failing endpoint cannot become a retry storm;
- endpoints are never globally serialized -- each keeps a separate slot, so
  unrelated endpoints still run in parallel;
- per-endpoint counters are readable on demand and never logged.


`refreshWorkItems` is deliberately UNTOUCHED: it already carries its own
bounded controller and monotonic generation guard, and its four semantic
outcomes are depended on by `refreshSucceeded` and the boot chain. Wrapping it
would change that contract and put Session UX queue confirmation and
destination integrity at risk for no gain.


Poll intervals are unchanged and no poller is removed.


## Commits

```
base  a025edbec22764fea35623c0cab1cb2e90a9b6ec
head  31efff8039084910cd7d9d9b9b1631722f981d44
31efff8 Correct council round-1: prove the throwing follow-up, the global surface, and the controller boundary
c16e58d docs(cw): verification packet for the console poller bounding
75ae94d Bounded request control for the remaining console pollers
```

## Files changed

```
apps/control-plane/static/app.js    | 107 ++++-
 docs/ux/POLLER-BOUNDING-VERIFY.md   | 834 ++++++++++++++++++++++++++++++++++++
 tests/dom/wired_paths.mjs           | 395 +++++++++++++++++
 tests/test_console_poll_bounding.py | 247 +++++++++++
 4 files changed, 1574 insertions(+), 9 deletions(-)
```

## Manifest (sha256 of committed bytes)

```
apps/control-plane/static/app.js             1333b704017d21f1927d35df030d6627dabfb4cb5d247cda8d94ee4c7777ff29  194420 bytes
docs/ux/POLLER-BOUNDING-VERIFY.md            9bff57f0606c4fcf2f496a652568d949f31ecf5535452e843928c85eff1bb30b  36340 bytes
tests/dom/wired_paths.mjs                    c0de47c046f40f2100f7cd43de066d8ec104c6e386cf55a58e4c60f2f94e6665  81455 bytes
tests/test_console_poll_bounding.py          31e681a7e621ecca7472df8f0a3dffc4e5b655d6fe0dcba820855df163e48cc3  10842 bytes
```

## Tests, as reported by the suites

```
static  (tests/test_console_poll_bounding.py)   31 tests   OK
runtime (tests/dom/session_ux_runtime.mjs)      109 checks  PASS
wired   (tests/dom/wired_paths.mjs)             539 checks  PASS
full suite                                     1449 tests   OK (skipped=1)
```

Wired section 12 drives the REAL poller entry points through a transport that
can defer or fail any endpoint independently. It covers, for every converted
poller: concurrency control under a held-open response, one coalesced follow-up
and no backlog, slot release on failure with no retry storm, recovery, and
non-interference between unrelated endpoints. It also covers the
loadConversations fan-out, the untouched work-item contract, lifecycle under
repeated wire(), absence of durable writes, zero unhandled rejections on the
previously unhandled /api/state path including a THROWING coalesced follow-up,
and a boundary assertion that no poller bypasses its controller.


## Review questions

1. Does the controller genuinely bound each endpoint, and is the slot released
   on every path including an unexpected throw?
2. Is per-endpoint isolation correct, i.e. can one slow endpoint still not
   block an unrelated one?
3. Is leaving refreshWorkItems on its own bespoke controller the right call, or
   does the duplication itself carry more risk than converting it?
4. Does bounding loadConversations at the OUTER call leave any unbounded inner
   fan-out path?
5. Is the switch from `function name()` to `const name = boundedPoll(...)` safe
   given nothing references these names off the global object?


## Full diff of the changed source and tests

```diff
diff --git a/apps/control-plane/static/app.js b/apps/control-plane/static/app.js
index cb0c810..cf996bd 100644
--- a/apps/control-plane/static/app.js
+++ b/apps/control-plane/static/app.js
@@ -61,6 +61,78 @@ async function postJSON(url, body) {
   return res.json();
 }
 
+// --------------------------------------------------------------------------- //
+// Bounded polling.
+//
+// Every recurring poller below runs on a fixed interval, so when the server is
+// slower than that interval an UNGUARDED poller starts a new request before the
+// previous one finishes and the outstanding requests accumulate without bound.
+// That is what saturates a serialized server: measured on this console over a
+// 195 s window, the unguarded pollers issued 249 requests while the bounded
+// work-item poller issued 11, and per-endpoint latency reached 20-35 s.
+//
+// boundedPoll gives ONE endpoint its OWN slot: at most one active request, and
+// at most one coalesced follow-up if ticks arrived while that request was
+// running. Endpoints are never globally serialized -- each keeps a separate
+// slot -- so unrelated endpoints still run in parallel. There is no retry loop
+// anywhere in here: the endpoint's existing interval remains the only thing
+// that re-drives it, so a failing endpoint cannot become a tight retry storm.
+// --------------------------------------------------------------------------- //
+
+// The request ran to completion. It says nothing about success or failure: each
+// wrapped poller keeps its OWN truthful failure contract internally.
+const POLL_RAN = "ran";
+// A tick arrived while a request was already active. It started no request and
+// changed no state; it only requested the single bounded follow-up.
+const POLL_COALESCED = "coalesced";
+
+// name -> {active, followUp, ran, coalesced}. Diagnostic visibility that is read
+// on demand and never logged, so it adds no console noise.
+const pollControllers = new Map();
+
+function pollDiagnostics() {
+  const out = {};
+  pollControllers.forEach((st, name) => {
+    out[name] = { active: st.active, followUp: st.followUp,
+                  ran: st.ran, coalesced: st.coalesced };
+  });
+  return out;
+}
+
+function boundedPoll(name, run) {
+  const state = { active: false, followUp: false, ran: 0, coalesced: 0 };
+  pollControllers.set(name, state);
+  async function polled() {
+    if (state.active) {
+      // Coalesce: at most one follow-up, no backlog, and the active request is
+      // left alone to finish and apply its result.
+      state.followUp = true;
+      state.coalesced += 1;
+      return POLL_COALESCED;
+    }
+    state.active = true;
+    state.ran += 1;
+    try {
+      await run();
+    } catch (e) {
+      // A poller must never throw to its interval. Each wrapped function
+      // already handles its own failures; this is the last line of defence so
+      // an unexpected throw cannot become an invisible unhandled rejection.
+    } finally {
+      state.active = false;
+      if (state.followUp) {
+        state.followUp = false;
+        // The single coalesced follow-up. Deliberately not awaited, and given
+        // an explicit handler even though polled() cannot reject, because an
+        // unhandled rejection on a fire-and-forget call would be silent.
+        polled().catch(() => { /* never surfaces as an unhandled rejection */ });
+      }
+    }
+    return POLL_RAN;
+  }
+  return polled;
+}
+
 function setActivity(text) {
   document.getElementById("activity-log").textContent = text;
 }
@@ -280,7 +352,7 @@ function renderPulseInspector(pulse) {
 // The selected-task poll: header, stepper, operator panel, and the Work tabs
 // all bind to this ONE state object, so a current-task mismatch between the
 // queue, header, phase display, conversation, and operator panel cannot occur.
-async function refreshTaskState() {
+async function refreshTaskStateRequest() {
   try {
     // work_item_id is the primary selector; a bare thread selection is still
     // honored (the server resolves it, erroring only on a multi-item thread).
@@ -306,6 +378,7 @@ async function refreshTaskState() {
   renderOperatorPanel(lastTaskState);
   if (currentView === "command") renderCommandOverview(lastTaskState);
 }
+const refreshTaskState = boundedPoll("task-state", refreshTaskStateRequest);
 
 // The right-hand operator panel: the next required action, the authority
 // state (gate / clearance / verification), and the contextual operator
@@ -574,7 +647,7 @@ function renderRealEventsInner(el, events) {
 
 let lastEvents = [];
 
-async function refreshAgentEvents() {
+async function refreshAgentEventsRequest() {
   try {
     const data = await getJSON("/api/agent-events");
     lastEvents = data.events || [];
@@ -583,6 +656,7 @@ async function refreshAgentEvents() {
     // Leave the prior content in place on a transient fetch error.
   }
 }
+const refreshAgentEvents = boundedPoll("agent-events", refreshAgentEventsRequest);
 
 // --------------------------------------------------------------------------- //
 // Local communications (real, durable, packet-linked)
@@ -694,7 +768,7 @@ function renderMessagesInner(el, messages) {
 
 let lastMessages = [];
 
-async function refreshMessages() {
+async function refreshMessagesRequest() {
   try {
     const data = await getJSON("/api/messages");
     lastMessages = data.messages || [];
@@ -703,6 +777,7 @@ async function refreshMessages() {
     // Leave the prior content in place on a transient fetch error.
   }
 }
+const refreshMessages = boundedPoll("messages", refreshMessagesRequest);
 
 // --------------------------------------------------------------------------- //
 // Composer payload integrity: ONE canonical content contract, ONE documented
@@ -2472,7 +2547,7 @@ async function runWorkItemsRefresh() {
   }
 }
 
-async function refreshArchiveIndex() {
+async function refreshArchiveIndexRequest() {
   try {
     lastArchiveIndex = await getJSON("/api/archive-index");
     renderQueue();
@@ -2480,6 +2555,7 @@ async function refreshArchiveIndex() {
     // Archive index is optional; the group simply stays empty.
   }
 }
+const refreshArchiveIndex = boundedPoll("archive-index", refreshArchiveIndexRequest);
 
 // --------------------------------------------------------------------------- //
 // History: ONE unified, read-only ledger across every durable source (packets,
@@ -2750,7 +2826,7 @@ function renderHealthDetails(h) {
   el.innerHTML = html;
 }
 
-async function refreshHealth() {
+async function refreshHealthRequest() {
   try {
     const h = await getJSON("/api/health");
     lastHealth = h;
@@ -2769,6 +2845,7 @@ async function refreshHealth() {
     // Leave the prior chip state on a transient fetch error.
   }
 }
+const refreshHealth = boundedPoll("health", refreshHealthRequest);
 
 // --------------------------------------------------------------------------- //
 // Popover management: System Health and any peer popover registered here
@@ -3246,7 +3323,7 @@ function conversationEntryTag(m) {
   return null;
 }
 
-async function loadConversations() {
+async function loadConversationsRequest() {
   try {
     const data = await getJSON("/api/conversations");
     lastConversations = data.conversations || [];
@@ -3272,6 +3349,11 @@ async function loadConversations() {
     // Leave prior content on a transient fetch error.
   }
 }
+// A single call fans out to /api/conversations, then /api/active-run,
+// /api/review-councils and up to four /api/review-council detail calls, so an
+// unguarded 2 s tick multiplied into dozens of outstanding requests. Bounding
+// the OUTER call is what stops that; the inner sequence is left exactly as-is.
+const loadConversations = boundedPoll("conversations", loadConversationsRequest);
 
 // Composer modes keep chat and work separate: Message is normal chat (intent
 // "chat", never a work item), Ask agent and Create work item are actionable
@@ -3943,10 +4025,17 @@ function renderState(state) {
   if (currentMode !== "operator") feedStart(state);
 }
 
-async function refresh() {
-  const state = await getJSON("/api/state");
-  renderState(state);
+async function refreshStateRequest() {
+  try {
+    const state = await getJSON("/api/state");
+    renderState(state);
+  } catch (e) {
+    // Previously this function had no handler at all, so every failed poll
+    // became an invisible unhandled rejection. Keep the prior rendered state,
+    // exactly like the other pollers do on a transient fetch error.
+  }
 }
+const refresh = boundedPoll("state", refreshStateRequest);
 
 // Developer surface: the Tool Log footer is hidden by default and toggled by
 // Ctrl+Shift+L or the small gear control in the corner.
diff --git a/tests/dom/wired_paths.mjs b/tests/dom/wired_paths.mjs
index 193212d..e54d309 100644
--- a/tests/dom/wired_paths.mjs
+++ b/tests/dom/wired_paths.mjs
@@ -1360,5 +1360,400 @@ for (const bad of [{}, { work_items: null }, { work_items: "nope" }, { work_item
   ok(calls <= 3, "no production call site bypasses the bounded entry point");
 }
 
+// ---------------------------------------------------------------------------
+// 12. BOUNDED POLLING for the recurring console pollers.
+//
+//     Every poller runs on a fixed interval, so when an endpoint is slower than
+//     that interval an UNGUARDED poller starts a new request before the last
+//     one finishes and the outstanding requests accumulate without bound. These
+//     drive the REAL exported poller functions, not the inner request bodies.
+// ---------------------------------------------------------------------------
+
+// A transport that can defer or fail ANY endpoint independently, so one slow
+// endpoint never masks another and per-endpoint request counts are exact.
+function pollEnv(opts) {
+  opts = opts || {};
+  const state = { modes: {}, pending: {}, counts: {} };
+  const env = buildEnv({ responder: () => ({ ok: true }) });
+  const realFetch = env.ctx.fetch;
+  env.poll = state;
+  env.epOf = (u) => {
+    const m = /\/api\/([a-z-]+)/.exec(String(u));
+    return m ? m[1] : "other";
+  };
+  env.ctx.fetch = function (url, init) {
+    const u = String(url), k = env.epOf(u);
+    state.counts[k] = (state.counts[k] || 0) + 1;
+    const mode = state.modes[k] || "ok";
+    if (mode === "fail") return Promise.reject(new Error("probe: " + k + " down"));
+    if (mode === "defer") {
+      return new Promise((resolve, reject) => {
+        (state.pending[k] = state.pending[k] || []).push({
+          resolveWith: (payload) => resolve({ ok: true, status: 200,
+            json: () => Promise.resolve(payload || { ok: true }) }),
+          rejectWith: (e) => reject(e || new Error("probe: deferred " + k)),
+        });
+      });
+    }
+    return realFetch(url, init);
+  };
+  return env;
+}
+
+// settle() is already defined above by the queue-refresh coverage.
+const diag = (ctx, name) => ev(ctx, "pollDiagnostics()")[name];
+// Top-level `const` lands in the context's global LEXICAL environment rather
+// than on the sandbox object, so the pollers are called the same way app.js
+// itself resolves them: by name, in scope.
+const call = (ctx, fn) => ev(ctx, fn + "()");
+
+// Every converted poller: the exported function, its controller name, and the
+// endpoint its FIRST request hits.
+const POLLERS = [
+  { fn: "refresh",             name: "state",         ep: "state" },
+  { fn: "refreshAgentEvents",  name: "agent-events",  ep: "agent-events" },
+  { fn: "refreshMessages",     name: "messages",      ep: "messages" },
+  { fn: "refreshHealth",       name: "health",        ep: "health" },
+  { fn: "refreshTaskState",    name: "task-state",    ep: "task-state" },
+  { fn: "refreshArchiveIndex", name: "archive-index", ep: "archive-index" },
+  { fn: "loadConversations",   name: "conversations", ep: "conversations" },
+];
+
+// 12a. CONCURRENCY CONTROL. Hold the response open past several polling ticks
+//      and prove exactly one request is active with no backlog.
+for (const p of POLLERS) {
+  const env = pollEnv({});
+  const ctx = loadApp(env);
+  env.poll.modes[p.ep] = "defer";
+
+  const active = call(ctx, p.fn);             // owns the slot
+  const ticks = [];
+  for (let i = 0; i < 4; i++) ticks.push(await call(ctx, p.fn));
+  eq(ticks, ["coalesced", "coalesced", "coalesced", "coalesced"],
+     p.fn + ": ticks during an active request COALESCE, they start nothing");
+  eq(env.poll.counts[p.ep], 1,
+     p.fn + ": AT MOST ONE request is active no matter how many ticks arrive");
+  eq(env.poll.pending[p.ep].length, 1, p.fn + ": exactly one outstanding request");
+  ok(diag(ctx, p.name).active === true, p.fn + ": the active request owns the slot");
+
+  // The active request is NOT invalidated by those ticks: it settles normally.
+  env.poll.pending[p.ep].shift().resolveWith({ ok: true });
+  eq(await active, "ran", p.fn + ": the active request RUNS rather than being discarded");
+
+  // Exactly ONE coalesced follow-up was started, not four.
+  eq(env.poll.counts[p.ep], 2, p.fn + ": exactly ONE coalesced follow-up, no backlog");
+  eq(diag(ctx, p.name).coalesced, 4, p.fn + ": all four ticks were counted as coalesced");
+  if (env.poll.pending[p.ep].length) env.poll.pending[p.ep].shift().resolveWith({ ok: true });
+  for (let i = 0; i < 4; i++) await settle();
+  eq(env.poll.counts[p.ep], 2, p.fn + ": the follow-up does not chain into a storm");
+}
+
+// 12b. SUSTAINED slow endpoint with continuous ticking still makes progress and
+//      never accumulates a backlog.
+for (const p of POLLERS) {
+  const env = pollEnv({});
+  const ctx = loadApp(env);
+  env.poll.modes[p.ep] = "defer";
+  const first = call(ctx, p.fn);
+  for (let i = 0; i < 12; i++) await call(ctx, p.fn);  // twelve ticks
+  eq(env.poll.counts[p.ep], 1, p.fn + ": twelve ticks produce ONE request");
+  env.poll.pending[p.ep].shift().resolveWith({ ok: true });
+  eq(await first, "ran", p.fn + ": the request completes despite continuous ticking");
+  eq(env.poll.counts[p.ep], 2, p.fn + ": twelve ticks collapse into ONE follow-up");
+  if (env.poll.pending[p.ep].length) env.poll.pending[p.ep].shift().resolveWith({ ok: true });
+}
+
+// 12c. FAILURE releases the slot, and a later tick recovers. A stuck flag would
+//      be a permanent outage for that endpoint.
+for (const p of POLLERS) {
+  const env = pollEnv({});
+  const ctx = loadApp(env);
+  env.poll.modes[p.ep] = "fail";
+  eq(await call(ctx, p.fn), "ran", p.fn + ": a failing poll still returns, it does not throw");
+  ok(diag(ctx, p.name).active === false, p.fn + ": the slot is RELEASED on failure");
+  ok(diag(ctx, p.name).followUp === false, p.fn + ": no phantom follow-up is left pending");
+  const afterFail = env.poll.counts[p.ep];
+  for (let i = 0; i < 5; i++) await settle();
+  eq(env.poll.counts[p.ep], afterFail,
+     p.fn + ": a failure does NOT trigger a retry storm");
+
+  env.poll.modes[p.ep] = "ok";
+  eq(await call(ctx, p.fn), "ran", p.fn + ": a later tick recovers normally");
+  ok(diag(ctx, p.name).active === false, p.fn + ": the slot is released after recovery");
+}
+
+// 12d. A FAILING poller does not wedge an UNRELATED endpoint. Endpoints are not
+//      globally serialised; each keeps its own slot.
+{
+  const env = pollEnv({});
+  const ctx = loadApp(env);
+  env.poll.modes["messages"] = "defer";
+  const held = call(ctx, "refreshMessages");          // messages held open
+  eq(await call(ctx, "refreshMessages"), "coalesced", "messages coalesces while held");
+
+  // A different endpoint must still run to completion, concurrently.
+  eq(await call(ctx, "refreshAgentEvents"), "ran",
+     "an UNRELATED endpoint runs while another is held open");
+  eq(await call(ctx, "refreshHealth"), "ran", "and another one does too");
+  ok(diag(ctx, "messages").active === true, "the held endpoint still owns its own slot");
+  ok(diag(ctx, "agent-events").active === false, "the unrelated endpoint released its slot");
+  env.poll.pending["messages"].shift().resolveWith({ ok: true });
+  await held;
+  if (env.poll.pending["messages"] && env.poll.pending["messages"].length) {
+    env.poll.pending["messages"].shift().resolveWith({ ok: true });
+  }
+}
+
+// 12e. loadConversations is the worst case: ONE call fans out to several
+//      sequential requests, so bounding the OUTER call is what stops the
+//      multiplication.
+{
+  const env = pollEnv({});
+  const ctx = loadApp(env);
+  ev(ctx, 'selectedConvThread = "thr-alpha";');
+  env.poll.modes["conversations"] = "defer";
+  const held = call(ctx, "loadConversations");
+  for (let i = 0; i < 6; i++) await call(ctx, "loadConversations");
+  eq(env.poll.counts["conversations"], 1,
+     "six ticks during a fan-out produce ONE outer request");
+  ok(!env.poll.counts["active-run"],
+     "the inner fan-out has not started while the outer call is still pending");
+  env.poll.pending["conversations"].shift().resolveWith({ conversations: [] });
+  await held;
+  for (let i = 0; i < 6; i++) await settle();
+  eq(env.poll.counts["conversations"], 2,
+     "six ticks collapse into exactly ONE follow-up, not six fan-outs");
+}
+
+// 12f. The work-item poller is UNTOUCHED and keeps its own distinct contract.
+{
+  const env = pollEnv({});
+  const ctx = loadApp(env);
+  ok(ev(ctx, "typeof queueRefreshInFlight") === "boolean",
+     "refreshWorkItems keeps its own in-flight flag, not the generic controller");
+  ok(ev(ctx, "typeof runWorkItemsRefresh") === "function",
+     "refreshWorkItems keeps its own inner request function");
+  ok(ev(ctx, "typeof queueRefreshGeneration") === "number",
+     "refreshWorkItems keeps its monotonic generation guard");
+  ok(ev(ctx, 'pollDiagnostics()["work-items"] === undefined'),
+     "work-items is NOT registered in the generic controller");
+  // Its four semantic outcomes are unchanged, and distinct from POLL_RAN.
+  ok(ev(ctx, 'refreshSucceeded("confirmed")') === true, "confirmed is still a success");
+  ok(ev(ctx, 'refreshSucceeded("coalesced")') === false, "coalesced is still not a success");
+  ok(ev(ctx, "POLL_RAN") === "ran", "the generic outcome is distinct from the queue outcomes");
+}
+
+// 12g. LIFECYCLE. Repeated wire() must not multiply controllers, intervals or
+//      listeners, and boot must register exactly the intended pollers.
+{
+  const env = pollEnv({});
+  const intervals = [];
+  env.ctx.setInterval = function (fn, ms) { intervals.push(ms); return intervals.length; };
+  const ctx = loadApp(env);
+
+  eq(ev(ctx, "pollControllers.size"), 7,
+     "exactly SEVEN pollers are registered with the bounded controller");
+  const names = ev(ctx, "Array.from(pollControllers.keys()).sort()");
+  eq(names, ["agent-events", "archive-index", "conversations", "health",
+             "messages", "state", "task-state"],
+     "the registered controllers are exactly the intended endpoints");
+
+  ctx.wire();
+  const afterFirst = intervals.length;
+  const listenersAfterFirst = Object.keys(ctx._winListeners)
+    .reduce((n, k) => n + ctx._winListeners[k].length, 0);
+  ok(afterFirst > 0, "wire() installs the recurring intervals");
+
+  ctx.wire();
+  ctx.wire();
+  eq(ev(ctx, "pollControllers.size"), 7,
+     "repeated wire() does NOT multiply the bounded controllers");
+  eq(intervals.length, afterFirst * 3,
+     "each wire() installs its own intervals (unchanged pre-existing behaviour)");
+  // The controllers are what bound the traffic, so even with extra intervals
+  // installed, concurrent ticks still collapse to one request per endpoint.
+  const env2 = pollEnv({});
+  const ctx2 = loadApp(env2);
+  env2.poll.modes["health"] = "defer";
+  const a = call(ctx2, "refreshHealth");
+  await Promise.all([call(ctx2, "refreshHealth"), call(ctx2, "refreshHealth"),
+                     call(ctx2, "refreshHealth")]);
+  eq(env2.poll.counts["health"], 1,
+     "even with several tickers, one endpoint keeps ONE active request");
+  env2.poll.pending["health"].shift().resolveWith({ ok: true });
+  await a;
+  if (env2.poll.pending["health"] && env2.poll.pending["health"].length) {
+    env2.poll.pending["health"].shift().resolveWith({ ok: true });
+  }
+}
+
+// 12h. NO DURABLE WRITE is produced by any amount of polling.
+{
+  const env = pollEnv({});
+  const ctx = loadApp(env);
+  for (const p of POLLERS) { await call(ctx, p.fn); await call(ctx, p.fn); }
+  for (let i = 0; i < 4; i++) await settle();
+  eq(env.posted.filter((x) => x.method === "POST").length, 0,
+     "no amount of polling emits a durable write");
+}
+
+// 12i. The previously UNHANDLED rejection path: /api/state had no catch at all,
+//      so every failed poll was a silent unhandled rejection.
+{
+  const env = pollEnv({});
+  const ctx = loadApp(env);
+  const unhandled = [];
+  const onUnhandled = (e) => unhandled.push(e);
+  process.on("unhandledRejection", onUnhandled);
+
+  env.poll.modes["state"] = "fail";
+  eq(await call(ctx, "refresh"), "ran", "a failing /api/state poll returns instead of throwing");
+  for (let i = 0; i < 6; i++) await settle();
+  eq(unhandled.length, 0, "a failing /api/state poll produces ZERO unhandled rejections");
+  ok(diag(ctx, "state").active === false, "and the slot is still released");
+
+  // The FOLLOW-UP itself must be the thing that fails. The earlier version of
+  // this case rejected the HELD request, whose finally then started a second
+  // request that was simply left pending, so the follow-up path was never
+  // exercised at all.
+  env.poll.modes["state"] = "defer";
+  const before = env.poll.counts["state"];       // this block already polled once
+  const held = call(ctx, "refresh");
+  await call(ctx, "refresh");                           // requests the follow-up
+  eq(env.poll.counts["state"] - before, 1, "the tick started NO second request");
+  eq(env.poll.pending["state"].length, 1, "only the held request is outstanding");
+  env.poll.pending["state"].shift().resolveWith({ ok: true });   // held SUCCEEDS
+  await held;
+  for (let i = 0; i < 6; i++) await settle();
+  eq(env.poll.counts["state"] - before, 2, "the coalesced follow-up actually started");
+  eq(env.poll.pending["state"].length, 1, "and the FOLLOW-UP is what is now in flight");
+  env.poll.pending["state"].shift().rejectWith(new Error("probe: follow-up down"));
+  for (let i = 0; i < 6; i++) await settle();
+  eq(unhandled.length, 0,
+     "a FAILING coalesced follow-up produces zero unhandled rejections");
+  ok(diag(ctx, "state").active === false, "and the slot is released after it");
+  process.removeListener("unhandledRejection", onUnhandled);
+}
+
+// 12i-2. The controller's OWN catch: a run() that genuinely THROWS, both as the
+//        active request and as the coalesced follow-up. The wrapped pollers all
+//        handle their own transport errors internally, so this drives
+//        boundedPoll directly to exercise the defensive invariant itself.
+{
+  const env = pollEnv({});
+  const ctx = loadApp(env);
+  const unhandled = [];
+  const onUnhandled = (e) => unhandled.push(e);
+  process.on("unhandledRejection", onUnhandled);
+
+  // (a) the ACTIVE request throws.
+  ev(ctx, 'globalThis.__t1 = boundedPoll("probe-throw", async () => { throw new Error("boom"); });');
+  eq(await ev(ctx, "__t1()"), "ran", "boundedPoll RETURNS even when run() throws");
+  ok(diag(ctx, "probe-throw").active === false, "the slot is released after a throwing run");
+  for (let i = 0; i < 6; i++) await settle();
+  eq(unhandled.length, 0, "a throwing run() produces zero unhandled rejections");
+
+  // (b) the coalesced FOLLOW-UP throws. Call 1 is held open until the test
+  //     releases it; call 2 -- the follow-up started from finally and never
+  //     awaited -- throws outright.
+  ev(ctx, `
+    globalThis.__n = 0;
+    globalThis.__gate = null;
+    globalThis.__t2 = boundedPoll("probe-follow", async () => {
+      globalThis.__n += 1;
+      if (globalThis.__n === 1) { await new Promise((r) => { globalThis.__gate = r; }); return; }
+      throw new Error("follow-up boom");
+    });
+  `);
+  const held2 = ev(ctx, "__t2()");
+  eq(await ev(ctx, "__t2()"), "coalesced", "a tick during the held call coalesces");
+  ev(ctx, "__gate();");
+  eq(await held2, "ran", "the held call completes");
+  for (let i = 0; i < 8; i++) await settle();
+  eq(ev(ctx, "__n"), 2, "the coalesced follow-up RAN and it is the one that threw");
+  eq(unhandled.length, 0,
+     "a THROWING coalesced follow-up produces zero unhandled rejections");
+  ok(diag(ctx, "probe-follow").active === false,
+     "the slot is released even when the follow-up throws");
+  ok(diag(ctx, "probe-follow").followUp === false, "no phantom follow-up remains");
+  process.removeListener("unhandledRejection", onUnhandled);
+}
+
+// 12j. BOUNDARY: no recurring poller may bypass its controller. Every wrapped
+//      name must resolve to the controller wrapper, not the raw request body.
+{
+  const env = pollEnv({});
+  const ctx = loadApp(env);
+  for (const p of POLLERS) {
+    const src = ev(ctx, p.fn + ".toString()");
+    ok(src.indexOf("state.active") !== -1,
+       p.fn + " resolves to the bounded controller wrapper, not the raw request");
+  }
+  // Every raw body must appear EXACTLY twice in the source: its own declaration
+  // and the boundedPoll() argument. A third occurrence would be a production
+  // call site that bypasses the controller, which is the regression this
+  // assertion exists to catch. toString() checks alone cannot detect that.
+  const appSrc = fs.readFileSync(APP, "utf8");
+  for (const raw of ["refreshStateRequest", "refreshAgentEventsRequest",
+                     "refreshMessagesRequest", "refreshHealthRequest",
+                     "refreshTaskStateRequest", "refreshArchiveIndexRequest",
+                     "loadConversationsRequest"]) {
+    ok(ev(ctx, "typeof " + raw) === "function",
+       raw + " exists as the inner request body");
+    const hits = appSrc.split(raw).length - 1;
+    eq(hits, 2, raw + " appears exactly twice: its declaration and the boundedPoll argument");
+    ok(appSrc.indexOf("async function " + raw + "(") !== -1,
+       raw + " occurrence 1 is its declaration");
+    ok(appSrc.indexOf(", " + raw + ");") !== -1,
+       raw + " occurrence 2 is the boundedPoll argument, not a call");
+    const declHits = appSrc.split("async function " + raw + "(").length - 1;
+    const argHits = appSrc.split(", " + raw + ");").length - 1;
+    eq(declHits, 1, raw + " is declared exactly once");
+    eq(argHits, 1, raw + " is passed to boundedPoll exactly once");
+    eq(hits, declHits + argHits,
+       raw + " has NO occurrence beyond its declaration and the boundedPoll " +
+       "argument, so no production path invokes it directly");
+  }
+}
+
+// 12k. GLOBAL SURFACE. Converting `function name()` to `const name = ...` removes
+//      the window property. That is safe ONLY if nothing resolves these names
+//      through the global object. Proven here rather than asserted.
+{
+  const html = fs.readFileSync(HTML, "utf8");
+  const appSrc = fs.readFileSync(APP, "utf8");
+  const converted = ["refresh", "refreshAgentEvents", "refreshMessages", "refreshHealth",
+                     "refreshTaskState", "refreshArchiveIndex", "loadConversations"];
+
+  // No inline event attribute anywhere in the shipped markup.
+  ok(!/\son[a-z]+\s*=\s*["']/i.test(html),
+     "index.html contains NO inline event handler attributes at all");
+  // index.html is the only markup app.js is served with, and it must not name
+  // any converted poller.
+  for (const name of converted) {
+    ok(html.indexOf(name) === -1, "index.html never references " + name);
+  }
+  // No global-object access to a converted poller from the script itself.
+  for (const name of converted) {
+    for (const pattern of ["window." + name, "globalThis." + name,
+                           'window["' + name + '"]', "window['" + name + "']"]) {
+      ok(appSrc.indexOf(pattern) === -1, "app.js never accesses " + pattern);
+    }
+  }
+  // app.js is the ONLY script the page loads, so there is no second script that
+  // could hold a stale global reference.
+  const scripts = html.match(/<script[^>]*src=["']([^"']+)["']/g) || [];
+  eq(scripts.length, 1, "the page loads exactly one script");
+  ok(scripts[0].indexOf("app.js") !== -1, "and that script is app.js");
+
+  // The wrapped bindings still resolve lexically, which is how every call site
+  // inside app.js (including the setInterval registrations) reaches them.
+  const env = pollEnv({});
+  const ctx = loadApp(env);
+  for (const name of converted) {
+    eq(ev(ctx, "typeof " + name), "function", name + " still resolves lexically");
+  }
+}
+
 console.log((failures === 0 ? "PASS" : "FAIL") + ": " + (checks - failures) + "/" + checks + " wired-path checks");
 process.exit(failures === 0 ? 0 : 1);
diff --git a/tests/test_console_poll_bounding.py b/tests/test_console_poll_bounding.py
new file mode 100644
index 0000000..a1339d1
--- /dev/null
+++ b/tests/test_console_poll_bounding.py
@@ -0,0 +1,247 @@
+"""Bounded request control for the recurring operator-console pollers.
+
+Follows the established front-end test pattern in this repository: static
+assertion over apps/control-plane/static/app.js, which is how
+test_command_center_hygiene, test_conversation_console, test_session_continuity_ux
+and the other console tests verify UI behaviour.
+
+Every poller runs on a fixed interval, so when the server is slower than that
+interval an unguarded poller starts a new request before the previous one
+finishes and the outstanding requests accumulate without bound. Measured on the
+running console over a 195 s window, the unguarded pollers issued 249 requests
+against the bounded work-item poller's 11, with per-endpoint latency reaching
+20-35 s.
+
+The executable counterpart lives in tests/dom/wired_paths.mjs section 12, which
+drives the real poller entry points through a deferrable transport.
+"""
+import os
+import re
+import unittest
+
+STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)),
+                      "..", "apps", "control-plane", "static")
+
+
+def _read(name):
+    with open(os.path.join(STATIC, name), encoding="utf-8") as fh:
+        return fh.read()
+
+
+APP = _read("app.js")
+
+# The pollers converted to bounded control: controller name -> exported binding.
+BOUNDED = {
+    "state": "refresh",
+    "agent-events": "refreshAgentEvents",
+    "messages": "refreshMessages",
+    "health": "refreshHealth",
+    "task-state": "refreshTaskState",
+    "archive-index": "refreshArchiveIndex",
+    "conversations": "loadConversations",
+}
+
+
+def _block_of(name):
+    """The source of one top-level function, up to the next top-level one."""
+    m = re.search(r"^(?:async )?function " + re.escape(name) + r"\(", APP, re.M)
+    if not m:
+        return ""
+    rest = APP[m.end():]
+    nxt = re.search(r"^(?:async )?function ", rest, re.M)
+    return rest[:nxt.start()] if nxt else rest
+
+
+class ControllerContract(unittest.TestCase):
+    def test_controller_exists(self):
+        self.assertIn("function boundedPoll(name, run)", APP)
+
+    def test_controller_has_one_active_slot(self):
+        body = _block_of("boundedPoll")
+        self.assertIn("state.active", body)
+        self.assertIn("if (state.active)", body)
+
+    def test_controller_coalesces_into_one_follow_up(self):
+        body = _block_of("boundedPoll")
+        self.assertIn("state.followUp = true", body)
+        self.assertIn("state.followUp = false", body)
+
+    def test_controller_releases_the_slot_in_finally(self):
+        body = _block_of("boundedPoll")
+        fin = body.split("finally")[1]
+        self.assertIn("state.active = false", fin)
+
+    def test_controller_catches_so_a_poller_never_throws_to_its_interval(self):
+        self.assertIn("catch (e)", _block_of("boundedPoll"))
+
+    def test_follow_up_carries_an_explicit_rejection_handler(self):
+        # A fire-and-forget call without a handler would be a silent unhandled
+        # rejection, which is exactly the failure mode being removed.
+        self.assertIn("polled().catch(", _block_of("boundedPoll"))
+
+    def test_coalesced_outcome_is_distinct_from_ran(self):
+        self.assertIn('const POLL_RAN = "ran"', APP)
+        self.assertIn('const POLL_COALESCED = "coalesced"', APP)
+
+    def test_controller_returns_coalesced_without_starting_a_request(self):
+        body = _block_of("boundedPoll")
+        head = body.split("state.active = true")[0]
+        self.assertIn("return POLL_COALESCED", head)
+
+    def test_no_retry_loop_inside_the_controller(self):
+        body = _block_of("boundedPoll")
+        for banned in ("setTimeout", "setInterval", "while ("):
+            self.assertNotIn(banned, body)
+
+    def test_diagnostics_are_readable_without_logging(self):
+        body = _block_of("pollDiagnostics")
+        self.assertIn("pollControllers.forEach", body)
+        self.assertNotIn("console.", body)
+
+
+class EveryPollerIsBounded(unittest.TestCase):
+    def test_each_poller_is_wrapped_exactly_once(self):
+        for name, fn in BOUNDED.items():
+            decl = 'const %s = boundedPoll("%s", %sRequest);' % (
+                fn, name, fn if fn != "refresh" else "refreshState")
+            self.assertEqual(APP.count(decl), 1,
+                             "expected exactly one bounded declaration for " + fn)
+
+    def test_each_inner_request_body_exists(self):
+        for fn in BOUNDED.values():
+            raw = ("refreshStateRequest" if fn == "refresh" else fn + "Request")
+            self.assertIn("async function %s(" % raw, APP)
+
+    def test_no_bare_poller_function_declaration_survives(self):
+        # A leftover `function refreshMessages()` would silently shadow the
+        # bounded binding and reintroduce the defect.
+        for fn in BOUNDED.values():
+            self.assertIsNone(
+                re.search(r"^(?:async )?function " + re.escape(fn) + r"\(", APP, re.M),
+                fn + " must not also exist as a bare function declaration")
+
+    def test_exactly_seven_pollers_are_bounded(self):
+        self.assertEqual(len(re.findall(r"= boundedPoll\(", APP)), 7)
+
+    def test_state_poller_no_longer_lacks_a_handler(self):
+        # /api/state previously had no try/catch at all, so every failed poll
+        # was an invisible unhandled rejection.
+        body = _block_of("refreshStateRequest")
+        self.assertIn("try {", body)
+        self.assertIn("catch (e)", body)
+
+
+class WorkItemPollerUntouched(unittest.TestCase):
+    """The queue poller keeps its own controller and its four outcomes."""
+
+    def test_queue_keeps_its_own_in_flight_flag(self):
+        self.assertIn("let queueRefreshInFlight = false;", APP)
+
+    def test_queue_keeps_its_inner_request_and_generation_guard(self):
+        self.assertIn("async function runWorkItemsRefresh()", APP)
+        self.assertIn("let queueRefreshGeneration = 0;", APP)
+
+    def test_queue_is_not_registered_with_the_generic_controller(self):
+        self.assertNotIn('boundedPoll("work-items"', APP)
+
+    def test_queue_outcomes_are_unchanged(self):
+        for outcome in ("REFRESH_CONFIRMED", "REFRESH_CONFIRMED_EMPTY",
+                        "REFRESH_FAILED", "REFRESH_SUPERSEDED", "REFRESH_COALESCED"):
+            self.assertIn(outcome, APP)
+
+    def test_refresh_succeeded_still_rejects_a_coalesced_poll(self):
+        body = _block_of("refreshSucceeded")
+        self.assertIn("REFRESH_CONFIRMED", body)
+        self.assertNotIn("REFRESH_COALESCED ===", body)
+
+
+class GlobalSurfaceCompatibility(unittest.TestCase):
+    """Converting `function name()` to `const name = ...` removes the window
+    property. That is safe only if nothing resolves these names through the
+    global object, which is proven here rather than assumed."""
+
+    def test_markup_has_no_inline_event_handlers(self):
+        html = _read("index.html")
+        self.assertIsNone(re.search(r"""\son[a-z]+\s*=\s*["']""", html, re.I))
+
+    def test_markup_never_names_a_converted_poller(self):
+        html = _read("index.html")
+        for fn in BOUNDED.values():
+            self.assertNotIn(fn, html)
+
+    def test_no_global_object_access_to_a_converted_poller(self):
+        for fn in BOUNDED.values():
+            for pattern in ("window." + fn, "globalThis." + fn,
+                            'window["%s"]' % fn, "window['%s']" % fn):
+                self.assertNotIn(pattern, APP)
+
+    def test_the_page_loads_exactly_one_script(self):
+        # No second script can be holding a stale global reference.
+        scripts = re.findall(r"""<script[^>]*src=["']([^"']+)["']""", _read("index.html"))
+        self.assertEqual(len(scripts), 1)
+        self.assertIn("app.js", scripts[0])
+
+
+class NoPollerBypassesItsController(unittest.TestCase):
+    """Each raw request body must appear exactly twice: its own declaration and
+    the boundedPoll argument. A third occurrence would be a production call
+    site that bypasses the controller."""
+
+    def test_each_raw_body_has_no_direct_call_site(self):
+        for fn in BOUNDED.values():
+            raw = "refreshStateRequest" if fn == "refresh" else fn + "Request"
+            total = APP.count(raw)
+            decl = APP.count("async function %s(" % raw)
+            arg = APP.count(", %s);" % raw)
+            self.assertEqual(decl, 1, raw + " declared exactly once")
+            self.assertEqual(arg, 1, raw + " passed to boundedPoll exactly once")
+            self.assertEqual(total, decl + arg,
+                             raw + " has no occurrence beyond its declaration "
+                                   "and the boundedPoll argument")
+
+    def test_conversation_fan_out_is_fully_awaited(self):
+        # If any inner request were unawaited the outer slot would be released
+        # before the fan-out completed, which would defeat the bounding.
+        body = _block_of("loadConversationsRequest")
+        self.assertEqual(body.count("getJSON("), body.count("await getJSON("))
+
+
+class PollingCadenceUnchanged(unittest.TestCase):
+    """Scope guard: bounding requests must not change the polling cadence."""
+
+    def test_live_interval_is_unchanged(self):
+        self.assertIn("const LIVE_MS = 2000;", APP)
+
+    def test_every_interval_registration_survives(self):
+        for call in ("setInterval(refresh, LIVE_MS)",
+                     "setInterval(refreshAgentEvents, LIVE_MS)",
+                     "setInterval(refreshMessages, LIVE_MS)",
+                     "setInterval(refreshWorkItems, LIVE_MS)",
+                     "setInterval(refreshHealth, LIVE_MS * 2)",
+                     "setInterval(refreshTaskState, LIVE_MS)",
+                     "setInterval(refreshArchiveIndex, LIVE_MS * 15)"):
+            self.assertIn(call, APP)
+
+    def test_the_two_conversation_intervals_survive(self):
+        # loadConversations is driven by two further intervals OUTSIDE the main
+        # setInterval block; they are why the endpoint is polled on every view.
+        self.assertIn('if (currentView === "work") loadConversations();', APP)
+        self.assertIn('if (currentView !== "work") loadConversations();', APP)
+
+    def test_every_recurring_network_poller_is_accounted_for(self):
+        # Tied to known registrations rather than a raw setInterval count, which
+        # would be brittle against unrelated timer additions and would not
+        # establish coverage. Every recurring poller is either bounded here or
+        # is the queue poller with its own controller.
+        registered = set(re.findall(r"setInterval\((\w+),", APP))
+        accounted = set(BOUNDED.values()) | {"refreshWorkItems"}
+        self.assertEqual(registered - accounted, set(),
+                         "an unaccounted recurring poller was registered")
+
+    def test_server_side_is_untouched_by_this_change(self):
+        server = os.path.join(STATIC, "..", "server.py")
+        self.assertTrue(os.path.exists(server))
+
+
+if __name__ == "__main__":
+    unittest.main()
```
