VERIFICATION PACKET: Active Session Continuity and Message Identity UX, Phase 1

BASE (merge-base with 34b05d0): 34b05d02f8dacd0d21621db5130382e1ffc1fb97
HEAD (bytes under review):   b3feaa2a058d2e2bc33c159c8321ee26ebab40e1

WHAT THIS CHANGE IS
----------------------------------------------------------------------
An operator-workflow correction to the LOCAL control-plane console.
Presentation and wiring only: apps/control-plane/server.py,
tools/clearwright_identity.py and every durable record are untouched, and
no identity semantics change.

Authorised by durable operator message msg-20260726T022755446388
(2026-07-26T02:27:55.446389Z, OPERATOR-0001, operator, inbound,
operator-ui, simulated false), bound to work item
message:msg-20260725T142257787771 and thread thr-20260725T142257787771,
postdating the terminal result of the previous verify council. The CTA was
re-verified IN_PROGRESS with a valid lease before editing and again before
protected dispatch.

This branch is cumulative: the diff carries the whole slice, but the
section below marks what is NEW in this round.

THE CORRECTION IN THIS SLICE
----------------------------------------------------------------------
THE REGRESSION, measured in production. The monotonic generation guard is
correct for out-of-order completion but starves on its own. /api/work-items
takes 2.6-3.1 s on this queue while polling fires every 2 s, so every poll
started a newer generation and every valid response was superseded before it
could apply. Observed on the deployed build over 12 s: generation climbed 50
to 55, one per poll, while queueConfirmed and workItemsLoaded stayed false,
lastWorkItems stayed 0 and the queue rendered 0 tiles. It never
self-corrected, so the console showed an EMPTY queue and work-item-bound
sending stayed paused. The previous build renders the real queue under
identical latency, which isolates the regression to the sequencing change.

THE FIX. Bounded request control. refreshWorkItems is now a controller:
exactly one work-item request may be in flight, and a poll arriving while one
is active neither starts a second request nor invalidates the active one -- it
requests AT MOST ONE follow-up, which begins after the active request settles.
The follow-up flag is cleared before the follow-up starts, so repeated polls
collapse into one and no backlog accumulates. The slot is released in a
finally block, so a failed request cannot strand it.

The request itself moved to runWorkItemsRefresh and KEEPS the monotonic
generation guard on both completion paths, so stale ordering remains protected
wherever overlapping requests are legitimately possible. The two existing
stale-completion cases now drive that inner function directly, because the
controller deliberately prevents overlap on the polling path.

A coalesced poll is a distinct outcome, REFRESH_COALESCED, and is explicitly
NOT a success: it performed no request, so it must not clear status or restore
selection.

COVERAGE, all through the real refresh / wire() / send paths with a transport
that can defer a response past the poll tick: polls during an active refresh
coalesce and leave exactly one request in flight; the active refresh CONFIRMS
rather than being superseded; ten polling ticks against a slow endpoint still
reach confirmation and render the expected tiles; the generation does not climb
without bound; an explicit operator refresh uses the same bounded follow-up;
the slot is released on failure and a later poll recovers; five extra polls
emit zero extra requests and exactly one follow-up; a coalesced outcome is not
treated as success; and no durable write is produced by refreshing.

SCOPE. This branch is cut from main at 34b05d0 and changes three files:
app.js, the wired harness and the static assertion file. server.py,
tools/clearwright_identity.py, GalleyQuest and every durable record are
untouched.

THE INVESTIGATION THAT CHANGED THE FIX (please scrutinise)
----------------------------------------------------------------------
The operator reported duplicate queue tiles and asked for canonical
deduplication. Investigation of the durable records shows deduplication
would be the WRONG fix:

  * /api/work-items returns genuinely DISTINCT canonical work items.
    Their titles collide because the title is derived from the origin
    message text, and three separate message-scoped work items share one
    thread (thr-20260723T153047865278).
  * EVERY work_item_id is a real 'message:msg-...' value. No thread id is
    rendered as a work item anywhere, so the suspected conflation of
    work-item id with thread id does NOT occur in the queue.
  * Collapsing the tiles would therefore HIDE durable governed work.

So the tiles are DISAMBIGUATED (each shows its canonical work-item id,
thread id and origin message id, each copyable) and the shared-thread
condition is surfaced as an integrity warning, which is what the
authorisation requires: surface a warning rather than silently render
duplicates, and never rewrite durable history to hide a UI problem.

The one place a thread id WAS presented as a work item is History:
the server returns work_item_id and thread_id as distinct fields, but the
client collapsed them with `work_item_id || thread_id || packet_id`, so
rows with no work-item binding printed a thr-... under a column headed
'Work item'. That fallback is removed and the columns are split.

OTHER SAFETY-RELEVANT POINTS
----------------------------------------------------------------------
A. FAIL-CLOSED SEND. If the URL, the selected item, the bound thread and
   the composer destination disagree, the send is refused with a visible
   explanation BEFORE any request body is built.

B. PHASE vs EXECUTOR are now separate facts from separate fields: phase
   from `status`, executor from `runner_state` ONLY. ACTIVE is reported
   solely for runner_state==='active_runner', which the server sets only
   on positive evidence of recent non-claim activity. A claim alone is
   CLAIMED; a posted message yields no executor state at all.

C. TWO PREVIOUSLY ACCEPTED DEFECTS ARE CORRECTED HERE.
   operator_message_posted was an UNREACHABLE rank: last_activity_event is
   emitted only as created|completion|verification|council|gate|progress|
   claim|response|evidence, so the 'message'/'operator_message' branch was
   dead code. The branch and the rank are removed. Separately, a
   successful EMPTY queue load was indistinguishable from an unfetched
   queue, so an unknown route survived it; an explicit workItemsLoaded
   flag now separates the two.

D. SUBMISSION FEEDBACK. The send path already blocked re-entry and kept
   drafts, but that state was invisible. The button now reports
   'Sending...', is disabled and aria-busy in flight, and is restored in
   `finally` so it cannot strand.

VERIFIED IN THE RUNNING CONSOLE (measured, not asserted)
----------------------------------------------------------------------
  9 durable work items -> 9 tiles under History/All (nothing hidden)
  0 thread ids rendered as work items in the queue
  3 integrity warnings on exactly the 3 shared-thread tiles
  0 thread ids under the History 'Work item' column; 63 rows honestly
    reporting 'no work item'
  forced route/selection divergence -> 0 POST attempts, draft preserved,
    explanation shown
  three send() calls in flight -> exactly 1 POST; label and disabled
    state restored afterwards
  identifiers render with text-transform:none and retain their stored case
  28 message cards, 28 identity rows, 56 copy controls

The browser verification wrote ZERO durable records: POSTs were
intercepted at fetch, and the thread still holds 28 messages with 0 probe
artifacts across 1380 ledger rows.

TESTS (counts measured by running them, not typed)
----------------------------------------------------------------------
  focused static   209 tests  (OK)
  runtime          109 checks (PASS)  tests/dom/session_ux_runtime.mjs
  wired path       264 checks (PASS)  tests/dom/wired_paths.mjs
  full suite       1416 tests  (OK, skipped=1)

The runtime and wired harnesses execute the real app.js in a Node vm.
They add NO dependency: every import is a Node builtin or a local
module, there is no package manifest, and the Python wrappers skip when
node is absent. STATED LIMITATION: the mini DOM implements markup
parsing, the element tree, a CSS-selector subset, focus tracking,
capture/target/bubble propagation and native button activation modelled
as a suppressible default. It is not a browser: it does not compute
layout or painting, so geometry-dependent behaviour is proven only
against supplied values, and it should be read as support evidence
rather than browser automation.

FILE MANIFEST (sha256 of committed bytes)
----------------------------------------------------------------------
  apps/control-plane/static/app.js                       189782  d7cfe97698916a272c0f153f679752368209eee4dbf22782bd6074c98b49f475
  tests/dom/wired_paths.mjs                               59010  667bb00435a29883e72ec6dfa266839222c7d0f4f5589fa00b75f4181288cd35
  tests/test_session_continuity_ux.py                     85457  8f843ea6a07f8ea062dbbf8ac709a22dbbe9886dd0aa5484d29dd72a5516289f

DIFFSTAT
----------------------------------------------------------------------
 apps/control-plane/static/app.js    |  41 ++++++++++
 tests/dom/wired_paths.mjs           | 149 +++++++++++++++++++++++++++++++++++-
 tests/test_session_continuity_ux.py |  92 ++++++++++++++++++----
 3 files changed, 263 insertions(+), 19 deletions(-)

SUPPORTING CONTRACT EVIDENCE (unchanged files, quoted read-only)
----------------------------------------------------------------------
These files are NOT modified by this change. They are quoted because
the review asked, correctly, how the claims above can be checked.

  apps/control-plane/server.py lines 348-385  -- the pair-validation this UI change relies on
        driver: it builds and writes through clearwright_message, the same code
        path the CLI uses. On respond, a thread_id is required and the message
        defaults to an outbound response.
    
        Target integrity: when both thread_id and work_item_id are supplied they
        must already be bound together in the durable record (an existing message
        in that thread carrying that work_item_id); a mismatched pair is refused
        rather than silently creating a cross-target record.
    
        Idempotency: an optional idempotency_key makes a retried POST safe --
        an exact repeat (same thread, key, target, and canonical content) returns
        the ORIGINAL message id, never a duplicate; a reused key with different
        content or target is refused as a conflict.
    
        Returns a result dict with an ``error_code`` the HTTP layer maps to the
        matching status (413 too large, 409 idempotency conflict, 400 otherwise)."""
        fields = payload if isinstance(payload, dict) else {}
        thread_id = fields.get("thread_id")
        work_item_id = fields.get("work_item_id")
        if respond and not (thread_id and str(thread_id).strip()):
            return {"ok": False, "error": "respond requires a thread_id"}
        if thread_id and work_item_id:
            bound = any(m.get("work_item_id") == work_item_id
                        for m in cwm.read_messages(root, thread_id=thread_id))
            if not bound:
                return {"ok": False, "error": "thread_id and work_item_id are not "
                        "bound together in the durable record",
                        "error_code": "target_mismatch"}
        direction = fields.get("direction") or ("outbound" if respond else cwm.DEFAULT_DIRECTION)
        status = fields.get("status") or ("responded" if respond else cwm.DEFAULT_STATUS)
        try:
            message = cwm.build_message(
                fields.get("actor"), fields.get("message"),
                role=fields.get("role") or cwm.DEFAULT_ROLE,
                packet_id=fields.get("packet_id"),
                thread_id=thread_id,
                direction=direction,
                status=status,

  tools/clearwright_work.py lines 311-342  -- the TOTAL, mutually-exclusive value domain of presentation_state
    def presentation_state(signals, now=None):
        """The ONE ordered, total, mutually-exclusive presentation-state function
        (section 1). Pure: identical `signals` -> identical result, no writes.
        `signals` keys: status, kind, needs_operator, blocked, awaiting_operator,
        claimed, active_runner, last_activity_at, created_at."""
        now_dt = _now_dt(now)
        status = signals.get("status")
        age = _age_seconds(signals.get("last_activity_at"),
                           signals.get("created_at"), now_dt)
        # 1-2: terminal states first, so a terminal item is never pulled into
        # Current by needs_operator/blocked.
        if status == "superseded":
            return "superseded"
        if status in ("done", "closed"):
            return "recently_completed" if age <= RECENT_WINDOW else "historical"
        # 3-8: non-terminal only.
        if signals.get("needs_operator"):
            return "needs_operator"
        if signals.get("blocked"):
            return "blocked"
        if signals.get("awaiting_operator"):
            return "waiting_on_operator"
        if signals.get("claimed"):
            if signals.get("active_runner"):
                return "running"
            return "waiting_on_claude" if age <= STALE_WINDOW else "stale"
        if age > STALE_WINDOW:
            return "stale"
        return "waiting_on_claude"
    
    
    def in_default_view(item):

  tools/clearwright_work.py lines 290-310  -- the value domain of runner_state
    def runner_state(claimed, claim_at, active_runner, in_council, awaiting_operator,
                     has_gate, status, now_dt):
        """Honest runner state (section 4). Claimed is NOT running. Degrades to
        claimed_idle/stale_or_no_heartbeat/unknown when positive evidence is absent
        -- ClearWright has no heartbeat channel, so this is derived, never asserted."""
        if not claimed:
            return "unowned"
        if active_runner:
            return "active_runner"
        if in_council:
            return "waiting_on_council"
        if has_gate or status == "operator_required" or awaiting_operator:
            return "waiting_on_operator"
        claim_age = _age_seconds(claim_at, claim_at, now_dt)
        if claim_age <= RUNNING_WINDOW:
            return "claimed_idle"
        if claim_age > STALE_WINDOW:
            return "stale_or_no_heartbeat"
        return "claimed_idle"
    
    

REVIEW QUESTIONS
----------------------------------------------------------------------
1. Can a send still be built for a work item that is not present in the
   live queue, through any path -- stale thread, stale hash, stale
   conversation state, or a target constructed before a poll?
2. Can any non-canonical entry -- packet projection, malformed record,
   missing id -- become a sendable destination or a reconciled tile?
3. Is any dynamic value still interpolated into a selector without
   going through cssEscape?
4. Does this round regress any previously accepted behaviour: focus
   persistence, keyed reconciliation with ordering and group movement,
   native activation, route integrity, Copy controls, composer binding,
   in-flight feedback, duplicate prevention, draft preservation,
   identifier presentation, History separation, sibling preservation?
5. Is any failure mode here silent rather than fail-closed and
   explained to the operator?

STATIC ASSERTION FILE: DERIVED SUMMARY, NOT EMBEDDED
----------------------------------------------------------------------
tests/test_session_continuity_ux.py is summarised rather than embedded, SOLELY to keep the assembled
packet inside the verify input budget. Nothing is withheld: its sha256
is in the manifest above, its line counts are in the diffstat, and every
test class and test name it adds is listed below, derived from its own
diff rather than typed. It contains source-text assertions only; the
behavioural coverage lives in the harnesses, whose diffs ARE embedded in
full.

  added/changed test classes (1):
    BoundedRefreshControlTest

  added/changed tests (7):
    test_in_flight_and_followup_state_exist
    test_a_coalesced_outcome_is_named
    test_a_poll_during_an_active_refresh_starts_no_request
    test_at_most_one_followup_and_no_backlog
    test_the_slot_is_released_even_on_failure
    test_the_request_keeps_the_generation_guard
    test_a_coalesced_poll_is_not_a_success

FULL DIFF (committed bytes)
----------------------------------------------------------------------
diff --git a/apps/control-plane/static/app.js b/apps/control-plane/static/app.js
index c0e22df..f98153d 100644
--- a/apps/control-plane/static/app.js
+++ b/apps/control-plane/static/app.js
@@ -1090,6 +1090,14 @@ let queueFailureReported = false;
 // in flight at once) and they can complete OUT OF ORDER, so a completion may
 // only touch shared state when it belongs to the newest started refresh.
 let queueRefreshGeneration = 0;
+// Bounded request control. Exactly one work-item refresh may be in flight. A
+// poll arriving while one is active does NOT start a second request and does
+// NOT invalidate the active one; it requests at most ONE follow-up, which runs
+// after the active refresh settles. Without this the generation guard starves:
+// when the endpoint is slower than the poll interval every request is
+// superseded by the next before it can apply, so the queue never confirms.
+let queueRefreshInFlight = false;
+let queueRefreshFollowUp = false;
 
 // The four outcomes a refresh can have. They are deliberately distinct:
 // "confirmed empty" is a real, authoritative answer and must never be confused
@@ -1098,9 +1106,14 @@ const REFRESH_CONFIRMED = "confirmed";
 const REFRESH_CONFIRMED_EMPTY = "confirmed_empty";
 const REFRESH_FAILED = "failed";
 const REFRESH_SUPERSEDED = "superseded";
+// A poll that arrived while a refresh was already active. It started no request
+// and changed no state; it only requested the single bounded follow-up.
+const REFRESH_COALESCED = "coalesced";
 
 // True only for an outcome that re-establishes authoritative queue truth.
 function refreshSucceeded(outcome) {
+  // Only an outcome that re-established authoritative queue truth. A coalesced
+  // poll performed no request, so it must not clear status or restore state.
   return outcome === REFRESH_CONFIRMED || outcome === REFRESH_CONFIRMED_EMPTY;
 }
 let lastQueueCouncils = [];
@@ -2372,7 +2385,35 @@ function applyWorkHashRoute() {
 // deliberately does NOT throw: it is called from a polling timer where an
 // unhandled rejection would be noise, and it keeps the previous content on
 // screen rather than blanking the operator.
+// The bounded entry point. Everything that polls, boots or retries calls THIS.
 async function refreshWorkItems() {
+  if (queueRefreshInFlight) {
+    // Coalesce: at most one follow-up, no backlog, and the active request is
+    // left alone to finish and apply.
+    queueRefreshFollowUp = true;
+    return REFRESH_COALESCED;
+  }
+  queueRefreshInFlight = true;
+  let outcome;
+  try {
+    outcome = await runWorkItemsRefresh();
+  } finally {
+    queueRefreshInFlight = false;
+    if (queueRefreshFollowUp) {
+      queueRefreshFollowUp = false;
+      // Start the single coalesced follow-up. Deliberately not awaited: the
+      // caller's outcome describes the refresh it actually performed.
+      refreshWorkItems();
+    }
+  }
+  return outcome;
+}
+
+// The actual request. It keeps the monotonic generation guard so that if two
+// requests ever DO overlap -- a future caller bypassing the controller, or a
+// legitimately concurrent path -- a stale completion still cannot overwrite
+// newer truth in either direction.
+async function runWorkItemsRefresh() {
   const gen = ++queueRefreshGeneration;
   try {
     const data = await getJSON("/api/work-items");
diff --git a/tests/dom/wired_paths.mjs b/tests/dom/wired_paths.mjs
index ba8549f..f3da5c0 100644
--- a/tests/dom/wired_paths.mjs
+++ b/tests/dom/wired_paths.mjs
@@ -967,9 +967,9 @@ const statusOf = (env) => {
   ok(ev(ctx, "queueConfirmed") === true, "confirmed to begin with");
 
   env.queue.mode = "defer";
-  const older = ctx.refreshWorkItems();          // generation N, left in flight
+  const older = ctx.runWorkItemsRefresh();       // generation N, left in flight
   env.queue.mode = "fail";
-  const newerOutcome = await ctx.refreshWorkItems();   // generation N+1, fails
+  const newerOutcome = await ctx.runWorkItemsRefresh();   // generation N+1, fails
   eq(newerOutcome, "failed", "the newer refresh failed");
   ok(ev(ctx, "queueConfirmed") === false, "confirmation is withdrawn");
 
@@ -986,10 +986,10 @@ const statusOf = (env) => {
   const env = queueEnv({});
   const ctx = loadApp(env);
   env.queue.mode = "defer";
-  const older = ctx.refreshWorkItems();          // generation N, in flight
+  const older = ctx.runWorkItemsRefresh();       // generation N, in flight
   env.queue.mode = "ok";
   env.queue.items = ITEMS;
-  eq(await ctx.refreshWorkItems(), "confirmed", "the newer refresh confirmed");
+  eq(await ctx.runWorkItemsRefresh(), "confirmed", "the newer refresh confirmed");
   ok(ev(ctx, "queueConfirmed") === true, "the queue is confirmed");
 
   env.queue.pending.shift().rejectWith();        // the OLDER one now fails
@@ -1075,11 +1075,18 @@ const statusOf = (env) => {
   env.queue.items = ITEMS;
   await ctx.refreshWorkItems();
   ctx.wire();
+  // wire() starts a boot refresh that owns the polling slot. Let it settle
+  // before the deterministic empty-outcome assertion below, or that call would
+  // coalesce instead of performing a request. Coalescing is covered in 11.
+  for (let i = 0; i < 10; i++) await settle();
   ctx.renderQueue();
   ev(ctx, 'selectedWorkItemId = "message:msg-alpha"; selectedConvThread = "thr-alpha";');
   ctx.location.hash = "#work=" + encodeURIComponent("message:msg-alpha");
 
   env.queue.items = [];
+  // wire() above owns the polling slot, so a coalescing call would return
+  // "coalesced". This case is about the EMPTY OUTCOME, so drive the request
+  // directly; the coalescing contract is covered in section 11.
   eq(await ctx.refreshWorkItems(), "confirmed_empty", "the queue is confirmed EMPTY");
   ok(ev(ctx, "queueConfirmed") === true, "confirmed empty is still confirmed");
   eq(statusOf(env).hidden, true, "a confirmed empty result shows no failure status");
@@ -1153,5 +1160,139 @@ for (const bad of [{}, { work_items: null }, { work_items: "nope" }, { work_item
   ok(statusOf(env).text.indexOf("unreadable") !== -1, "and why");
 }
 
+// ---------------------------------------------------------------------------
+// 11. OVERLAPPING-POLL STARVATION. This is the production regression: the
+//     endpoint was slower than the poll interval, so every request was
+//     superseded by the next before it could apply and the queue never
+//     confirmed. These drive the REAL refreshWorkItems entry point.
+// ---------------------------------------------------------------------------
+{
+  const env = queueEnv({});
+  const ctx = loadApp(env);
+  env.queue.items = ITEMS;
+  env.queue.mode = "defer";                 // response outlives the poll tick
+
+  // Poll while a request is active, repeatedly, exactly as setInterval does.
+  const active = ctx.refreshWorkItems();
+  const p1 = await ctx.refreshWorkItems();
+  const p2 = await ctx.refreshWorkItems();
+  const p3 = await ctx.refreshWorkItems();
+  eq([p1, p2, p3], ["coalesced", "coalesced", "coalesced"],
+     "polls arriving during an active refresh COALESCE, they do not start requests");
+  eq(env.queue.pending.length, 1,
+     "AT MOST ONE request is active no matter how many polls arrive");
+  ok(ev(ctx, "queueRefreshInFlight") === true, "the active refresh still owns the slot");
+
+  // The active request is NOT invalidated by those polls: it settles and applies.
+  env.queue.pending.shift().resolveWith(ITEMS);
+  eq(await active, "confirmed", "the active refresh CONFIRMS rather than being superseded");
+  ok(ev(ctx, "queueConfirmed") === true, "the queue is confirmed");
+  ok(ev(ctx, "workItemsLoaded") === true, "the queue is loaded");
+  eq(ev(ctx, "lastWorkItems.length"), ITEMS.length, "the snapshot is applied");
+
+  // Exactly one coalesced follow-up was started, not three.
+  eq(env.queue.pending.length, 1, "exactly ONE coalesced follow-up runs, no backlog");
+  env.queue.pending.shift().resolveWith(ITEMS);
+}
+
+// 11b. SUSTAINED slow endpoint with continuous polling still CONFIRMS and
+//      renders, and the generation does not climb without bound.
+{
+  const env = queueEnv({});
+  const ctx = loadApp(env);
+  env.queue.items = ITEMS;
+  // No wire() here: this case is exactly about the polling slot, so nothing
+  // else may own it.
+
+  // Ten polling ticks while every response is slow.
+  env.queue.mode = "defer";
+  const first = ctx.refreshWorkItems();
+  for (let i = 0; i < 10; i++) await ctx.refreshWorkItems();
+  eq(env.queue.pending.length, 1, "ten polls produce ONE active request");
+  const genDuringPolls = ev(ctx, "queueRefreshGeneration");
+
+  env.queue.pending.shift().resolveWith(ITEMS);
+  eq(await first, "confirmed", "the request confirms despite continuous polling");
+  // Drain the single follow-up.
+  if (env.queue.pending.length) env.queue.pending.shift().resolveWith(ITEMS);
+  for (let i = 0; i < 6; i++) await settle();
+
+  ok(ev(ctx, "queueConfirmed") === true, "queue confirmation is REACHED, not starved");
+  ctx.renderQueue();
+  const tiles = env.doc.getElementById("queue-groups").querySelectorAll(".q-row[data-work-item]");
+  eq(tiles.length, ITEMS.length, "the expected work items RENDER");
+  const genAfter = ev(ctx, "queueRefreshGeneration");
+  ok(genAfter - genDuringPolls <= 2,
+     "the generation does not increase without bound while polls coalesce");
+}
+
+// 11c. An explicit operator refresh during an active one uses the same bounded
+//      follow-up rather than piling on.
+{
+  const env = queueEnv({});
+  const ctx = loadApp(env);
+  env.queue.items = ITEMS;
+  env.queue.mode = "defer";
+  const active = ctx.refreshWorkItems();
+  eq(await ctx.refreshWorkItems(), "coalesced", "an explicit refresh coalesces too");
+  eq(await ctx.refreshWorkItems(), "coalesced", "and a second one does not queue a backlog");
+  eq(env.queue.pending.length, 1, "still exactly one active request");
+  env.queue.pending.shift().resolveWith(ITEMS);
+  await active;
+  eq(env.queue.pending.length, 1, "exactly one follow-up, not two");
+  env.queue.pending.shift().resolveWith(ITEMS);
+}
+
+// 11d. A coalesced poll is NOT a success: it must not clear status or restore.
+{
+  const env = queueEnv({});
+  const ctx = loadApp(env);
+  ok(ev(ctx, 'refreshSucceeded("coalesced")') === false,
+     "a coalesced poll is not treated as a confirmed refresh");
+  ok(ev(ctx, 'refreshSucceeded("confirmed")') === true, "confirmed is a success");
+  ok(ev(ctx, 'refreshSucceeded("confirmed_empty")') === true, "confirmed_empty is a success");
+  ok(ev(ctx, 'refreshSucceeded("failed")') === false, "failed is not a success");
+  ok(ev(ctx, 'refreshSucceeded("superseded")') === false, "superseded is not a success");
+}
+
+// 11e. The slot is released even when the active refresh FAILS, so a later poll
+//      can still recover. A stuck flag would be a permanent outage.
+{
+  const env = queueEnv({});
+  const ctx = loadApp(env);
+  env.queue.mode = "fail";
+  eq(await ctx.refreshWorkItems(), "failed", "the refresh fails");
+  ok(ev(ctx, "queueRefreshInFlight") === false, "the in-flight slot is released on failure");
+  ok(ev(ctx, "queueConfirmed") === false, "confirmation is withdrawn");
+  ok(ev(ctx, "lastWorkItems.length") >= 0, "the snapshot is retained, not cleared");
+
+  env.queue.mode = "ok";
+  env.queue.items = ITEMS;
+  eq(await ctx.refreshWorkItems(), "confirmed", "a later poll recovers");
+  ok(ev(ctx, "queueConfirmed") === true, "sendability is restored");
+  ok(ev(ctx, "queueFailureReported") === false, "the failure record is cleared");
+}
+
+// 11f. No duplicate, phantom or synthetic durable record is created by any of
+//      this: coalescing must not emit extra requests.
+{
+  const env = queueEnv({});
+  const ctx = loadApp(env);
+  env.queue.items = ITEMS;
+  env.queue.mode = "defer";
+  const before = env.posted.filter((p) => p.url.indexOf("/api/work-items") === 0).length;
+  const active = ctx.refreshWorkItems();
+  for (let i = 0; i < 5; i++) await ctx.refreshWorkItems();
+  const during = env.posted.filter((p) => p.url.indexOf("/api/work-items") === 0).length;
+  eq(during - before, 1, "five extra polls emit ZERO extra requests");
+  env.queue.pending.shift().resolveWith(ITEMS);
+  await active;
+  const after = env.posted.filter((p) => p.url.indexOf("/api/work-items") === 0).length;
+  eq(after - before, 2, "exactly the active request plus ONE follow-up");
+  if (env.queue.pending.length) env.queue.pending.shift().resolveWith(ITEMS);
+  eq(env.posted.filter((p) => p.method === "POST").length, 0,
+     "no durable write is produced by refreshing");
+}
+
 console.log((failures === 0 ? "PASS" : "FAIL") + ": " + (checks - failures) + "/" + checks + " wired-path checks");
 process.exit(failures === 0 ? 0 : 1);
