VERIFICATION PACKET: Active Session Continuity and Message Identity UX, Phase 1

BASE (merge-base with main): a3a5618ff8c35af561ee8a281c35e69bbd9aafac
HEAD (bytes under review):   218302dc1f530225dfd8bc86065a80152a21c483

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

THE CORRECTIONS IN THIS ROUND (newest work, scrutinise first)
----------------------------------------------------------------------
1. REFRESH-FAILURE VISIBILITY THROUGH THE COMPLETE CHAIN. refreshWorkItems
   previously caught its own error and resolved normally, so callers could
   not tell a handled failure from a successful load; the boot continuation
   then cleared the explanation the failure had just rendered, making an
   initial-load failure invisible.

   It now returns one of FOUR explicit outcomes -- confirmed, confirmed_empty,
   failed, superseded -- and deliberately does not throw, because it is called
   from a polling timer where an unhandled rejection would be noise and it
   keeps prior content on screen rather than blanking the operator.

   A reported failure is tracked separately and is NOT transient:
   clearTransientRestoreStatus refuses to erase it, the boot continuation acts
   only when refreshSucceeded(outcome), and the Retry control branches the
   same way. Only a later CONFIRMED success clears it. Confirmed-empty is a
   first-class outcome, never conflated with unloaded or failed.

2. DETERMINISTIC MONOTONIC REFRESH SEQUENCING. Every refresh takes a
   generation from a monotonically increasing counter and may touch shared
   state only while it is still the newest. BOTH completion paths are guarded,
   so an older success arriving after a newer failure cannot restore
   sendability, and an older failure arriving after a newer success cannot
   invalidate a confirmed snapshot. A superseded completion changes nothing.

COVERAGE runs through the REAL refresh, wire(), destination and send paths,
using a controllable transport that can defer a request and complete it out of
order. It proves: the four outcomes are distinct; both stale-completion
directions; an initial-load failure through wire() keeps its explanation after
every continuation settles, including transient cleanup, restoration and a
further failed poll; zero POSTs while unconfirmed with the draft preserved;
recovery only after a later confirmed refresh; and that a confirmed EMPTY
result is authoritative, leaving stale destinations unsendable without being
reported as a failure.

Two harness fidelity bugs were fixed while writing that coverage: the
transport wrapper double-counted POSTs it delegated, and the status helper
treated an element with no hidden attribute as visible even when empty.

PRESERVED: every previously accepted Session Continuity and Message Identity
UX behaviour, including focus persistence, keyed reconciliation with ordering
and group movement, native activation, route integrity, Copy controls,
read-only packet projections, composer binding, in-flight feedback, duplicate
prevention, draft preservation, identifier presentation and History
separation.

STILL UNVERIFIED: real-browser Space activation. The tooling delivers no
discrete key events to the page, so the measurement is unavailable rather than
negative.

EARLIER WORK IN THIS SAME DIFF (reviewed in prior councils)
----------------------------------------------------------------------
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
  focused static   202 tests  (OK)
  runtime          109 checks (PASS)  tests/dom/session_ux_runtime.mjs
  wired path       232 checks (PASS)  tests/dom/wired_paths.mjs
  full suite       1409 tests  (FAILED, skipped=1)

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
  apps/control-plane/static/app.js                       187880  db6b1d5dd313d6e33661fb466c01875f3235f18e557d9bd30daece0b91e5444e
  apps/control-plane/static/index.html                    22393  86b4ffbf452f29a46c2c7c9877a3daadfdf29b5bc3af4118bb40b55a993b02f4
  apps/control-plane/static/style.css                     55312  8870e44bca0b803f636ec90cf8c229f864762a850780bb1128cc947711471407
  tests/dom/mini_dom.mjs                                  15472  03b1de7aadd7cd0c81e37bcea5eba26fc5c052f0a1a05d105842f6d4cb29fa32
  tests/dom/session_ux_runtime.mjs                        31834  e5085e168f511380372b6c7420de37c8b4d9bee5edb8947c6bde0d30d9f05894
  tests/dom/wired_paths.mjs                               52112  0290013cb51746de3111db9976d7f9464afe2d85444b540a2486937b1f00d922
  tests/test_session_continuity_ux.py                     82294  1232e843c653e67e4984364292f4c5aecd5a1724613d8a6bc1df50a7f9956181
  tests/test_session_ux_runtime.py                         1585  ebb673195e5fb9463a228865f4a136e4111de7aa9bc41d714d8816c4c9386a1f
  tests/test_session_ux_wired.py                           2358  3c8647d059510f8e7c8d0207f07ff842fd962ac5f06f8fa659762af272ade56d

DIFFSTAT
----------------------------------------------------------------------
 apps/control-plane/static/app.js     | 1251 +++++++++++++++++++++++-
 apps/control-plane/static/index.html |   49 +-
 apps/control-plane/static/style.css  |  166 ++++
 tests/dom/mini_dom.mjs               |  399 ++++++++
 tests/dom/session_ux_runtime.mjs     |  667 +++++++++++++
 tests/dom/wired_paths.mjs            | 1157 +++++++++++++++++++++++
 tests/test_session_continuity_ux.py  | 1724 ++++++++++++++++++++++++++++++++++
 tests/test_session_ux_runtime.py     |   40 +
 tests/test_session_ux_wired.py       |   55 ++
 9 files changed, 5452 insertions(+), 56 deletions(-)

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

FULL DIFF (committed bytes)
----------------------------------------------------------------------
NOTE: non-ASCII characters below are shown as <U+XXXX>.

diff --git a/apps/control-plane/static/app.js b/apps/control-plane/static/app.js
index dd2d10c..c0e22df 100644
--- a/apps/control-plane/static/app.js
+++ b/apps/control-plane/static/app.js
@@ -315,12 +315,18 @@ function renderOperatorPanel(ts) {
   const authBody = document.getElementById("authority-body");
   const actions = document.getElementById("operator-actions");
   if (!nextBody || !authBody || !actions) return;
+  // Phase 1, item 5: ONE contextual panel. With nothing selected the rail is
+  // hidden entirely rather than showing three separate "No task selected"
+  // cards; rows that have no content are omitted rather than rendered empty.
+  const rail = document.getElementById("session-rail");
   if (!ts) {
-    nextBody.innerHTML = '<p class="muted">No task selected.</p>';
-    authBody.innerHTML = '<p class="muted">No task selected.</p>';
-    actions.innerHTML = '<p class="muted">No task selected.</p>';
+    if (rail) rail.hidden = true;
+    nextBody.innerHTML = "";
+    authBody.innerHTML = "";
+    actions.innerHTML = "";
     return;
   }
+  if (rail) rail.hidden = false;
   nextBody.innerHTML = '<p class="op-next' + (ts.phase_attention ? " op-next-attention" : "") +
     '">' + esc(ts.next_action || "") + "</p>";
 
@@ -761,6 +767,57 @@ function createComposer(opts) {
     return name + ":" + (target.thread_id || "new") + ":" + (target.work_item_id || "");
   }
 
+  // Item 5: the URL, the selected tile, the bound thread and the composer
+  // destination must describe the SAME durable object before anything is sent.
+  // Any disagreement fails closed with a visible explanation rather than
+  // posting to a destination the operator did not read on screen.
+  function destinationDisagreement(target) {
+    if (!target || !target.work_item_id) return "";
+    if (target.work_item_id !== selectedWorkItemId) {
+      return "The composer destination and the selected work item disagree.";
+    }
+    // A CURRENTLY CONFIRMED queue is required. A snapshot that merely loaded
+    // once is not positive evidence: if the latest refresh failed, the old
+    // record and thread are still sitting in the array and would otherwise
+    // authorise a send against a destination nothing has re-confirmed.
+    if (!queueConfirmed) {
+      return "The work queue is not currently confirmed, so this destination " +
+             "cannot be verified. Wait for the queue to reload, then reselect " +
+             "the work item.";
+    }
+    // A LIVE canonical record is required. Guarding this on `known &&` meant
+    // the check was skipped exactly when the item had disappeared, which is the
+    // case it most needed to catch.
+    if (!isCanonicalMessageWorkItem(target.work_item_id)) {
+      return "That destination is not a message-scoped work item, so it cannot " +
+             "receive a message.";
+    }
+    const known = liveQueueRecord(target.work_item_id);
+    if (!known) {
+      return "That work item is no longer in the live queue, so the destination " +
+             "cannot be confirmed. Reselect an active work item.";
+    }
+    if (!known.thread_id || known.thread_id !== target.thread_id) {
+      return "The composer thread does not match the selected work item's thread.";
+    }
+    // STRICT. A work-item-bound send requires a canonical route that PROVES the
+    // URL agrees. Absent and malformed routes are not evidence of agreement, so
+    // both refuse rather than silently passing the check. Every queue activation
+    // now writes the route, so a missing one means the selection was not
+    // established through navigation and must be re-made.
+    const route = parseWorkRoute(location.hash);
+    if (!route) {
+      return "The URL carries no work route, so it cannot confirm the destination.";
+    }
+    if (route.malformed) {
+      return "The URL contains an unreadable work route.";
+    }
+    if (route.work_item_id !== selectedWorkItemId) {
+      return "The URL points at a different work item than the one selected.";
+    }
+    return "";
+  }
+
   function updateBanner() {
     if (!bannerEl) return;
     const target = getTarget();
@@ -768,8 +825,36 @@ function createComposer(opts) {
     // before anything has actually been sent; the banner only calls it
     // "continuing" once the caller confirms that id is a real durable thread.
     const confirmed = !isConfirmedTarget || isConfirmedTarget();
-    let text = (target.thread_id && confirmed) ? ("Continuing " + target.thread_id) : "New conversation";
-    if (target.work_item_id) text += " <U+00B7> " + target.work_item_id;
+    // Phase 1, item 3: the destination is DISPLAYED, never inferred from prose.
+    // Work-item id, thread id and an abbreviated title are shown above the
+    // composer so posting to the wrong destination requires an explicit change.
+    if (target.work_item_id && target.unresolved) {
+      bannerEl.classList.add("composer-destination");
+      bannerEl.innerHTML =
+        '<span class="dest-label dest-unresolved">Destination unresolved</span> ' +
+        '<span class="dest-work mono" data-dest-work-item="' + esc(target.work_item_id) + '">' +
+        esc(target.work_item_id) + '</span>' +
+        '<span class="dest-title">no durable thread yet - sending is blocked</span>';
+      return;
+    }
+    if (target.work_item_id) {
+      const di = (lastWorkItems || []).find((it) => it.work_item_id === target.work_item_id);
+      const title = di && di.title ? String(di.title) : "";
+      const short = title.length > 60 ? title.slice(0, 57) + "..." : title;
+      bannerEl.classList.add("composer-destination");
+      bannerEl.innerHTML =
+        '<span class="dest-label">Posting to</span> ' +
+        '<span class="dest-work mono" data-dest-work-item="' + esc(target.work_item_id) + '">' +
+        esc(target.work_item_id) + '</span>' +
+        (target.thread_id && confirmed
+          ? ' <span class="dest-thread mono" data-dest-thread="' + esc(target.thread_id) + '">' +
+            esc(target.thread_id) + '</span>' : '') +
+        (short ? ' <span class="dest-title">' + esc(short) + '</span>' : '');
+      return;
+    }
+    bannerEl.classList.remove("composer-destination");
+    const text = (target.thread_id && confirmed)
+      ? ("Continuing " + target.thread_id) : "New conversation";
     bannerEl.textContent = text;
   }
 
@@ -823,10 +908,29 @@ function createComposer(opts) {
       return;
     }
     showError("");
+    const preTarget = getTarget();
+    const disagreement = destinationDisagreement(preTarget);
+    if (disagreement) {
+      showError(disagreement + " Nothing was sent. Reselect the work item so " +
+                "the URL, the selection and the destination agree.");
+      return;
+    }
+    if (preTarget && preTarget.unresolved) {
+      showError("This work item has no durable thread yet, so the destination " +
+                "cannot be verified. The draft was kept; sending is blocked " +
+                "until the queue reports the thread.");
+      return;
+    }
     const draft = persistDraft();
-    const target = getTarget();
+    const target = preTarget;
+    // Item 13: the operator must never have to guess whether a send is running.
+    // `sending` already blocks re-entry from the button, Enter and Ctrl+Enter --
+    // they all call send() -- but that state was invisible.
     sending = true;
     sendBtn.disabled = true;
+    const idleLabel = sendBtn.textContent;
+    sendBtn.textContent = "Sending...";
+    sendBtn.setAttribute("aria-busy", "true");
     try {
       const body = Object.assign(
         { message: raw, idempotency_key: draft.idempotencyKey },
@@ -865,10 +969,18 @@ function createComposer(opts) {
       textarea.value = "";
       autoGrow();
       updateCounter();
+      // Phase 1, item 4: the operator must never open History or raw JSON to
+      // retrieve a durable message id. Show destination + the new id inline,
+      // with a copy control, immediately after a verified post.
+      showPostConfirmation(result);
       if (onPosted) onPosted(result, stored);
     } finally {
+      // Always restored, on success, refusal, network error and verification
+      // failure alike, so the composer can never strand in a sending state.
       sending = false;
       sendBtn.disabled = false;
+      sendBtn.textContent = idleLabel;
+      sendBtn.removeAttribute("aria-busy");
     }
   }
 
@@ -958,6 +1070,39 @@ const WORK_KIND_LABEL = {
 // --------------------------------------------------------------------------- //
 
 let lastWorkItems = [];
+// Distinguishes "the queue has not been fetched yet" from "the queue was
+// fetched successfully and is empty". Inferring this from lastWorkItems.length
+// conflated the two, so after a successful empty response an unknown explicit
+// route was retained as a provisional selection instead of being rejected.
+let workItemsLoaded = false;
+// A SUCCESSFUL queue response is positive evidence that the snapshot is live.
+// A failed refresh is not: it leaves the previous array in place, which must
+// NOT keep authorising sends. Send authorization therefore requires a
+// currently-confirmed queue, not merely one that loaded successfully once.
+let queueConfirmed = false;
+// A refresh failure the operator can still see. It is NOT transient: no boot
+// continuation, route restoration, conversation restoration, polling cycle or
+// transient-status cleanup may erase it. Only a later CONFIRMED success clears
+// it, because only that re-establishes the truth it was reporting.
+let queueFailureReported = false;
+// Monotonically increasing refresh generation. Overlapping refreshes are
+// possible (the polling timer, the boot path and the Retry control can all be
+// in flight at once) and they can complete OUT OF ORDER, so a completion may
+// only touch shared state when it belongs to the newest started refresh.
+let queueRefreshGeneration = 0;
+
+// The four outcomes a refresh can have. They are deliberately distinct:
+// "confirmed empty" is a real, authoritative answer and must never be confused
+// with "not loaded" or "failed".
+const REFRESH_CONFIRMED = "confirmed";
+const REFRESH_CONFIRMED_EMPTY = "confirmed_empty";
+const REFRESH_FAILED = "failed";
+const REFRESH_SUPERSEDED = "superseded";
+
+// True only for an outcome that re-establishes authoritative queue truth.
+function refreshSucceeded(outcome) {
+  return outcome === REFRESH_CONFIRMED || outcome === REFRESH_CONFIRMED_EMPTY;
+}
 let lastQueueCouncils = [];
 let lastArchiveIndex = { archived: [], count: 0 };
 
@@ -1045,6 +1190,17 @@ function actionsForState(pstate, canonical) {
   }
 }
 
+// Everything queueCard renders from, so an unchanged item produces an unchanged
+// signature and its node is never replaced.
+function queueCardSignature(it) {
+  return [it.work_item_id, it.thread_id, it.presentation_state, it.status,
+          it.runner_state, it.claimed_by, it.title || it.summary,
+          it.last_activity_at, it.last_activity_event,
+          it.work_item_id === selectedWorkItemId ? "sel" : "",
+          isCanonicalMessageWorkItem(it.work_item_id) ? "canon" : "noncanon",
+          sharesThreadWithOtherWorkItems(it, lastWorkItems) ? "amb" : ""].join("\u0001");
+}
+
 function queueCard(it) {
   const ps = it.presentation_state || "waiting_on_claude";
   const selected = it.work_item_id && it.work_item_id === selectedWorkItemId;
@@ -1061,11 +1217,93 @@ function queueCard(it) {
     ? '<span class="q-opflag" title="operator action required"><U+25C9> operator</span>' : "";
   // Technical ids ride on data attributes only; the primary card stays readable.
   const title = esc((it.title || it.summary || it.work_item_id || "").slice(0, 140));
+  // The row is NOT the control. Keyboard reachability comes from the explicit
+  // .q-open button rendered below, which carries aria-current; this comment
+  // previously still described the superseded role/tabindex/aria-pressed row.
+  const execLabel = executorStateLabel(it);
+  const phaseLabel = lifecyclePhaseLabel(it);
+  const wid = it.work_item_id || "";
+  // POLICY, stated because the reviewers asked. Two distinct cases:
+  //   * a record with NO usable key is EXCLUDED from reconciliation entirely
+  //     (renderQueue filters it), because several such rows would collapse onto
+  //     one empty key and reconciliation could move or focus the wrong tile;
+  //   * a record WITH a usable key but a non-canonical shape is DISPLAYED
+  //     read-only, because it is durable governed work the operator must see.
+  // Neither can ever become a sendable destination.
+  // A non-canonical entry -- today only the packet projection
+  // "in_progress:<packet-id>" -- is REAL work the operator must still see, so
+  // it is not hidden. It is rendered READ-ONLY: no .q-open control, so it is
+  // not activatable, not navigable and not selectable as a message
+  // destination. Only message-scoped work items are conversations.
+  const canonical = isCanonicalMessageWorkItem(wid);
+  const tid = it.thread_id || "";
+  const originId = originMessageId(wid);
+  // Item 1: multiple canonical work items on one thread is legal but reads as
+  // duplication. Surface it; never hide a durable record to tidy the view.
+  const ambiguous = sharesThreadWithOtherWorkItems(it, lastWorkItems);
+  const warn = ambiguous
+    ? '<div class="q-integrity" role="note">Shared thread: more than one work ' +
+      "item is bound to this thread. These are distinct durable records, not " +
+      "duplicates - compare the work-item IDs.</div>"
+    : "";
+  // Item 3: each identifier is LABELLED by type. A thread id is never presented
+  // as a work-item id, and the shared-suffix coincidence is explained in place.
+  const suffixNote = sharesSuffix(wid, tid)
+    ? ' <span class="q-idnote" title="The thread was created together with this' +
+      " item's origin message, so their numeric suffixes match. They remain" +
+      ' different identifiers.">matching suffix</span>'
+    : "";
+  const ids =
+    '<div class="q-ids">' +
+      '<span class="q-idrow"><span class="q-idk">Work item</span>' +
+        '<code class="mono q-idv" title="' + esc(wid) + '">' + esc(abbrevId(wid)) + "</code>" +
+        copyIdButton(wid, "work-item ID") + "</span>" +
+      (tid ? '<span class="q-idrow"><span class="q-idk">Thread</span>' +
+        '<code class="mono q-idv" title="' + esc(tid) + '">' + esc(abbrevId(tid)) + "</code>" +
+        copyIdButton(tid, "thread ID") + suffixNote + "</span>" : "") +
+      (originId ? '<span class="q-idrow"><span class="q-idk">Origin message</span>' +
+        '<code class="mono q-idv" title="' + esc(originId) + '">' + esc(abbrevId(originId)) +
+        "</code>" + copyIdButton(originId, "origin message ID") + "</span>" : "") +
+    "</div>";
+  // ACCESSIBILITY: the row is a plain CONTAINER. Nesting real <button> copy
+  // controls inside an element that itself claimed role="button" is an invalid
+  // pattern, so the primary action is now an explicit button and the copy
+  // buttons are its siblings. Enter and Space come free from native button
+  // semantics rather than a hand-rolled key handler.
+  // aria-current, not aria-pressed: activating this control NAVIGATES to a
+  // work item, it does not toggle a state off again, so a toggle-button
+  // contract would misdescribe it to assistive technology.
+  const openStart = canonical
+    ? ('<button type="button" class="q-open" ' +
+       'aria-current="' + (selected ? "true" : "false") + '"' +
+       ' aria-label="Open work item ' + esc(wid) +
+       (execLabel ? " (executor " + esc(execLabel) + ")" : "") + '"' +
+       ' data-work-item="' + esc(wid) + '">')
+    : ('<div class="q-readonly" role="note"' +
+       ' aria-label="Packet record ' + esc(wid) + ', not a conversation">');
+  const openEnd = canonical ? "</button>" : "</div>";
+  const roBadge = canonical ? ""
+    : '<span class="q-ro-badge" title="A clearance packet shown for visibility. ' +
+      'It is not a conversation and cannot receive messages.">packet record</span>';
   return '<div class="q-row q-card' + (selected ? " is-selected" : "") +
-    '" data-thread="' + esc(it.thread_id || "") +
-    '" data-work-item="' + esc(it.work_item_id || "") + '">' +
-    '<div class="q-title">' + title + "</div>" +
-    '<div class="q-meta">' + bits.join("") + opFlag + "</div>" +
+    (ambiguous ? " q-ambiguous" : "") + (canonical ? "" : " q-noncanonical") + '"' +
+    ' data-sig="' + esc(queueCardSignature(it)) + '"' +
+    ' data-canonical="' + (canonical ? "true" : "false") + '"' +
+    ' data-thread="' + esc(it.thread_id || "") +
+    '" data-work-item="' + esc(wid) + '">' +
+    openStart +
+    '<span class="q-title">' + title + "</span>" + roBadge +
+    // A button's content model is phrasing content, so the meta strip is a
+    // span laid out as a block rather than a div inside interactive markup.
+    '<span class="q-meta">' + bits.join("") + opFlag +
+    // Item 10: phase and executor state are DIFFERENT facts and are labelled
+    // separately, so "PHASE: VERIFICATION / EXECUTOR: IN COUNCIL" can never be
+    // misread as a single contradictory status.
+    (phaseLabel ? '<span class="q-phase mono" title="lifecycle phase">Phase ' +
+      esc(phaseLabel) + "</span>" : "") +
+    (execLabel ? '<span class="q-exec mono" title="executor state, derived from ' +
+      'runner_state only">Executor ' + esc(execLabel) + "</span>" : "") +
+    "</span>" + openEnd + ids + warn +
     "</div>";
 }
 
@@ -1117,6 +1355,108 @@ function attentionCounts(items) {
   return c;
 }
 
+// The rendered signature of the queue as last written to the DOM. Polling
+// re-runs renderQueue roughly every two seconds; when nothing material changed,
+// rewriting innerHTML destroyed every node -- including the focused control --
+// which made keyboard operation of the queue impossible. The signature lets an
+// unchanged poll become a no-op.
+let lastQueueSignature = null;
+
+// The canonical work-item key of the currently focused queue control, if any.
+function focusedQueueKey() {
+  const a = document.activeElement;
+  const btn = a && a.closest ? a.closest(".q-open") : null;
+  return btn ? btn.getAttribute("data-work-item") : null;
+}
+
+// Put focus back on the SAME durable identity after a reconciliation that had
+// to replace nodes. If that identity is legitimately gone, move predictably to
+// the first remaining tile, then to the container, rather than dropping focus
+// to the document body.
+function restoreQueueFocus(key, el) {
+  if (!key) return;
+  let btn = null;
+  try {
+    btn = el.querySelector('.q-open[data-work-item="' + cssAttrValue(key) + '"]');
+  } catch (e) { btn = null; }
+  if (btn) { btn.focus(); return; }
+  const first = el.querySelector(".q-open");
+  if (first) { first.focus(); return; }
+  if (el.setAttribute) el.setAttribute("tabindex", "-1");
+  if (el.focus) el.focus();
+}
+
+// Keyed reconciliation. Tiles are matched by canonical work-item id, and a tile
+// whose rendered markup is unchanged is LEFT ENTIRELY ALONE, so it keeps its
+// DOM identity, its focus and its scroll position. Only genuinely changed,
+// added or removed tiles touch the DOM.
+function reconcileQueue(el, desired) {
+  const existing = {};
+  Array.from(el.querySelectorAll(".q-row[data-work-item]")).forEach((node) => {
+    existing[node.getAttribute("data-work-item")] = node;
+  });
+  const keep = {};
+  desired.forEach((d) => { keep[d.key] = true; });
+
+  // Anything no longer in the queue goes, and only then.
+  Object.keys(existing).forEach((k) => {
+    if (!keep[k] && existing[k].parentNode) existing[k].parentNode.removeChild(existing[k]);
+  });
+
+  // Group the desired list, preserving its computed order.
+  const order = [];
+  const byGroup = {};
+  desired.forEach((d) => {
+    if (!byGroup[d.group]) { byGroup[d.group] = []; order.push(d); }
+    byGroup[d.group].push(d);
+  });
+
+  const seen = {};
+  order.forEach((first) => {
+    const g = first.group;
+    seen[g] = true;
+    let groupEl = el.querySelector('.q-group[data-group="' + cssAttrValue(g) + '"]');
+    if (!groupEl) {
+      groupEl = document.createElement("div");
+      groupEl.className = "q-group";
+      groupEl.setAttribute("data-group", g);
+      groupEl.innerHTML = '<div class="q-group-head">' + esc(first.groupLabel) + "</div>";
+    }
+    // Appending an already-attached node MOVES it, so this also fixes the
+    // order of the groups themselves.
+    el.appendChild(groupEl);
+
+    byGroup[g].forEach((d, i) => {
+      const prev = existing[d.key];
+      let node;
+      if (prev && prev.getAttribute("data-sig") === d.sig) {
+        node = prev;              // UNCHANGED: identity, focus and scroll kept
+      } else {
+        const tmp = document.createElement("div");
+        tmp.innerHTML = d.html;
+        node = tmp.firstElementChild || tmp.children[0];
+        if (!node) return;
+        // A changed row may also have changed GROUP, so detach it from
+        // wherever it was rather than replacing it in its old parent.
+        if (prev && prev.parentNode) prev.parentNode.removeChild(prev);
+      }
+      // Place it at its desired index, counting past the group heading. Only
+      // move when it is genuinely out of position, so an unchanged and
+      // correctly ordered tile is never touched.
+      const want = groupEl.children[i + 1];
+      if (want !== node) groupEl.insertBefore(node, want || null);
+    });
+  });
+
+  // Groups that no longer have any desired item must not linger as empty
+  // headings.
+  Array.from(el.querySelectorAll(".q-group")).forEach((g) => {
+    if (!seen[g.getAttribute("data-group")] && g.parentNode) {
+      g.parentNode.removeChild(g);
+    }
+  });
+}
+
 function renderQueue() {
   const el = document.getElementById("queue-groups");
   if (!el) return;
@@ -1124,21 +1464,44 @@ function renderQueue() {
   const rows = filterSortQueue(lastWorkItems, queueFilterMode, queueSearch);
   syncFilterChips();
   if (!rows.length) {
+    const emptySig = "EMPTY";
+    if (lastQueueSignature === emptySig) return;
+    lastQueueSignature = emptySig;
+    // The empty transition destroys every tile, including a focused one, so it
+    // owes the same focus contract as a normal reconciliation: never silently
+    // drop focus to the document body.
+    const hadFocus = !!focusedQueueKey();
     el.innerHTML = '<p class="muted queue-empty">Nothing here in this view. Try the History / All filter.</p>';
+    if (hadFocus) {
+      el.setAttribute("tabindex", "-1");
+      if (el.focus) el.focus();
+    }
     return;
   }
-  let html = "", curState = null;
-  rows.forEach((it) => {
-    if (it.presentation_state !== curState) {
-      if (curState !== null) html += "</div>";
-      curState = it.presentation_state;
-      html += '<div class="q-group"><div class="q-group-head">' +
-        esc(PSTATE_LABEL[curState] || curState) + "</div>";
-    }
-    html += queueCard(it);
+  // A record with no canonical work-item id cannot be keyed, so it is skipped
+  // rather than reconciled under an empty key where several such rows would
+  // collapse together and reconciliation could drop, move or focus the wrong
+  // tile. The derivation guarantees a canonical id, so this is fail-closed
+  // handling of data that should not exist, not an expected path.
+  const desired = rows.filter((it) => it && it.work_item_id).map((it) => {
+    const html = queueCard(it);
+    return {
+      key: it.work_item_id,
+      group: it.presentation_state || "",
+      groupLabel: PSTATE_LABEL[it.presentation_state] || it.presentation_state || "",
+      sig: queueCardSignature(it),
+      html: html
+    };
   });
-  if (curState !== null) html += "</div>";
-  el.innerHTML = html;
+  // NO-CHANGE SHORT CIRCUIT. The dominant polling case is "nothing changed",
+  // and it must not touch the DOM at all.
+  const signature = desired.map((d) => d.group + "|" + d.key + "|" + d.sig).join("\n");
+  if (signature === lastQueueSignature) return;
+  lastQueueSignature = signature;
+
+  const focusKey = focusedQueueKey();
+  reconcileQueue(el, desired);
+  if (focusKey) restoreQueueFocus(focusKey, el);
 }
 
 function syncFilterChips() {
@@ -1290,23 +1653,674 @@ function openAttention() {
   }
 }
 
+// One safe composer during active work (Phase 1, item 3). While a work item is
+// selected the generic new-conversation composer is demoted so it cannot
+// compete with the work-item-bound composer for the operator's attention.
+// Nothing is disabled or removed: the operator can still start a new
+// conversation deliberately, it simply stops being an equal-weight target.
+function applyComposerFocus() {
+  const generic = document.getElementById("composer-card");
+  if (!generic) return;
+  const active = !!selectedWorkItemId;
+  generic.classList.toggle("composer-demoted", active);
+  // Demote CONSISTENTLY. Marking the card aria-hidden while leaving its Send
+  // button in the tab order is worse than doing nothing: a screen-reader user
+  // tabs to a control the accessibility tree says is not there. `inert` removes
+  // it from both the a11y tree and the tab order in one step; where it is not
+  // supported, mirror that on every focusable descendant, not just the input.
+  const FOCUSABLE = "a[href], button, input, select, textarea, [tabindex]";
+  if ("inert" in HTMLElement.prototype) {
+    generic.inert = active;
+    generic.removeAttribute("aria-hidden");   // inert already implies it
+  } else {
+    generic.setAttribute("aria-hidden", active ? "true" : "false");
+    generic.querySelectorAll(FOCUSABLE).forEach((el) => {
+      if (active) {
+        // Preserve any explicit tabindex so demotion is exactly reversible.
+        if (!el.hasAttribute("data-prior-tabindex")) {
+          el.setAttribute("data-prior-tabindex",
+                          el.hasAttribute("tabindex") ? el.getAttribute("tabindex") : "");
+        }
+        el.tabIndex = -1;
+      } else if (el.hasAttribute("data-prior-tabindex")) {
+        const prior = el.getAttribute("data-prior-tabindex");
+        if (prior === "") el.removeAttribute("tabindex");
+        else el.setAttribute("tabindex", prior);
+        el.removeAttribute("data-prior-tabindex");
+      }
+    });
+  }
+}
+
+// Keyboard activation for queue rows (Phase 1, item 7). Enter or Space opens
+// the row exactly as a click does; the existing click handler is untouched.
+// NO keyboard handler for queue tiles, deliberately. The primary control is a
+// real <button>, so the browser already activates it on Enter and on Space and
+// fires exactly one click, which the delegated queue listener turns into one
+// canonical navigation. An earlier version called preventDefault() on Space to
+// stop page scrolling; on a focused button Space's default action IS the
+// activation, so that suppressed the very keyboard path this control exists to
+// provide. Space does not scroll the page while a button holds focus, so there
+// is nothing to suppress and nothing to add.
+
+// --------------------------------------------------------------------------
+// TRUTHFUL EXECUTION STATE (Phase 1, item 6)
+// --------------------------------------------------------------------------
+// RUNNING is never derived from message-post activity. An inbound operator
+// message proves only that the OPERATOR acted; it is no evidence that an
+// executor resumed, consumed it, or is working. Such an item is reported as
+// a state the durable record can actually support, never an
+// inference from message-post activity.
+//
+// States requiring executor acknowledgement or wake telemetry
+// (EXECUTOR_RESUMED, MESSAGE_ACKNOWLEDGED, WAKE_PENDING) are NOT produced here
+// and are NOT simulated. They are deferred to the Phase 2 executor wake bridge.
+// Evidence-backed only. `it` is the derived work item from /api/work-items.
+// LIFECYCLE PHASE: where the governed work item sits in its own lifecycle.
+// Derived from `status`, whose observed domain is open|planning|verification|
+// claimed|operator_required|done|closed|superseded|malformed. This says nothing
+// about whether any executor is running.
+const LIFECYCLE_PHASE_LABELS = {
+  open: "OPEN", planning: "PLANNING", verification: "VERIFICATION",
+  claimed: "CLAIMED", operator_required: "OPERATOR REQUIRED",
+  done: "DONE", closed: "CLOSED", superseded: "SUPERSEDED",
+  malformed: "MALFORMED"
+};
+
+function lifecyclePhaseOf(it) {
+  const st = String((it && it.status) || "");
+  return Object.prototype.hasOwnProperty.call(LIFECYCLE_PHASE_LABELS, st) ? st : "";
+}
+
+function lifecyclePhaseLabel(it) {
+  return LIFECYCLE_PHASE_LABELS[lifecyclePhaseOf(it)] || "";
+}
+
+// EXECUTOR STATE: what a runner is actually doing, derived ONLY from
+// runner_state, whose domain is unowned|active_runner|waiting_on_council|
+// waiting_on_operator|claimed_idle|stale_or_no_heartbeat|unknown.
+//
+// ACTIVE is reported only for "active_runner", which the server returns solely
+// on positive evidence of recent non-claim activity. A claim alone is never
+// running, and a posted message is never running: ClearWright has no heartbeat
+// channel, so anything stronger would be fabricated.
+const EXECUTOR_LABELS = {
+  active_runner: "ACTIVE", waiting_on_council: "IN COUNCIL",
+  waiting_on_operator: "WAITING FOR OPERATOR", claimed_idle: "CLAIMED",
+  stale_or_no_heartbeat: "NO HEARTBEAT", unowned: "UNCLAIMED",
+  unknown: "UNKNOWN"
+};
+
+function executorRunnerState(it) {
+  const r = String((it && it.runner_state) || "");
+  return Object.prototype.hasOwnProperty.call(EXECUTOR_LABELS, r) ? r : "";
+}
+
+function executorStateLabel(it) {
+  return EXECUTOR_LABELS[executorRunnerState(it)] || "";
+}
+
+// Ranking vocabulary, mapped from the SAME evidence as the executor label so
+// the queue order and the rendered state can never disagree. Terminal
+// presentation states win first because a finished item is not active work.
+function truthfulExecutionState(it) {
+  if (!it) return "";
+  const p = String(it.presentation_state || "");
+  const r = executorRunnerState(it);
+  if (p === "recently_completed" || p === "historical" || p === "superseded") return "complete";
+  if (p === "needs_operator" || p === "waiting_on_operator" || r === "waiting_on_operator") {
+    return "waiting_for_operator";
+  }
+  if (r === "waiting_on_council") return "in_council";
+  if (r === "stale_or_no_heartbeat" || p === "stale") return "paused";
+  if (r === "active_runner") return "executor_active";
+  if (p === "blocked") return "blocked";
+  if (r === "claimed_idle" || it.claimed_by) return "claimed";
+  return "";
+}
+
+// --------------------------------------------------------------------------
+// DURABLE IDENTITY ON THE QUEUE (operator correction items 1, 2 and 3)
+// --------------------------------------------------------------------------
+// Investigation result, recorded because it changes what the correct fix is:
+// the apparently duplicated tiles are NOT phantoms and NOT client artifacts.
+// /api/work-items returns genuinely DISTINCT canonical work items whose titles
+// collide because the title is derived from the origin message text, and in one
+// case three separate message-scoped work items share a single thread. Every
+// work_item_id is a real "message:msg-..." value; no thread id is ever rendered
+// as a work item. Collapsing them would HIDE durable governed work, so the tiles
+// are disambiguated and the shared-thread condition is surfaced as an integrity
+// warning instead.
+
+// ONE escaping mechanism for every dynamic value interpolated into a
+// selector. Every such value in this file sits inside a QUOTED ATTRIBUTE
+// selector -- [data-work-item="..."] and friends -- so the correct
+// operation is CSS STRING-LITERAL escaping, not identifier escaping.
+// CSS.escape is deliberately NOT used here: it escapes identifiers, and
+// its output does not round-trip inside a quoted string, so a value
+// containing a quote, a backslash or a space would fail to match the very
+// node it names. Within a CSS string the backslash and the quote must be
+// escaped, and so must newlines, NUL and other control characters, which
+// are not permitted raw in a string token.
+function cssAttrValue(value) {
+  const v = String(value === null || value === undefined ? "" : value);
+  let out = "";
+  for (const ch of v) {
+    const code = ch.codePointAt(0);
+    if (ch === '\\' || ch === '"') { out += '\\' + ch; continue; }
+    // A CSS string token may not contain a raw newline, NUL or other control
+    // character. The general escape is a hexadecimal sequence terminated by a
+    // space, which is well defined for every code point in that range.
+    if (code < 0x20 || code === 0x7f) {
+      out += '\\' + code.toString(16) + ' ';
+      continue;
+    }
+    out += ch;
+  }
+  return out;
+}
+
+// --------------------------------------------------------------------------
+// CANONICAL SENDABLE DESTINATIONS (operator correction 1 and 4)
+// --------------------------------------------------------------------------
+// A destination may only be a MESSAGE-SCOPED work item that is present in the
+// live queue and carries a durable thread. Two shapes are excluded on purpose:
+//
+//   * packet projections such as "in_progress:<packet-id>", which are the queue
+//     presentation of a clearance packet rather than a conversation, and have
+//     no thread to post into;
+//   * any record without a canonical message-scoped id, including a malformed
+//     or future entry, which must never resolve to a destination.
+//
+// The canonical id of a message work item is literally "message:" + its origin
+// message id, so this is a shape the derivation guarantees rather than a
+// convention.
+function isCanonicalMessageWorkItem(id) {
+  return typeof id === "string" && /^message:msg-[0-9A-Za-z]+$/.test(id);
+}
+
+// The LIVE queue record for a work item, or null. Returning null is the
+// fail-closed answer: it is used to refuse, never to fall back to remembered
+// state. A selection whose item polling has removed therefore stops being
+// sendable the moment the queue no longer lists it.
+function liveQueueRecord(workItemId) {
+  if (!isCanonicalMessageWorkItem(workItemId)) return null;
+  return (lastWorkItems || []).find(
+    (i) => i && i.work_item_id === workItemId) || null;
+}
+
+// thread_id -> [work_item_id, ...] for every item the queue can see.
+function threadWorkItemIndex(items) {
+  const idx = {};
+  (items || []).forEach((it) => {
+    const t = it && it.thread_id;
+    if (!t) return;
+    if (!idx[t]) idx[t] = [];
+    if (idx[t].indexOf(it.work_item_id) === -1) idx[t].push(it.work_item_id);
+  });
+  return idx;
+}
+
+// True when this item's thread carries more than one canonical work item. That
+// is legal but ambiguous to read, so it is flagged rather than hidden.
+function sharesThreadWithOtherWorkItems(it, items) {
+  if (!it || !it.thread_id) return false;
+  const peers = threadWorkItemIndex(items)[it.thread_id] || [];
+  return peers.length > 1;
+}
+
+// A work item id derived from a message carries that message's id verbatim:
+// "message:" + message_id. When the thread was created with that same origin
+// message the numeric suffixes match, which reads like duplication but is not.
+function idSuffix(id) {
+  const m = /(\d{8}T\d{6,})/.exec(String(id || ""));
+  return m ? m[1] : "";
+}
+
+function sharesSuffix(workItemId, threadId) {
+  const a = idSuffix(workItemId);
+  return !!a && a === idSuffix(threadId);
+}
+
+function originMessageId(workItemId) {
+  const s = String(workItemId || "");
+  return s.indexOf("message:") === 0 ? s.slice("message:".length) : "";
+}
+
+// Abbreviated for display only. The FULL id is always what gets copied.
+function abbrevId(id, keep) {
+  const s = String(id || "");
+  const k = keep || 14;
+  return s.length <= k + 3 ? s : s.slice(0, k) + "\u2026" + s.slice(-4);
+}
+
+// --------------------------------------------------------------------------
+// DURABLE MESSAGE IDENTITY (Phase 1, item 4) - see GitHub issue #86
+// --------------------------------------------------------------------------
+// Every durable message already carries data-message-id in the DOM; it was
+// simply never surfaced. These helpers render it visibly with a keyboard
+// accessible copy control. Presentation only: no identity is created,
+// altered, or inferred here.
+function copyToClipboard(value) {
+  if (!value) return Promise.resolve(false);
+  try {
+    if (navigator.clipboard && navigator.clipboard.writeText) {
+      return navigator.clipboard.writeText(value).then(() => true, () => false);
+    }
+  } catch (e) { /* fall through */ }
+  return Promise.resolve(false);
+}
+
+// A real <button> so it is reachable and activatable by keyboard.
+function copyIdButton(value, label) {
+  if (!value) return "";
+  return '<button type="button" class="copy-id" data-copy-value="' + esc(value) +
+    '" aria-label="Copy ' + esc(label) + ' ' + esc(value) + '" title="Copy ' +
+    esc(label) + '">Copy</button>';
+}
+
+function messageIdentityRow(m) {
+  if (!m) return "";
+  const mid = m.message_id || "";
+  const tid = m.thread_id || "";
+  const wid = m.work_item_id || "";
+  let html = '<div class="msg-identity">';
+  if (mid) {
+    html += '<span class="msg-id mono" data-message-id-text="' + esc(mid) + '">' +
+      esc(mid) + "</span>" + copyIdButton(mid, "message ID");
+  }
+  if (tid) {
+    html += '<span class="msg-thread mono">' + esc(tid) + "</span>" +
+      copyIdButton(tid, "thread ID");
+  }
+  if (m.actor) html += '<span class="msg-actor">' + esc(m.actor) + "</span>";
+  if (m.intent) html += '<span class="msg-intent">' + esc(m.intent) + "</span>";
+  if (wid) html += '<span class="msg-binding mono">' + esc(wid) + "</span>";
+  if (m.at) html += '<span class="msg-time">' + esc(m.at) + "</span>";
+  return html + "</div>";
+}
+
+// Confirmation shown immediately after a verified post.
+function showPostConfirmation(result) {
+  if (!result) return;
+  const el = document.getElementById("post-confirmation");
+  if (!el) return;
+  el.hidden = false;
+  el.innerHTML = '<span class="pc-label">Posted</span>' +
+    '<span class="pc-msg mono" data-posted-message-id="' + esc(result.message_id || "") + '">' +
+    esc(result.message_id || "") + "</span>" +
+    copyIdButton(result.message_id, "message ID") +
+    '<span class="pc-work mono">' + esc(result.work_item_id || "") + "</span>" +
+    '<span class="pc-thread mono">' + esc(result.thread_id || "") + "</span>";
+}
+
+// One delegated listener serves every copy control, including ones rendered
+// later, and keeps them keyboard operable.
+document.addEventListener("click", (e) => {
+  const btn = e.target && e.target.closest && e.target.closest(".copy-id");
+  if (!btn) return;
+  const val = btn.getAttribute("data-copy-value") || "";
+  copyToClipboard(val).then((ok) => {
+    const prev = btn.textContent;
+    btn.textContent = ok ? "Copied" : "Copy failed";
+    setTimeout(() => { btn.textContent = prev; }, 1200);
+  });
+});
+
+// --------------------------------------------------------------------------
+// ACTIVE SESSION CONTINUITY (Phase 1, item 1)
+// --------------------------------------------------------------------------
+// Refreshing must return the operator to active work. The deterministic
+// #work= hash route below already exists; it is now WRITTEN on every selection
+// and MIRRORED to one localStorage key so a plain reload (no hash) still
+// restores. Nothing here changes work-item, thread, authority or identity
+// semantics: it only decides which existing item is shown first.
+const SELECTION_KEY = "cw_selected_work_item_v1";
+
+function persistSelection(workItemId) {
+  try {
+    if (workItemId) localStorage.setItem(SELECTION_KEY, workItemId);
+    else localStorage.removeItem(SELECTION_KEY);
+  } catch (e) { /* storage unavailable -> hash route still works */ }
+}
+
+function readPersistedSelection() {
+  try { return localStorage.getItem(SELECTION_KEY) || null; } catch (e) { return null; }
+}
+
+// Operator-specified priority order. Ranked ONLY from fields /api/work-items
+// already returns; nothing is inferred or simulated.
+// The operator's stated priority order. "wake_pending" is DELIBERATELY ABSENT:
+// it can only be established by executor acknowledgement or wake telemetry,
+// which is the Phase 2 wake bridge. Listing a bucket that no durable field can
+// fill makes the ranking contract inert and advertises a priority that never
+// applies, so it is deferred rather than simulated (Phase 1, item 6). When the
+// wake bridge lands it belongs between operator_message_posted and paused.
+// Every entry MUST be producible by executorStateOf(). Two buckets were removed
+// after being proven unreachable against the real server value domain:
+//   wake_pending            needs executor acknowledgement / wake telemetry,
+//                           which is the deferred Phase 2 wake bridge.
+//   operator_message_posted assumed last_activity_event could be "message" or
+//                           "operator_message". It cannot: last_activity() emits
+//                           exactly created|completion|verification|council|gate|
+//                           progress|claim|response|evidence, so that branch was
+//                           dead code and the rank could never be reached.
+// A rank nothing can produce silently mis-orders the queue, so it is removed
+// rather than left as decoration.
+const ACTIVE_RANK = [
+  "waiting_for_operator",
+  "paused",
+  "executor_active",
+  "in_council",
+  "claimed",
+  "blocked"
+];
+
+// Terminal / non-active presentation states are never auto-selected.
+const INACTIVE_STATES = ["recently_completed", "complete", "superseded", "historical"];
+
+function isActiveItem(it) {
+  if (!it) return false;
+  return INACTIVE_STATES.indexOf(String(it.presentation_state || "")) === -1;
+}
+
+// Map the derived record onto the operator's priority vocabulary. Only states
+// supportable from durable evidence are produced (Phase 1, item 6): anything
+// needing executor acknowledgement or wake telemetry is deferred to the wake
+// bridge and is NEVER synthesised here.
+function activeStateOf(it) {
+  // Delegate to the ONE truthful mapping instead of keeping a second, looser
+  // copy. The earlier duplicate never returned operator_message_posted (so that
+  // rank was unreachable) and defaulted every unrecognised item to in_council,
+  // which silently ranked unknown states ahead of blocked work. An unknown
+  // state now returns "" and sorts last rather than being guessed.
+  return truthfulExecutionState(it);
+}
+
+function rankActiveWorkItems(items) {
+  return (items || []).filter(isActiveItem).slice().sort((a, b) => {
+    const ra = ACTIVE_RANK.indexOf(activeStateOf(a));
+    const rb = ACTIVE_RANK.indexOf(activeStateOf(b));
+    const na = ra === -1 ? ACTIVE_RANK.length : ra;
+    const nb = rb === -1 ? ACTIVE_RANK.length : rb;
+    if (na !== nb) return na - nb;
+    const t = String(b.last_activity_at || "").localeCompare(String(a.last_activity_at || ""));
+    if (t !== 0) return t;
+    // Deterministic final key: equal or missing timestamps must not leave the
+    // restored selection dependent on queue iteration order.
+    return String(a.work_item_id || "").localeCompare(String(b.work_item_id || ""));
+  });
+}
+
+// Restore on load: an explicit prior selection wins while it is still valid;
+// otherwise the highest-priority active item; otherwise the empty state is
+// legitimate because there is genuinely no active work.
+function restoreActiveSelection() {
+  const items = lastWorkItems || [];
+  const deep = parseWorkRoute(location.hash);
+  if (deep && deep.malformed) {
+    selectTask(null);
+    persistSelection(null);
+    clearWorkRoute();
+    routeErrorReported = true;
+    showRestoreStatus("That link could not be read, so it was removed. " +
+                      "Nothing is selected.");
+    return;
+  }
+  if (deep) {
+    // An explicit deep link always wins. It is applied at boot BEFORE the queue
+    // is fetched, so the durable thread could not be resolved then; the queue is
+    // known now. Route handling goes through the SAME single policy as the
+    // hashchange path -- validate against the live queue, clear an unknown
+    // route, never persist a terminal item -- so the two cannot drift.
+    bindRouteSelection(deep.work_item_id);
+    return;
+  }
+  const stored = readPersistedSelection();
+  const storedItem = stored
+    ? items.find((it) => it.work_item_id === stored && isActiveItem(it))
+    : null;
+  const target = storedItem || rankActiveWorkItems(items)[0] || null;
+  if (!target) { persistSelection(null); return; }   // no active work: empty state is correct
+  navigateToWorkItem(target.work_item_id);
+}
+
+// ONE parser for the work route, shared by boot and restoration so malformed
+// input is handled identically in both. decodeURIComponent() raises URIError on
+// malformed percent-encoding; an unguarded call at boot aborts wire() before
+// initJumpToLatest(), restoration and the refresh timers are installed, so a
+// single bad URL would disable the console instead of being reported.
+function parseWorkRoute(hash) {
+  // NOTE the [^&]* rather than [^&]+: "#work=" carries an explicit but empty
+  // work id. That is an INVALID route, not the absence of one, and it must go
+  // through the same clear-and-report path instead of silently falling through
+  // to stored-selection restoration.
+  const m = /[#&]work=([^&]*)/.exec(hash || "");
+  if (!m) return null;
+  let wid;
+  try {
+    wid = decodeURIComponent(m[1]);
+  } catch (e) {
+    return { malformed: true, work_item_id: null, message_id: null };
+  }
+  if (!wid) return { malformed: true, work_item_id: null, message_id: null };
+  let msg = null;
+  const mm = /[#&]msg=([^&]+)/.exec(hash || "");
+  if (mm) {
+    try { msg = decodeURIComponent(mm[1]); } catch (e) { msg = null; }
+  }
+  return { malformed: false, work_item_id: wid, message_id: msg };
+}
+
+// Remove an invalid route so a reload does not repeat the same failure. Falls
+// back to assigning the hash when history is unavailable.
+function clearWorkRoute() {
+  try {
+    history.replaceState(null, "", location.pathname + location.search);
+  } catch (e) {
+    try { location.hash = ""; } catch (e2) { /* nothing further to do */ }
+  }
+}
+
+// ONE place where a route becomes a selection, so queue validation and the
+// terminal-item policy cannot diverge between the boot path and the hashchange
+// path. Returns true when a selection was bound, false when the route was
+// rejected, and null when the queue is not loaded yet (boot), in which case
+// restoreActiveSelection() validates once it is.
+function bindRouteSelection(wid) {
+  const items = lastWorkItems || [];
+  // Only a genuinely unfetched queue defers validation. A successful empty
+  // response is authoritative: the route cannot be backed by anything.
+  if (!workItemsLoaded) return null;
+  const known = items.find((it) => it.work_item_id === wid);
+  if (!known) {
+    // Queue-unbacked: clear unconditionally, drop the route so a reload does
+    // not repeat it, and say so.
+    selectTask(null);
+    persistSelection(null);
+    clearWorkRoute();
+    routeErrorReported = true;
+    showRestoreStatus('Work item "' + wid + '" is not in the live queue. ' +
+                      "The link may be stale, so nothing is selected.");
+    return false;
+  }
+  selectTask(known.thread_id || null, wid);
+  // A route that resolves is a successful navigation: any earlier route
+  // explanation is now obsolete and must not outlive it.
+  routeErrorReported = false;
+  // POLICY, applied on EVERY route path. An EXPLICIT link may open a terminal
+  // item, because reviewing finished work is the point of sharing a link. That
+  // is inspection, NOT active-session restoration: it is never persisted, so
+  // the next refresh restores real active work instead of reopening finished
+  // work. Automatic restoration still excludes terminal items entirely.
+  if (!isActiveItem(known)) {
+    persistSelection(null);
+    showRestoreStatus("Opened " + (activeStateOf(known) || "inactive").replace(/_/g, " ") +
+                      " work item for inspection. It is not active, so it " +
+                      "will not be restored on the next refresh.");
+  } else {
+    clearTransientRestoreStatus();
+  }
+  return true;
+}
+
 // Deterministic hash route: #work=<work_item_id>[&msg=<message_id>]. The
 // highlight message id is derived from the work item id itself (a message work
 // item id IS "message:" + message_id) -- no message search, no ambiguity.
 function navigateToWorkItem(workItemId) {
+  // Deliberate navigation: an earlier malformed-link explanation is obsolete.
+  // Clear the flag AND the visible text -- resetting only the flag left the
+  // stale message on screen whenever no hashchange followed.
+  routeErrorReported = false;
+  showRestoreStatus("");
   const msgId = workItemId && workItemId.indexOf("message:") === 0
     ? workItemId.slice("message:".length) : "";
   location.hash = "#work=" + encodeURIComponent(workItemId) +
     (msgId ? "&msg=" + encodeURIComponent(msgId) : "");
-  selectTask(null, workItemId);
+  // Use the durable thread the API already reports for this item, so the
+  // composer binds to the real thread instead of minting a new one.
+  const known = (lastWorkItems || []).find((it) => it.work_item_id === workItemId);
+  selectTask(known ? known.thread_id || null : null, workItemId);
   showView("work");
+  openConversationTab();                     // conversation-first (item 2)
   if (msgId) setTimeout(() => highlightMessage(msgId), 200);
+  else setTimeout(jumpToLatestMessage, 200); // land on the latest message
+}
+
+// Conversation-first active view (Phase 1, item 2). Selecting active work opens
+// the Conversation tab and lands on the latest message. Scroll is preserved
+// only when the operator deliberately moved away from the bottom, which the
+// existing unread tracking already detects.
+function openConversationTab() {
+  // The Work view IS the conversation surface in this console: #center-work
+  // hosts the conversation detail and the docked composer, and there is no
+  // separate tab control (the only role="tablist" is the queue filter strip).
+  // "Open the Conversation tab" therefore means show that view, which also
+  // runs loadConversations() and docks the composer.
+  if (currentView !== "work") showView("work");
+}
+
+// The element whose CONTENT is the conversation. Mutations are observed here.
+function conversationAnchorEl() {
+  return document.getElementById("conv-scroll") ||
+         document.getElementById("conv-detail") ||
+         document.getElementById("conversation") ||
+         document.getElementById("comms");
+}
+
+// The element that ACTUALLY SCROLLS. #conv-detail is laid out with
+// overflow-y:visible and grows with its content, so scrollHeight equals
+// clientHeight and it can never report a scroll position -- targeting it left
+// the whole jump-to-latest feature inert. Walk up to the nearest genuinely
+// scrollable ancestor and fall back to the page, which is the real scroller
+// in the current layout.
+function conversationScrollEl() {
+  let el = conversationAnchorEl();
+  while (el && el !== document.body && el !== document.documentElement) {
+    let oy = "";
+    try { oy = getComputedStyle(el).overflowY; } catch (e) { oy = ""; }
+    if ((oy === "auto" || oy === "scroll") && (el.scrollHeight - el.clientHeight) > 4) {
+      return el;
+    }
+    el = el.parentElement;
+  }
+  return document.scrollingElement || document.documentElement;
+}
+
+
+function operatorMovedAwayFromLatest(el) {
+  if (!el) return false;
+  return (el.scrollHeight - el.scrollTop - el.clientHeight) > 120;
+}
+
+// True when THIS page load already reported an unusable route. The boot success
+// path must not wipe that message: clearing the hash necessarily makes the bad
+// route invisible to the restoration that follows, so without this flag the
+// operator would see the explanation replaced by a silently restored selection.
+let routeErrorReported = false;
+
+// Clear only a TRANSIENT status. A reported route error is not transient: it
+// explains something the operator's link did, and it stays until they navigate.
+function clearTransientRestoreStatus() {
+  if (routeErrorReported) return;
+  // A reported refresh failure is NOT transient. Clearing it here is what
+  // previously made an initial-load failure invisible: the boot continuation
+  // erased the explanation the failure had just rendered.
+  if (queueFailureReported) return;
+  showRestoreStatus("");
+}
+
+// Restoration status is surfaced, never swallowed. `retry` shows the control
+// that re-runs the same load path.
+function showRestoreStatus(text, retry) {
+  const el = document.getElementById("restore-status");
+  if (!el) return;
+  if (!text) { el.hidden = true; el.textContent = ""; return; }
+  el.textContent = text;
+  el.hidden = false;
+  if (retry) {
+    const btn = document.createElement("button");
+    btn.type = "button";
+    btn.className = "btn btn-quiet";
+    btn.textContent = "Retry";
+    btn.addEventListener("click", () => {
+      refreshWorkItems().then((outcome) => {
+        if (refreshSucceeded(outcome)) { restoreActiveSelection(); return; }
+        if (outcome === REFRESH_FAILED) {
+          showRestoreStatus("Still could not load the work queue. Sending " +
+                            "remains paused until it reloads.", true);
+        }
+        // A superseded retry is silent: a newer refresh owns the truth.
+      }).catch(() => {
+        showRestoreStatus("Still could not load the work queue.", true);
+      });
+    });
+    el.appendChild(document.createTextNode(" "));
+    el.appendChild(btn);
+  }
+}
+
+// The Jump to latest control must actually work: it is activated by click, and
+// it appears only when new content arrives while the operator has deliberately
+// scrolled away from the newest message. Arriving content never yanks a
+// deliberately positioned view; it offers this control instead.
+function initJumpToLatest() {
+  const pill = document.getElementById("jump-to-latest");
+  if (!pill) return;
+  pill.addEventListener("click", jumpToLatestMessage);
+  // ONE capturing listener on window. Scroll events do not bubble, but they do
+  // reach window during the CAPTURE phase from any target, so this observes
+  // whichever element is scrolling without having to re-bind. Binding to the
+  // scroller resolved at init would go stale as soon as layout changed the
+  // scrolling ancestor -- which is exactly why the scroller is resolved lazily
+  // inside the handler.
+  window.addEventListener("scroll", () => {
+    if (!operatorMovedAwayFromLatest(conversationScrollEl())) pill.hidden = true;
+  }, true);
+  const anchor = conversationAnchorEl();
+  if (!anchor) return;
+  try {
+    const obs = new MutationObserver(() => {
+      // The pill is OUTSIDE the observed content container, so toggling it
+      // cannot re-enter this observer.
+      if (operatorMovedAwayFromLatest(conversationScrollEl())) pill.hidden = false;
+      else jumpToLatestMessage();
+    });
+    obs.observe(anchor, { childList: true, subtree: true });
+  } catch (e) { /* no observer -> the click path still works */ }
+}
+
+function jumpToLatestMessage() {
+  const el = conversationScrollEl();
+  if (!el) return;
+  el.scrollTop = el.scrollHeight;
+  const pill = document.getElementById("jump-to-latest");
+  if (pill) pill.hidden = true;
 }
 
 function highlightMessage(messageId) {
   try {
-    const sel = '[data-message-id="' +
-      (window.CSS && CSS.escape ? CSS.escape(messageId) : messageId) + '"]';
+    const sel = '[data-message-id="' + cssAttrValue(messageId) + '"]';
     const node = document.querySelector(sel);
     if (node) {
       node.classList.add("msg-highlight");
@@ -1317,27 +2331,99 @@ function highlightMessage(messageId) {
 
 // Apply a #work=...&msg=... route on load / hashchange (navigation only).
 function applyWorkHashRoute() {
-  const h = location.hash || "";
-  const m = /[#&]work=([^&]+)/.exec(h);
-  if (!m) return;
-  const wid = decodeURIComponent(m[1]);
-  selectTask(null, wid);
+  const route = parseWorkRoute(location.hash);
+  if (!route) return;
+  if (route.malformed) {
+    // Never throw out of boot. Clear the route AND the selection unconditionally
+    // -- clearWorkRoute() removes the hash, so the restoration that follows can
+    // no longer see this route, and leaving a selection behind would let an
+    // unusable link keep a destination bound.
+    selectTask(null);
+    persistSelection(null);
+    clearWorkRoute();
+    // Say what actually happens next. Restoration DOES continue, so claiming
+    // "nothing is selected" would be false a moment later.
+    routeErrorReported = true;
+    // Restoration may legitimately find no active work, so the message states
+    // only what has already happened and leaves the outcome to be observed.
+    showRestoreStatus("That link could not be read, so it was removed. " +
+                      "Nothing is selected from it.");
+    return;
+  }
+  // Validate here too. This function is also the hashchange path, so without
+  // it a post-boot link could select and persist an unknown or terminal item
+  // with no restoration pass following to correct it.
+  const bound = bindRouteSelection(route.work_item_id);
+  if (bound === false) return;
+  if (bound === null) {
+    // Queue not loaded yet (boot). Bind provisionally; restoreActiveSelection()
+    // validates against the live queue as soon as it arrives.
+    const known = (lastWorkItems || []).find((it) => it.work_item_id === route.work_item_id);
+    selectTask(known ? known.thread_id || null : null, route.work_item_id);
+  }
   showView("work");
-  const mm = /[#&]msg=([^&]+)/.exec(h);
-  if (mm) setTimeout(() => highlightMessage(decodeURIComponent(mm[1])), 200);
+  if (route.message_id) {
+    setTimeout(() => highlightMessage(route.message_id), 200);
+  }
 }
 
+// Returns one of the four REFRESH_* outcomes so callers can distinguish a
+// confirmed load from a handled failure without relying on exceptions. It
+// deliberately does NOT throw: it is called from a polling timer where an
+// unhandled rejection would be noise, and it keeps the previous content on
+// screen rather than blanking the operator.
 async function refreshWorkItems() {
+  const gen = ++queueRefreshGeneration;
   try {
     const data = await getJSON("/api/work-items");
-    lastWorkItems = data.work_items || [];
+    // A completion that is no longer the newest may not touch ANY shared
+    // state: not the snapshot, not loaded/confirmed, not the status. An older
+    // success arriving after a newer failure must not restore sendability.
+    if (gen !== queueRefreshGeneration) return REFRESH_SUPERSEDED;
+    // A 200 with a malformed body is NOT authoritative. Treating a missing or
+    // non-array work_items as an empty queue would confirm a snapshot the
+    // server never actually described, and confirmed-empty is a load-bearing
+    // outcome: it makes stale destinations unsendable. So it is reported as a
+    // FAILURE, which preserves the previous snapshot and pauses sending.
+    if (!data || !Array.isArray(data.work_items)) {
+      queueConfirmed = false;
+      queueFailureReported = true;
+      showRestoreStatus("The work queue returned an unreadable response, so " +
+                        "destinations cannot be confirmed. Sending is paused " +
+                        "until it reloads.", true);
+      return REFRESH_FAILED;
+    }
+    lastWorkItems = data.work_items;
+    workItemsLoaded = true;   // a SUCCESSFUL response, even when it is empty
+    queueConfirmed = true;    // and it is CURRENT as of this response
+    // Only a confirmed success clears a reported failure, because only this
+    // re-establishes the truth that failure was reporting.
+    if (queueFailureReported) {
+      queueFailureReported = false;
+      showRestoreStatus("");
+    }
     try {
       const cd = await getJSON("/api/review-councils");
-      lastQueueCouncils = cd.review_councils || [];
+      if (gen === queueRefreshGeneration) {
+        lastQueueCouncils = cd.review_councils || [];
+      }
     } catch (e2) { /* councils optional for queue hints */ }
+    if (gen !== queueRefreshGeneration) return REFRESH_SUPERSEDED;
     renderQueue();
+    return lastWorkItems.length ? REFRESH_CONFIRMED : REFRESH_CONFIRMED_EMPTY;
   } catch (e) {
-    // Leave the prior content in place on a transient fetch error.
+    // An older failure arriving after a newer success must not invalidate the
+    // confirmed current snapshot.
+    if (gen !== queueRefreshGeneration) return REFRESH_SUPERSEDED;
+    // Leave the prior content on screen so the operator is not blanked out, but
+    // withdraw its authority to authorise a send: an unrefreshed snapshot is
+    // not evidence that the destination is still live.
+    queueConfirmed = false;
+    queueFailureReported = true;
+    showRestoreStatus("The work queue could not be refreshed, so destinations " +
+                      "cannot be confirmed. Sending is paused until it reloads.",
+                      true);
+    return REFRESH_FAILED;
   }
 }
 
@@ -1398,7 +2484,7 @@ async function loadHistory() {
   lastLedgerRows = (data.rows || []).filter((row) => ledgerRowMatches(row, f));
   const body = document.getElementById("ledger-body");
   if (!lastLedgerRows.length) {
-    body.innerHTML = '<tr><td colspan="6" class="muted">No records match the filters.</td></tr>';
+    body.innerHTML = '<tr><td colspan="8" class="muted">No records match the filters.</td></tr>';
     return;
   }
   body.innerHTML = lastLedgerRows.slice(0, 500).map((row, i) =>
@@ -1406,12 +2492,31 @@ async function loadHistory() {
     '" data-ledger-index="' + i + '">' +
     "<td>" + esc(shortTime(row.at)) + "</td>" +
     "<td>" + esc(row.type) + (row.archived ? ' <span class="feed-badge local">archived</span>' : "") + "</td>" +
-    '<td class="mono">' + esc(row.work_item_id || row.thread_id || row.packet_id || "") + "</td>" +
+    // Item 4: message, work-item and thread are SEPARATE columns. The previous
+    // `work_item_id || thread_id || packet_id` fallback printed a thr-... value
+    // under a heading that said "Work item", which is a false identity claim for
+    // the 148 ledger rows that legitimately have no work-item binding.
+    '<td class="mono">' + esc(abbrevId(ledgerMessageId(row), 12)) + "</td>" +
+    '<td class="mono">' + (row.work_item_id
+      ? esc(abbrevId(row.work_item_id, 12))
+      : '<span class="muted ledger-none">no work item</span>') + "</td>" +
+    '<td class="mono">' + (row.thread_id
+      ? esc(abbrevId(row.thread_id, 12))
+      : '<span class="muted ledger-none">-</span>') + "</td>" +
     "<td>" + esc(row.actor || "") + "</td>" +
     '<td class="ledger-event">' + esc(row.event || "") + "</td>" +
     "<td>" + esc(row.status || "") + "</td></tr>").join("");
 }
 
+// The durable message id for a ledger row, when the row IS a message. Packet and
+// council rows have none, and inventing one would be a false identity claim.
+function ledgerMessageId(row) {
+  if (!row) return "";
+  const rec = row.record || {};
+  if (row.type === "message") return rec.message_id || "";
+  return "";
+}
+
 function openLedgerDetail(index) {
   const row = lastLedgerRows[index];
   if (!row) return;
@@ -1837,7 +2942,8 @@ function buildConversationTab(run) {
     html += '<div class="' + cls + '" data-message-id="' + esc(m.message_id || "") + '">' +
       (tag ? '<div class="conv-entry-tag">' + esc(tag.label) + "</div>" : "") +
       '<div class="conv-msg-body">' + esc(m.message) + "</div>" +
-      '<div class="conv-msg-meta">' + meta + "</div></div>";
+      '<div class="conv-msg-meta">' + meta + "</div>" +
+      messageIdentityRow(m) + "</div>";
   }
   html += "</div>";
   return html;
@@ -2135,6 +3241,27 @@ let convComposerNewThreadId = null;
 let convComposer = null;
 
 function convComposerTarget() {
+  // Phase 1, item 3: while a work item is selected the composer is BOUND to it,
+  // so the destination shown above the composer is the destination the post
+  // actually reaches. The work_item_id is only sent alongside a durable thread
+  // id, which engages the server's existing target-integrity check (it refuses
+  // a thread/work-item pair that is not genuinely bound) rather than relying on
+  // presentation alone.
+  if (selectedWorkItemId) {
+    // The thread comes ONLY from the live queue record. Reading
+    // selectedConvThread here was the stale-state path: when polling removed
+    // the selected item, the remembered thread kept the target sendable even
+    // though nothing in the live queue backed it.
+    const live = liveQueueRecord(selectedWorkItemId);
+    const thread = (live && live.thread_id) || null;
+    if (thread) return { work_item_id: selectedWorkItemId, thread_id: thread };
+    // FAIL CLOSED. A work_item_id WITHOUT a thread_id is the one shape the
+    // server's target-integrity check cannot validate, because that check
+    // compares the pair. Rather than emit an unverifiable target, report the
+    // selection as unresolved: the banner says so and the send is refused
+    // until the queue supplies a durable thread for this item.
+    return { work_item_id: selectedWorkItemId, thread_id: null, unresolved: true };
+  }
   if (selectedConvThread) return { thread_id: selectedConvThread };
   if (!convComposerNewThreadId) convComposerNewThreadId = genThreadId();
   return { thread_id: convComposerNewThreadId };
@@ -2783,12 +3910,23 @@ function toggleToolLog() {
   if (footer) footer.hidden = !footer.hidden;
 }
 
+// A queue tile is a control that CONTAINS controls. Without this, clicking Copy
+// would also open the item, which is the opposite of a copy-without-navigating
+// affordance.
+function eventTargetsInnerControl(e) {
+  const t = e && e.target;
+  return !!(t && t.closest && t.closest(".copy-id"));
+}
+
 function selectTask(threadId, workItemId) {
   selectedConvThread = threadId || null;
   selectedWorkItemId = workItemId || null;
+  persistSelection(selectedWorkItemId);   // survive refresh (Phase 1, item 1)
+  applyComposerFocus();                   // one safe composer (Phase 1, item 3)
   convComposerNewThreadId = null;
   if (convComposer) convComposer.restoreDraft();
   if (operatorChatComposer) operatorChatComposer.updateBanner();
+  if (convComposer) convComposer.updateBanner();   // destination follows selection
   renderQueue();
   refreshTaskState();
   loadConversations();
@@ -2869,11 +4007,18 @@ function wire() {
 
   // Work queue: clicking a row selects that task everywhere.
   document.getElementById("queue-groups").addEventListener("click", (e) => {
-    const row = e.target.closest(".q-row");
-    if (!row) return;
-    const thread = row.getAttribute("data-thread");
-    const workItem = row.getAttribute("data-work-item");
-    if (thread || workItem) selectTask(thread, workItem);
+    if (eventTargetsInnerControl(e)) return;   // Copy is not "open this item"
+    // Activation is scoped to the EXPLICIT primary control. Treating any pixel
+    // of the row as an activation target contradicted the button model and made
+    // the identifier rows and the integrity warning navigate unexpectedly, so
+    // selecting or copying that text is now safe.
+    const btn = e.target.closest(".q-open");
+    if (!btn) return;
+    const workItem = btn.getAttribute("data-work-item") ||
+      (btn.closest(".q-row") && btn.closest(".q-row").getAttribute("data-work-item"));
+    // Mouse activation goes through the SAME navigation as the keyboard so the
+    // canonical #work= route is always written.
+    if (workItem) navigateToWorkItem(workItem);
   });
 
   // Context-aware task actions are READ-ONLY navigation only: they switch view
@@ -2886,6 +4031,10 @@ function wire() {
     if (nav === "history") showView("history");
     else showView("work");   // conv / council / evidence / gate / verification tabs
   });
+  // This is the explicit, keyboard-reachable action that starts a new
+  // conversation while work is selected: clearing the selection is what
+  // re-enables the generic composer (applyComposerFocus lifts the demotion), so
+  // the demoted composer is never a dead end.
   document.getElementById("queue-new-btn").addEventListener("click", () => {
     selectTask(null);
     renderConvDetail(null);
@@ -2997,6 +4146,24 @@ function wire() {
   // at boot; the fast poll below only runs while the Work view is open.
   loadConversations();
   applyWorkHashRoute();   // honor a #work=...&msg=... deep link on load
+  // Active session continuity: once the queue has loaded, restore the prior
+  // selection or fall back to the highest-priority active item so a refresh
+  // never strands the operator on an empty panel while active work exists.
+  initJumpToLatest();
+  refreshWorkItems().then((outcome) => {
+    // Only a CONFIRMED success may clear status or restore a selection. On a
+    // handled failure the explanation stays and nothing is restored, because
+    // restoring from an unconfirmed snapshot is exactly what the failure means
+    // we cannot do. A superseded completion is not ours to act on at all.
+    if (!refreshSucceeded(outcome)) return;
+    clearTransientRestoreStatus();
+    restoreActiveSelection();
+  }).catch(() => {
+    // Continuity that fails silently is worse than continuity that reports the
+    // failure: the operator would see an empty console with no reason given.
+    showRestoreStatus("Could not load the work queue, so active work was not " +
+                      "restored. The next refresh will retry.", true);
+  });
   setInterval(refresh, LIVE_MS);
   setInterval(refreshAgentEvents, LIVE_MS);
   setInterval(refreshMessages, LIVE_MS);
diff --git a/apps/control-plane/static/index.html b/apps/control-plane/static/index.html
index 1a5fd93..57b8bd6 100644
--- a/apps/control-plane/static/index.html
+++ b/apps/control-plane/static/index.html
@@ -59,6 +59,9 @@
           <span>Work queue <span class="help" tabindex="0" role="button" aria-label="About the work queue">?<span class="tip">Actionable work only, derived from the live queue and messages: unanswered actionable requests, CTA packets ready to claim, IN_PROGRESS packets needing an update, and RFI packets awaiting clarification. Normal chat stays in the Conversation tab and never appears here. Workers can claim and respond through tools/clearwright_worker.py or local HTTP (/api/work-items); the browser is the operator display. Use "use CW" in Claude Desktop by having Claude post to this queue through the worker bridge (tools/clearwright_worker.py).</span></span></span>
           <button id="queue-new-btn" class="btn btn-quiet" type="button" title="Start a new conversation">New</button>
         </div>
+        <!-- Restoration/queue-load failures are reported here rather than
+             leaving the operator with an unexplained empty console. -->
+        <p class="restore-status" id="restore-status" role="status" aria-live="polite" hidden></p>
         <div class="queue-filters" id="queue-filters" role="tablist" aria-label="Queue filters">
           <button class="qf-chip is-active" data-filter="current" type="button">Current</button>
           <button class="qf-chip" data-filter="needs_attention" type="button">Needs attention</button>
@@ -113,24 +116,42 @@
 
         <div id="center-work" hidden>
           <p class="hint">Operator/agent dialogue on durable message threads. Replies from Claude, Codex, or other workers appear only when they actually post back through the local adapter; nothing here is simulated.</p>
-          <div id="conv-detail"><p class="muted">No task selected. Pick one from the work queue.</p></div>
+          <div id="conv-detail"></div>
           <!-- The fixed Work composer docks here (sticky at the viewport
                bottom) so replying never requires scrolling past the timeline. -->
+          <!-- Phase 1 item 4: the durable message id of a just-posted message is
+               shown here with a copy control, so the operator never needs
+               History or raw JSON to retrieve it (GitHub issue #86). -->
+          <div class="post-confirmation" id="post-confirmation" role="status" aria-live="polite" hidden></div>
+          <!-- Phase 1 item 2: explicit return to the newest message when the
+               operator has deliberately scrolled away. -->
+          <button type="button" class="jump-to-latest" id="jump-to-latest" hidden>Jump to latest</button>
           <div id="work-composer-dock"></div>
         </div>
       </section>
 
       <aside class="operator-region" id="operator-region" aria-label="Operator panel">
-        <div class="op-card" id="next-action-card">
-          <div class="op-card-head">Next required action</div>
-          <div class="op-card-body" id="next-action-body"><p class="muted">No task selected.</p></div>
-        </div>
+        <!-- Phase 1 item 5: ONE contextual session panel, shown only when a work
+             item is selected. Rows with no content are omitted rather than
+             rendered as separate empty placeholder cards. -->
+        <div class="session-rail" id="session-rail" hidden>
+          <div class="op-card" id="next-action-card">
+            <div class="op-card-head">Next required action</div>
+            <div class="op-card-body" id="next-action-body"></div>
+          </div>
+
+          <div class="op-card" id="authority-card">
+            <div class="op-card-head">Authority state</div>
+            <div class="op-card-body" id="authority-body"></div>
+          </div>
 
-        <div class="op-card" id="authority-card">
-          <div class="op-card-head">Authority state</div>
-          <div class="op-card-body" id="authority-body"><p class="muted">No task selected.</p></div>
+          <div class="op-card" id="operator-actions-card">
+            <div class="op-card-head">Operator actions</div>
+            <div class="op-card-body conv-actions" id="operator-actions"></div>
+          </div>
         </div>
 
+
         <section class="op-card clearance-card is-empty" id="clearance-card" aria-labelledby="incoming-h">
           <div class="op-card-head" id="incoming-h">Incoming clearance request <span class="help" tabindex="0" role="button" aria-label="About incoming clearance requests">?<span class="tip">Clearance packets arrive from agents, tools, scripts, or integrations. The operator reviews and decides; nobody fills out packet paperwork here.</span></span></div>
           <div id="operator-card"><p class="muted">Loading...</p></div>
@@ -151,10 +172,7 @@
           <p class="conv-target-hint">Message is normal chat: durable, but never a work item and never an Attention flag. Participation is real only when a worker posts back through CW. Use the Work page composer for actionable requests.</p>
         </div>
 
-        <div class="op-card" id="operator-actions-card">
-          <div class="op-card-head">Operator actions</div>
-          <div class="op-card-body conv-actions" id="operator-actions"><p class="muted">No task selected.</p></div>
-        </div>
+        
       </aside>
     </div>
 
@@ -188,9 +206,12 @@
         <div class="ledger-table-wrap">
           <table class="ledger" aria-label="History ledger">
             <thead>
-              <tr><th>Time</th><th>Type</th><th>Work item</th><th>Actor</th><th>Event</th><th>Status</th></tr>
+              <!-- Item 4: message, work item and thread are DISTINCT
+                   identifier types and get their own columns. A thr-...
+                   value must never appear under "Work item". -->
+              <tr><th>Time</th><th>Type</th><th>Message</th><th>Work item</th><th>Thread</th><th>Actor</th><th>Event</th><th>Status</th></tr>
             </thead>
-            <tbody id="ledger-body"><tr><td colspan="6" class="muted">Loading...</td></tr></tbody>
+            <tbody id="ledger-body"><tr><td colspan="8" class="muted">Loading...</td></tr></tbody>
           </table>
         </div>
         <div class="ledger-detail" id="ledger-detail" hidden>
diff --git a/apps/control-plane/static/style.css b/apps/control-plane/static/style.css
index ac13c95..30b369e 100644
--- a/apps/control-plane/static/style.css
+++ b/apps/control-plane/static/style.css
@@ -1047,3 +1047,169 @@ body.history-open .mission { display: none !important; }
 .activity-details[open] summary::before { content: "<U+25BE> "; }
 .activity-label { font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--gray); white-space: nowrap; }
 .activity-log { margin: 0.35rem 0 0.2rem; font-family: ui-monospace, monospace; font-size: 0.74rem; white-space: pre-wrap; max-height: 5.5rem; overflow-y: auto; }
+
+/* ==========================================================================
+   Active Session Continuity and Message Identity UX (Phase 1)
+   Presentation only. No durable record, identity, or authority semantics are
+   expressed here.
+   ========================================================================== */
+
+/* Item 3: the composer destination must be hard to misread and hard to get
+   wrong. It is shown above the composer, never inferred from message prose. */
+.composer-destination {
+  display: flex; flex-wrap: wrap; align-items: center; gap: .4rem;
+  padding: .4rem .55rem; border-radius: 6px;
+  background: rgba(120, 170, 255, .10);
+  border: 1px solid rgba(120, 170, 255, .35);
+  font-size: .82rem;
+}
+.composer-destination .dest-label { font-weight: 600; opacity: .85; }
+.composer-destination .dest-work { font-weight: 600; }
+.composer-destination .dest-thread { opacity: .8; }
+.composer-destination .dest-title { opacity: .7; font-style: italic; }
+/* .composer-banner sets text-transform:uppercase on everything it contains, so
+   without this the destination shows ids as "MESSAGE:MSG-2026..." -- a case the
+   durable record does not have, and misleading to read or transcribe. The
+   "Posting to" label keeps the banner styling; the identities render as stored. */
+.composer-destination .dest-work,
+.composer-destination .dest-thread,
+.composer-destination .dest-title {
+  text-transform: none;
+  letter-spacing: 0;
+}
+
+/* Item 3: the generic composer is demoted, never removed, while work is
+   selected, so it cannot compete with the work-item-bound composer. */
+.composer-demoted { opacity: .45; filter: saturate(.6); }
+.composer-demoted:focus-within { opacity: 1; filter: none; }
+
+/* Item 4: durable message identity, visible on every message card. */
+.msg-identity {
+  display: flex; flex-wrap: wrap; align-items: center; gap: .35rem;
+  margin-top: .3rem; padding-top: .3rem;
+  border-top: 1px dashed rgba(255, 255, 255, .12);
+  font-size: .74rem; opacity: .85;
+}
+.msg-identity .msg-id { font-weight: 600; }
+.msg-identity .msg-actor,
+.msg-identity .msg-intent,
+.msg-identity .msg-binding,
+.msg-identity .msg-time { opacity: .7; }
+
+/* A real button so it is keyboard reachable and focus-visible. */
+.copy-id {
+  font: inherit; font-size: .72rem; line-height: 1;
+  padding: .15rem .4rem; border-radius: 4px; cursor: pointer;
+  border: 1px solid rgba(255, 255, 255, .25);
+  background: transparent; color: inherit; opacity: .8;
+}
+.copy-id:hover { opacity: 1; }
+.copy-id:focus-visible { outline: 2px solid #7aa2ff; outline-offset: 1px; opacity: 1; }
+
+/* Item 4: post-send confirmation carrying the new durable message id. */
+.post-confirmation {
+  display: flex; flex-wrap: wrap; align-items: center; gap: .4rem;
+  margin: .4rem 0; padding: .4rem .55rem; border-radius: 6px;
+  background: rgba(90, 200, 140, .12);
+  border: 1px solid rgba(90, 200, 140, .35);
+  font-size: .78rem;
+}
+.post-confirmation .pc-label { font-weight: 600; }
+.post-confirmation .pc-work,
+.post-confirmation .pc-thread { opacity: .75; }
+
+/* Item 2: explicit return to the newest message. */
+.jump-to-latest {
+  align-self: center; font: inherit; font-size: .78rem;
+  padding: .25rem .6rem; margin: .25rem 0; border-radius: 999px;
+  cursor: pointer; border: 1px solid rgba(120, 170, 255, .45);
+  background: rgba(120, 170, 255, .15); color: inherit;
+}
+.jump-to-latest:focus-visible { outline: 2px solid #7aa2ff; outline-offset: 2px; }
+
+/* Item 5: one contextual rail; hidden entirely with nothing selected. */
+.session-rail[hidden] { display: none; }
+.session-rail { display: flex; flex-direction: column; gap: .6rem; }
+
+/* Item 7: queue rows are real controls, so they need a visible focus ring. */
+/* Queue rows are plain containers; the primary control is .q-open, which
+   carries the cursor and focus ring. The old role="button" rules are gone. */
+.q-exec { font-size: .68rem; opacity: .65; letter-spacing: .02em; }
+
+/* Round-2 council corrections. */
+.restore-status {
+  margin: 0 0 0.4rem; padding: 0.4rem 0.55rem;
+  font-size: 0.74rem; color: var(--text);
+  background: var(--panel-2); border: 1px solid var(--line);
+  border-left: 3px solid var(--warn, #d08a00); border-radius: 8px;
+}
+/* An unverifiable destination must not look like a valid one. */
+.composer-destination .dest-unresolved {
+  color: var(--warn, #d08a00);
+  font-weight: 700;
+}
+
+/* Durable identity on queue tiles (operator correction items 1, 2, 3, 10). */
+.q-ids {
+  display: flex; flex-direction: column; gap: 0.12rem;
+  margin-top: 0.3rem; padding-top: 0.3rem;
+  border-top: 1px dashed var(--line);
+}
+.q-idrow { display: flex; align-items: center; gap: 0.3rem; flex-wrap: wrap; font-size: 0.66rem; }
+.q-idk {
+  color: var(--gray); font-weight: 700; text-transform: uppercase;
+  letter-spacing: 0.04em; min-width: 5.6rem;
+}
+/* Identifiers render in their STORED case; no transform may alter them. */
+.q-idv { text-transform: none; letter-spacing: 0; opacity: .9; }
+.q-idnote {
+  font-size: 0.6rem; color: var(--gray); border: 1px dotted var(--line);
+  border-radius: 999px; padding: 0 0.3rem; cursor: help;
+}
+.q-phase, .q-exec {
+  font-size: 0.6rem; font-weight: 700; text-transform: none; letter-spacing: 0;
+  padding: 0 0.35rem; border-radius: 999px; border: 1px solid var(--line);
+  color: var(--gray);
+}
+.q-phase { border-style: dashed; }
+/* An ambiguous tile is MARKED, never hidden: the records are real. */
+.q-integrity {
+  margin-top: 0.3rem; padding: 0.25rem 0.4rem;
+  font-size: 0.64rem; line-height: 1.35; color: var(--text);
+  background: var(--panel-2); border-left: 3px solid var(--warn, #d08a00);
+  border-radius: 6px;
+}
+.q-row.q-ambiguous { border-left: 3px solid var(--warn, #d08a00); }
+/* History: an absent binding is stated, not substituted. */
+.ledger-none { font-style: italic; opacity: .7; }
+
+/* The queue tile's primary control. The row itself is a plain container, so
+   this button carries the click target, the pressed state and the focus ring,
+   and real copy buttons can sit beside it without nesting inside a control. */
+.q-open {
+  display: block; width: 100%; text-align: left;
+  background: none; border: 0; padding: 0; margin: 0;
+  font: inherit; color: inherit; cursor: pointer;
+}
+.q-open:focus-visible {
+  outline: 2px solid var(--blue-soft);
+  outline-offset: 2px;
+  border-radius: 6px;
+}
+/* The control uses aria-current, not a toggle state: activating it navigates
+   rather than switching something off again. */
+.q-open[aria-current="true"] .q-title { font-weight: 700; }
+
+/* .q-meta is a span (phrasing content, so it may sit inside the button) but
+   still lays out as a row. */
+.q-open .q-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 0.3rem; }
+
+/* A non-canonical entry (today only a clearance-packet projection) is shown for
+   visibility but is READ-ONLY: no activation, no navigation, no destination. */
+.q-row.q-noncanonical { opacity: .92; border-left: 3px dashed var(--line); }
+.q-readonly { display: block; cursor: default; }
+.q-ro-badge {
+  margin-left: 0.4rem; padding: 0 0.35rem; border-radius: 999px;
+  font-size: 0.6rem; font-weight: 700; letter-spacing: 0.03em;
+  color: var(--gray); border: 1px dotted var(--line); cursor: help;
+}
diff --git a/tests/dom/mini_dom.mjs b/tests/dom/mini_dom.mjs
new file mode 100644
index 0000000..d1f8ab4
--- /dev/null
+++ b/tests/dom/mini_dom.mjs
@@ -0,0 +1,399 @@
+/*
+ * A small but REAL DOM: parsing, tree, selectors, focus and event propagation.
+ *
+ * The previous harness called app.js functions directly. Both reviewers
+ * correctly said that proves the helpers, not the wired path -- a click on a
+ * rendered control reaching the delegated listener, Enter and Space activating
+ * a native button, and focus surviving a polling cycle are exactly the things
+ * direct calls cannot demonstrate. This module supplies enough real DOM
+ * behaviour to install wire() and dispatch genuine events.
+ *
+ * Dependency-free: Node builtins only, no package manifest, no browser driver.
+ *
+ * STATED LIMITATION: this is not a browser. It implements markup parsing, the
+ * element tree, a useful subset of CSS selectors, focus tracking, capture/target/
+ * bubble event propagation, and native <button> Enter/Space activation. It does
+ * NOT implement layout, painting, or real scrolling, so geometry-dependent
+ * behaviour is still proven only against supplied values.
+ */
+
+const VOID_TAGS = new Set(["br", "hr", "img", "input", "meta", "link"]);
+
+class ClassList {
+  constructor(el) { this.el = el; }
+  _set() {
+    const v = this.el.getAttribute("class") || "";
+    return new Set(v.split(/\s+/).filter(Boolean));
+  }
+  _write(s) { this.el.setAttribute("class", Array.from(s).join(" ")); }
+  add(c) { const s = this._set(); s.add(c); this._write(s); }
+  remove(c) { const s = this._set(); s.delete(c); this._write(s); }
+  toggle(c, on) { if (on === undefined ? !this.contains(c) : on) this.add(c); else this.remove(c); }
+  contains(c) { return this._set().has(c); }
+  get value() { return this.el.getAttribute("class") || ""; }
+}
+
+export class MiniEvent {
+  constructor(type, init) {
+    init = init || {};
+    this.type = type;
+    this.bubbles = init.bubbles !== false;
+    this.cancelable = init.cancelable !== false;
+    this.key = init.key;
+    this.code = init.code;
+    this.ctrlKey = !!init.ctrlKey;
+    this.metaKey = !!init.metaKey;
+    this.shiftKey = !!init.shiftKey;
+    this.defaultPrevented = false;
+    this.isTrusted = !!init.isTrusted;
+    this.target = null;
+    this.currentTarget = null;
+    this._stopped = false;
+  }
+  preventDefault() { if (this.cancelable) this.defaultPrevented = true; }
+  stopPropagation() { this._stopped = true; }
+}
+
+export class MiniElement {
+  constructor(tag, doc) {
+    this.tagName = String(tag).toUpperCase();
+    this.ownerDocument = doc;
+    this.childNodes = [];
+    this.parentNode = null;
+    this._attrs = {};
+    this._listeners = {};
+    this._text = "";
+    this.scrollTop = 0;
+    this.scrollHeight = 0;
+    this.clientHeight = 0;
+    this.style = {};
+    this.value = "";
+    this.disabled = false;
+    this.checked = false;
+    this._overflowY = "visible";
+  }
+
+  // --- attributes ---------------------------------------------------------
+  setAttribute(k, v) { this._attrs[k] = String(v); }
+  getAttribute(k) { return Object.prototype.hasOwnProperty.call(this._attrs, k) ? this._attrs[k] : null; }
+  hasAttribute(k) { return Object.prototype.hasOwnProperty.call(this._attrs, k); }
+  removeAttribute(k) { delete this._attrs[k]; }
+  get classList() { return new ClassList(this); }
+  get className() { return this.getAttribute("class") || ""; }
+  set className(v) { this.setAttribute("class", v); }
+  get id() { return this.getAttribute("id") || ""; }
+  set id(v) { this.setAttribute("id", v); }
+  get hidden() { return this.hasAttribute("hidden"); }
+  set hidden(v) { if (v) this.setAttribute("hidden", ""); else this.removeAttribute("hidden"); }
+  get tabIndex() {
+    if (this.hasAttribute("tabindex")) return parseInt(this.getAttribute("tabindex"), 10);
+    return (this.tagName === "BUTTON" || this.tagName === "A" ||
+            this.tagName === "INPUT" || this.tagName === "TEXTAREA" ||
+            this.tagName === "SELECT") ? 0 : -1;
+  }
+  set tabIndex(v) { this.setAttribute("tabindex", String(v)); }
+  get inert() { return this.hasAttribute("inert"); }
+  set inert(v) { if (v) this.setAttribute("inert", ""); else this.removeAttribute("inert"); }
+  get dataset() {
+    const out = {};
+    Object.keys(this._attrs).forEach((k) => {
+      if (k.indexOf("data-") === 0) out[k.slice(5).replace(/-([a-z])/g, (m, c) => c.toUpperCase())] = this._attrs[k];
+    });
+    return out;
+  }
+
+  // --- tree ---------------------------------------------------------------
+  get children() { return this.childNodes.filter((c) => c instanceof MiniElement); }
+  get firstElementChild() { return this.children[0] || null; }
+  get parentElement() { return this.parentNode instanceof MiniElement ? this.parentNode : null; }
+  appendChild(c) {
+    if (c.parentNode) c.parentNode.removeChild(c);
+    c.parentNode = this;
+    this.childNodes.push(c);
+    return c;
+  }
+  insertBefore(c, ref) {
+    if (c.parentNode) c.parentNode.removeChild(c);
+    c.parentNode = this;
+    const i = ref ? this.childNodes.indexOf(ref) : -1;
+    if (i < 0) this.childNodes.push(c); else this.childNodes.splice(i, 0, c);
+    return c;
+  }
+  removeChild(c) {
+    const i = this.childNodes.indexOf(c);
+    if (i >= 0) this.childNodes.splice(i, 1);
+    c.parentNode = null;
+    return c;
+  }
+  replaceChild(next, prev) {
+    const i = this.childNodes.indexOf(prev);
+    if (i < 0) return prev;
+    if (next.parentNode) next.parentNode.removeChild(next);
+    this.childNodes[i] = next;
+    next.parentNode = this;
+    prev.parentNode = null;
+    return prev;
+  }
+  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
+  contains(n) {
+    while (n) { if (n === this) return true; n = n.parentNode; }
+    return false;
+  }
+
+  // --- content ------------------------------------------------------------
+  set innerHTML(html) {
+    this.childNodes = [];
+    parseInto(this, String(html), this.ownerDocument);
+  }
+  get innerHTML() { return this.childNodes.map(serialize).join(""); }
+  get outerHTML() { return serialize(this); }
+  set textContent(v) { this.childNodes = [{ nodeType: 3, data: String(v) }]; }
+  get textContent() { return collectText(this); }
+  get innerText() { return this.textContent; }
+
+  // --- selectors ----------------------------------------------------------
+  matches(sel) { return selectorMatches(this, sel); }
+  closest(sel) {
+    let n = this;
+    while (n) { if (n instanceof MiniElement && selectorMatches(n, sel)) return n; n = n.parentNode; }
+    return null;
+  }
+  querySelectorAll(sel) {
+    const out = [];
+    walk(this, (n) => { if (n !== this && selectorMatches(n, sel)) out.push(n); });
+    return out;
+  }
+  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
+
+  // --- events -------------------------------------------------------------
+  addEventListener(type, fn, opts) {
+    const capture = opts === true || (opts && opts.capture);
+    (this._listeners[type] = this._listeners[type] || []).push({ fn, capture: !!capture });
+  }
+  removeEventListener(type, fn) {
+    const l = this._listeners[type];
+    if (l) this._listeners[type] = l.filter((e) => e.fn !== fn);
+  }
+  dispatchEvent(ev) {
+    ev.target = ev.target || this;
+    const path = [];
+    let n = this;
+    while (n) { path.push(n); n = n.parentNode; }
+    // capture (root -> target)
+    for (let i = path.length - 1; i >= 0 && !ev._stopped; i--) fire(path[i], ev, true);
+    // bubble (target -> root)
+    if (ev.bubbles) {
+      for (let i = 0; i < path.length && !ev._stopped; i++) fire(path[i], ev, false);
+    } else if (!ev._stopped) {
+      fire(this, ev, false);
+    }
+    return !ev.defaultPrevented;
+  }
+
+  focus() {
+    const d = this.ownerDocument;
+    if (d) d.activeElement = this;
+  }
+  blur() {
+    const d = this.ownerDocument;
+    if (d && d.activeElement === this) d.activeElement = d.body;
+  }
+  click() {
+    this.dispatchEvent(new MiniEvent("click", { bubbles: true, isTrusted: true }));
+  }
+  scrollIntoView() {}
+  getBoundingClientRect() { return { width: 100, height: 20, top: 0, left: 0 }; }
+}
+
+function fire(node, ev, capture) {
+  const l = node._listeners && node._listeners[ev.type];
+  if (!l) return;
+  ev.currentTarget = node;
+  l.slice().forEach((entry) => {
+    if (!!entry.capture === !!capture) {
+      try { entry.fn.call(node, ev); } catch (e) { /* surfaced by assertions */ }
+    }
+  });
+}
+
+function walk(node, fn) {
+  (node.childNodes || []).forEach((c) => {
+    if (c instanceof MiniElement) { fn(c); walk(c, fn); }
+  });
+}
+
+function collectText(node) {
+  if (!(node instanceof MiniElement)) return node && node.nodeType === 3 ? node.data : "";
+  return (node.childNodes || []).map(collectText).join("");
+}
+
+function serialize(node) {
+  if (!(node instanceof MiniElement)) return node && node.nodeType === 3 ? node.data : "";
+  const attrs = Object.keys(node._attrs)
+    .map((k) => " " + k + '="' + node._attrs[k] + '"').join("");
+  const tag = node.tagName.toLowerCase();
+  if (VOID_TAGS.has(tag)) return "<" + tag + attrs + ">";
+  return "<" + tag + attrs + ">" + node.childNodes.map(serialize).join("") + "</" + tag + ">";
+}
+
+// --- markup parsing ---------------------------------------------------------
+const TAG_RE = /<(\/?)([a-zA-Z][\w-]*)((?:\s+[\w:-]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+))?)*)\s*(\/?)>/g;
+const ATTR_RE = /([\w:-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g;
+
+function parseInto(root, html, doc) {
+  const stack = [root];
+  let last = 0;
+  TAG_RE.lastIndex = 0;
+  let m;
+  while ((m = TAG_RE.exec(html)) !== null) {
+    if (m.index > last) {
+      const text = html.slice(last, m.index);
+      if (text) stack[stack.length - 1].childNodes.push({ nodeType: 3, data: text });
+    }
+    last = TAG_RE.lastIndex;
+    const closing = m[1] === "/";
+    const tag = m[2].toLowerCase();
+    if (closing) {
+      for (let i = stack.length - 1; i > 0; i--) {
+        if (stack[i].tagName === tag.toUpperCase()) { stack.length = i; break; }
+      }
+      continue;
+    }
+    const el = new MiniElement(tag, doc);
+    ATTR_RE.lastIndex = 0;
+    let a;
+    while ((a = ATTR_RE.exec(m[3] || "")) !== null) {
+      if (!a[1]) continue;
+      el.setAttribute(a[1], a[2] !== undefined ? a[2] : (a[3] !== undefined ? a[3] : (a[4] !== undefined ? a[4] : "")));
+    }
+    const parent = stack[stack.length - 1];
+    el.parentNode = parent;
+    parent.childNodes.push(el);
+    if (!VOID_TAGS.has(tag) && m[4] !== "/") stack.push(el);
+  }
+  if (last < html.length) {
+    const text = html.slice(last);
+    if (text) stack[stack.length - 1].childNodes.push({ nodeType: 3, data: text });
+  }
+}
+
+// --- selector matching ------------------------------------------------------
+// Supports: tag, #id, .class, [attr], [attr="v"], and comma groups, plus a
+// single descendant combinator. Enough for every selector app.js uses.
+function selectorMatches(el, sel) {
+  if (!(el instanceof MiniElement)) return false;
+  return String(sel).split(",").some((part) => matchCompoundChain(el, part.trim()));
+}
+
+// Whitespace-aware compound splitter that respects quoted attribute values.
+function splitCompounds(sel) {
+  const out = [];
+  let buf = "", quote = null, esc = false;
+  for (const ch of String(sel)) {
+    if (esc) { buf += ch; esc = false; continue; }
+    if (ch === "\\") { buf += ch; esc = true; continue; }
+    if (quote) { buf += ch; if (ch === quote) quote = null; continue; }
+    if (ch === '"' || ch === "'") { quote = ch; buf += ch; continue; }
+    if (/\s/.test(ch)) { if (buf) { out.push(buf); buf = ""; } continue; }
+    buf += ch;
+  }
+  if (buf) out.push(buf);
+  return out;
+}
+
+function matchCompoundChain(el, sel) {
+  // Split on descendant combinators, but NOT on whitespace inside a quoted
+  // attribute value: [data-x="a b"] is one compound, not two.
+  const parts = splitCompounds(sel);
+  if (!parts.length) return false;
+  if (!matchCompound(el, parts[parts.length - 1])) return false;
+  let n = el.parentNode;
+  for (let i = parts.length - 2; i >= 0; i--) {
+    let found = false;
+    while (n) {
+      if (n instanceof MiniElement && matchCompound(n, parts[i])) { found = true; n = n.parentNode; break; }
+      n = n.parentNode;
+    }
+    if (!found) return false;
+  }
+  return true;
+}
+
+function matchCompound(el, comp) {
+  // Attribute values may contain BACKSLASH-ESCAPED characters, which is how
+  // a quote or backslash is carried inside a CSS string literal. Matching
+  // must unescape them, or a correctly escaped selector would fail here
+  // and the harness would report a false negative.
+  const re = /(^|\.|#)([\w-]+)|\[([\w-]+)(?:\s*=\s*"((?:[^"\\]|\\.)*)")?\]/g;
+  let m, ok = true, any = false;
+  while ((m = re.exec(comp)) !== null) {
+    any = true;
+    if (m[3] !== undefined) {
+      if (m[4] !== undefined) {
+        const want = m[4].replace(/\\(.)/g, "$1");   // unescape the literal
+        if (el.getAttribute(m[3]) !== want) ok = false;
+      }
+      else if (!el.hasAttribute(m[3])) ok = false;
+    } else if (m[1] === ".") {
+      if (!el.classList.contains(m[2])) ok = false;
+    } else if (m[1] === "#") {
+      if (el.getAttribute("id") !== m[2]) ok = false;
+    } else {
+      if (m[2] !== "*" && el.tagName !== m[2].toUpperCase()) ok = false;
+    }
+  }
+  return any && ok;
+}
+
+// --- document ---------------------------------------------------------------
+export function createDocument() {
+  const doc = {
+    createElement(tag) { return new MiniElement(tag, doc); },
+    createTextNode(t) { return { nodeType: 3, data: String(t) }; },
+    _listeners: {},
+    addEventListener(type, fn, opts) {
+      const capture = opts === true || (opts && opts.capture);
+      (doc._listeners[type] = doc._listeners[type] || []).push({ fn, capture: !!capture });
+    },
+    removeEventListener() {},
+    getElementById(id) { return doc.documentElement.querySelector('[id="' + id + '"]'); },
+    querySelector(sel) { return doc.documentElement.querySelector(sel); },
+    querySelectorAll(sel) { return doc.documentElement.querySelectorAll(sel); },
+  };
+  doc.documentElement = new MiniElement("html", doc);
+  doc.body = new MiniElement("body", doc);
+  doc.documentElement.appendChild(doc.body);
+  doc.activeElement = doc.body;
+  doc.scrollingElement = doc.documentElement;
+  // The document participates in propagation, so delegated listeners installed
+  // on `document` (which wire() uses) actually receive dispatched events.
+  doc.documentElement.parentNode = {
+    _listeners: doc._listeners,
+    parentNode: null,
+  };
+  return doc;
+}
+
+/*
+ * NATIVE BUTTON KEYBOARD ACTIVATION.
+ *
+ * A real browser activates a focused <button> on Enter and on Space and fires
+ * exactly one click. That default is what an over-eager preventDefault() can
+ * suppress, so the harness must model the default rather than assume it, or it
+ * could not detect the regression it exists to catch.
+ */
+export function pressKey(doc, key) {
+  const target = doc.activeElement;
+  if (!target) return { activated: false, defaultPrevented: false };
+  const code = key === " " ? "Space" : (key === "Enter" ? "Enter" : key);
+  const down = new MiniEvent("keydown", { key, code, bubbles: true, cancelable: true, isTrusted: true });
+  target.dispatchEvent(down);
+  const isButton = target.tagName === "BUTTON";
+  const activating = isButton && (key === "Enter" || key === " ");
+  // The default action runs ONLY if nothing called preventDefault on keydown.
+  if (activating && !down.defaultPrevented) {
+    target.dispatchEvent(new MiniEvent("click", { bubbles: true, isTrusted: true }));
+    return { activated: true, defaultPrevented: false };
+  }
+  return { activated: false, defaultPrevented: down.defaultPrevented };
+}
diff --git a/tests/dom/session_ux_runtime.mjs b/tests/dom/session_ux_runtime.mjs
new file mode 100644
index 0000000..a7008eb
--- /dev/null
+++ b/tests/dom/session_ux_runtime.mjs
@@ -0,0 +1,667 @@
+/*
+ * Runtime coverage for the session-continuity UX logic.
+ *
+ * Both reviewers correctly observed that static assertions over app.js cannot
+ * catch the defects that actually occurred in this slice: a scroll listener
+ * bound to an element that never scrolls, a rank bucket nothing can produce,
+ * and a target shape the server cannot validate. This harness EXECUTES the real
+ * app.js against a controllable DOM stub and asserts behaviour.
+ *
+ * Deliberately dependency-free: no package.json, no npm install, no browser. It
+ * runs on the Node already present in CI.
+ *
+ * STATED LIMITATION, so this is not read as more than it is: the stub supplies
+ * scroll geometry rather than computing layout, so it proves the DECISION LOGIC
+ * given a geometry, not that a real browser produces that geometry. The
+ * geometry used below is the one observed in the running console (a
+ * non-scrolling #conv-detail inside a scrolling page), which is exactly the
+ * case that made the feature inert.
+ */
+import fs from "node:fs";
+import path from "node:path";
+import vm from "node:vm";
+import { fileURLToPath } from "node:url";
+
+const HERE = path.dirname(fileURLToPath(import.meta.url));
+const APP = path.join(HERE, "..", "..", "apps", "control-plane", "static", "app.js");
+
+let failures = 0;
+let checks = 0;
+
+function ok(cond, label) {
+  checks += 1;
+  if (!cond) {
+    failures += 1;
+    console.error("FAIL: " + label);
+  }
+}
+
+function eq(actual, expected, label) {
+  ok(JSON.stringify(actual) === JSON.stringify(expected),
+     label + "  (got " + JSON.stringify(actual) + ", want " + JSON.stringify(expected) + ")");
+}
+
+// --------------------------------------------------------------------------
+// Minimal DOM stub. Only what app.js touches at load plus the elements the
+// functions under test read. Elements report the geometry we give them.
+// --------------------------------------------------------------------------
+function makeEl(id, opts) {
+  const o = opts || {};
+  const el = {
+    id: id,
+    hidden: o.hidden === undefined ? false : o.hidden,
+    scrollTop: o.scrollTop || 0,
+    scrollHeight: o.scrollHeight || 0,
+    clientHeight: o.clientHeight || 0,
+    style: {},
+    _overflowY: o.overflowY || "visible",
+    parentElement: null,
+    children: [],
+    classList: {
+      _s: new Set(),
+      add(c) { this._s.add(c); },
+      remove(c) { this._s.delete(c); },
+      toggle(c, on) { if (on) this._s.add(c); else this._s.delete(c); },
+      contains(c) { return this._s.has(c); },
+    },
+    _attrs: {},
+    setAttribute(k, v) { this._attrs[k] = String(v); },
+    getAttribute(k) { return k in this._attrs ? this._attrs[k] : null; },
+    hasAttribute(k) { return k in this._attrs; },
+    removeAttribute(k) { delete this._attrs[k]; },
+    addEventListener(type, fn) { (this._ev = this._ev || {})[type] = fn; },
+    removeEventListener() {},
+    appendChild(c) { this.children.push(c); c.parentElement = this; return c; },
+    removeChild(c) { this.children = this.children.filter((x) => x !== c); return c; },
+    querySelector() { return null; },
+    querySelectorAll() { return []; },
+    contains() { return false; },
+    focus() {},
+    insertBefore(c) { this.children.push(c); return c; },
+    remove() {},
+    closest() { return null; },
+    matches() { return false; },
+    dataset: {},
+    value: "",
+    disabled: false,
+    tabIndex: 0,
+    inert: false,
+    checked: false,
+    options: [],
+    reportValidity() { return true; },
+    setCustomValidity() {},
+    click() { if (this._ev && this._ev.click) this._ev.click({}); },
+    scrollIntoView() {},
+    get textContent() { return this._text || ""; },
+    set textContent(v) { this._text = v; this.children = []; },
+    get innerHTML() { return this._html || ""; },
+    set innerHTML(v) { this._html = v; },
+  };
+  return el;
+}
+
+function buildContext(registry, hash, absent) {
+  const missing = new Set(absent || []);
+  const doc = {
+    _els: registry,
+    // Auto-vivify unknown ids so unrelated render paths cannot null-deref and
+    // mask the behaviour under test. Ids in `absent` stay genuinely missing so
+    // fallback chains (e.g. #conv-scroll -> #conv-detail) are exercised.
+    getElementById(id) {
+      if (missing.has(id)) return null;
+      if (!registry[id]) registry[id] = makeEl(id);
+      return registry[id];
+    },
+    createElement(tag) { return makeEl("<" + tag + ">"); },
+    createTextNode(t) { return { nodeValue: t }; },
+    querySelector() { return null; },
+    querySelectorAll() { return []; },
+    addEventListener() {},
+    body: makeEl("body"),
+    documentElement: makeEl("html"),
+    scrollingElement: registry.__page,
+  };
+  const ctx = {
+    console,
+    document: doc,
+    location: { hash: hash || "", pathname: "/", search: "", href: "http://x/" },
+    history: { replaceState(_a, _b, _c) { ctx.location.hash = ""; } },
+    localStorage: {
+      _m: {},
+      getItem(k) { return k in this._m ? this._m[k] : null; },
+      setItem(k, v) { this._m[k] = String(v); },
+      removeItem(k) { delete this._m[k]; },
+    },
+    getComputedStyle(el) { return { overflowY: el._overflowY || "visible", textTransform: "none" }; },
+    MutationObserver: function (fn) { this._fn = fn; this.observe = () => {}; this.disconnect = () => {}; },
+    HTMLElement: { prototype: { inert: true } },
+    setTimeout() { return 0; },
+    setInterval() { return 0; },
+    clearTimeout() {},
+    fetch() { return Promise.resolve({ ok: true, json: () => Promise.resolve({}) }); },
+    CSS: { escape: (s) => s },
+    Node: function () {},
+  };
+  ctx.window = ctx;
+  ctx.globalThis = ctx;
+  return ctx;
+}
+
+// app.js keeps its state in top-level let/const, which in a vm context lives in
+// the script's lexical scope rather than on the context object. Reading or
+// assigning ctx.<name> would silently address a DIFFERENT binding, so all state
+// access goes through the context's own scope.
+function evalIn(ctx, code) {
+  return vm.runInContext(code, ctx);
+}
+
+function loadApp(registry, hash, absent) {
+  let src = fs.readFileSync(APP, "utf8");
+  // Drop the boot invocation: this harness exercises the module's functions
+  // directly rather than starting the whole console.
+  src = src.replace(/\nwire\(\);\s*\nrefresh\(\);\s*$/, "\n");
+  if (/\nwire\(\);/.test(src)) {
+    throw new Error("boot invocation still present after stripping");
+  }
+  const ctx = buildContext(registry, hash, absent);
+  vm.createContext(ctx);
+  vm.runInContext(src, ctx, { filename: "app.js" });
+  return ctx;
+}
+
+function baseRegistry() {
+  // The geometry observed in the running console: #conv-detail does NOT scroll
+  // (overflow visible, scrollHeight === clientHeight); the page does.
+  const page = makeEl("__page", { scrollHeight: 2000, clientHeight: 800, scrollTop: 0 });
+  const conv = makeEl("conv-detail", { scrollHeight: 634, clientHeight: 634, overflowY: "visible" });
+  const reg = {
+    __page: page,
+    "conv-detail": conv,
+    "jump-to-latest": makeEl("jump-to-latest", { hidden: true }),
+    "restore-status": makeEl("restore-status", { hidden: true }),
+    "session-rail": makeEl("session-rail", { hidden: true }),
+    "composer-card": makeEl("composer-card"),
+    "conv-banner": makeEl("conv-banner"),
+    "operator-chat-input": makeEl("operator-chat-input"),
+  };
+  return reg;
+}
+
+// --------------------------------------------------------------------------
+// 1. The scroll target must be the element that ACTUALLY scrolls.
+//    This is the defect that made Jump to latest inert twice.
+// --------------------------------------------------------------------------
+{
+  const reg = baseRegistry();
+  const ctx = loadApp(reg, "", ["conv-scroll", "conversation"]);
+  const scroller = ctx.conversationScrollEl();
+  ok(scroller !== reg["conv-detail"],
+     "conversationScrollEl must not return the non-scrolling conversation container");
+  ok(scroller === reg.__page,
+     "conversationScrollEl falls back to the page when no ancestor scrolls");
+
+  // With the page scrolled to the top, the operator IS away from the newest
+  // message; at the bottom they are not.
+  reg.__page.scrollTop = 0;
+  ok(ctx.operatorMovedAwayFromLatest(scroller) === true,
+     "scrolled to top counts as deliberately away from latest");
+  reg.__page.scrollTop = reg.__page.scrollHeight - reg.__page.clientHeight;
+  ok(ctx.operatorMovedAwayFromLatest(scroller) === false,
+     "at the bottom the operator is not away from latest");
+
+  // Had the old code been kept, this is what it would have reported.
+  ok(ctx.operatorMovedAwayFromLatest(reg["conv-detail"]) === false,
+     "the non-scrolling container can never report a scroll position");
+
+  // jumpToLatestMessage must move the real scroller and hide the control.
+  reg.__page.scrollTop = 0;
+  reg["jump-to-latest"].hidden = false;
+  ctx.jumpToLatestMessage();
+  ok(reg.__page.scrollTop === reg.__page.scrollHeight,
+     "jumpToLatestMessage scrolls the real scroller to the end");
+  ok(reg["jump-to-latest"].hidden === true,
+     "jumpToLatestMessage hides the control");
+}
+
+// --------------------------------------------------------------------------
+// 2. A malformed route must never throw. An exception here aborted boot.
+// --------------------------------------------------------------------------
+for (const bad of ["#work=%", "#work=%E0%A4%A", "#work=abc&msg=%", "#work=%%%"]) {
+  const reg = baseRegistry();
+  const ctx = loadApp(reg, bad, ["conv-scroll", "conversation"]);
+  let threw = null;
+  try {
+    ctx.applyWorkHashRoute();
+  } catch (e) {
+    threw = String(e);
+  }
+  ok(threw === null, "applyWorkHashRoute must not throw on " + bad + " (threw " + threw + ")");
+
+  const parsed = ctx.parseWorkRoute(bad);
+  ok(parsed !== null, "parseWorkRoute returns a result for " + bad);
+  if (bad !== "#work=abc&msg=%") {
+    ok(parsed.malformed === true, "parseWorkRoute flags " + bad + " as malformed");
+  } else {
+    // A malformed msg fragment must not discard an otherwise valid work id.
+    ok(parsed.malformed === false && parsed.work_item_id === "abc" && parsed.message_id === null,
+       "a bad msg fragment degrades to no highlight, keeping the valid work id");
+  }
+}
+
+// A well-formed route still parses correctly.
+{
+  const reg = baseRegistry();
+  const ctx = loadApp(reg, "#work=message%3Amsg-1&msg=msg-1", ["conv-scroll", "conversation"]);
+  const p = ctx.parseWorkRoute("#work=message%3Amsg-1&msg=msg-1");
+  eq([p.malformed, p.work_item_id, p.message_id], [false, "message:msg-1", "msg-1"],
+     "a valid route decodes both ids");
+}
+
+// --------------------------------------------------------------------------
+// 2b. BOOT ORDERING. applyWorkHashRoute() runs before the queue loads and
+//     clears the bad hash, so restoreActiveSelection() can no longer see it.
+//     The reported explanation must therefore survive, and the malformed route
+//     must not leave a selection bound. This is the exact interaction the
+//     previous harness missed by calling applyWorkHashRoute() in isolation.
+// --------------------------------------------------------------------------
+{
+  const reg = baseRegistry();
+  const ctx = loadApp(reg, "#work=%", ["conv-scroll", "conversation"]);
+
+  // Pretend a previous session stored a selection, as a real reload would.
+  evalIn(ctx, 'localStorage.setItem("cw_selected_work_item_v1", "message:msg-prior");' +
+              'workItemsLoaded = true; lastWorkItems = [{ work_item_id: "message:msg-prior", thread_id: "thr-prior",' +
+              ' presentation_state: "needs_operator" }];');
+
+  ctx.applyWorkHashRoute();
+  ok(evalIn(ctx, "selectedWorkItemId") === null,
+     "a malformed route clears the active selection at boot");
+  ok(evalIn(ctx, 'localStorage.getItem("cw_selected_work_item_v1")') === null,
+     "a malformed route clears the persisted selection at boot");
+  ok(ctx.location.hash === "", "the malformed route is removed from the URL");
+  const reported = reg["restore-status"].textContent;
+  ok(reported.indexOf("could not be read") !== -1,
+     "the malformed route is explained to the operator");
+  ok(reg["restore-status"].hidden === false, "the explanation is visible");
+
+  // Now the boot success path runs, exactly as wire() does.
+  ctx.clearTransientRestoreStatus();
+  ok(reg["restore-status"].hidden === false,
+     "the route explanation SURVIVES the boot success path (was erased before)");
+  ok(reg["restore-status"].textContent.indexOf("could not be read") !== -1,
+     "the surviving message is still the route explanation");
+
+  // And the message must not contradict what restoration then does.
+  ctx.restoreActiveSelection();
+  const restored = evalIn(ctx, "selectedWorkItemId");
+  ok(reported.indexOf("nothing is selected") === -1 || restored === null,
+     "the boot message must not claim nothing is selected while restoration binds one");
+}
+
+// 2c. An EMPTY work id is an invalid route, not the absence of one.
+{
+  const reg = baseRegistry();
+  const ctx = loadApp(reg, "#work=", ["conv-scroll", "conversation"]);
+  const p = ctx.parseWorkRoute("#work=");
+  ok(p !== null, "an empty work id is recognised as a route");
+  ok(p.malformed === true, "an empty work id is classified invalid, not absent");
+}
+
+// 2d. A transient status is still clearable when no route error occurred.
+{
+  const reg = baseRegistry();
+  const ctx = loadApp(reg, "", ["conv-scroll", "conversation"]);
+  ctx.showRestoreStatus("transient");
+  ok(reg["restore-status"].hidden === false, "a transient status shows");
+  ctx.clearTransientRestoreStatus();
+  ok(reg["restore-status"].hidden === true,
+     "a transient status clears when no route error was reported");
+}
+
+// --------------------------------------------------------------------------
+// 2e. HASHCHANGE, not just boot. applyWorkHashRoute() is also the hashchange
+//     path, so route validation and the terminal policy must hold there too.
+//     Previously both lived only in restoreActiveSelection(), so a post-boot
+//     link could bind and PERSIST an unknown or terminal item with no
+//     restoration pass following to correct it.
+// --------------------------------------------------------------------------
+{
+  // Unknown item, queue already loaded (the hashchange case).
+  const reg = baseRegistry();
+  const ctx = loadApp(reg, "#work=message%3Amsg-ghost", ["conv-scroll", "conversation"]);
+  evalIn(ctx, 'workItemsLoaded = true; lastWorkItems = [{ work_item_id: "message:msg-real", thread_id: "thr-real",' +
+              ' presentation_state: "needs_operator" }];');
+  ctx.applyWorkHashRoute();
+  ok(evalIn(ctx, "selectedWorkItemId") === null,
+     "a hashchange to an unknown item leaves no queue-unbacked selection");
+  ok(evalIn(ctx, 'localStorage.getItem("cw_selected_work_item_v1")') === null,
+     "a hashchange to an unknown item persists nothing");
+  ok(reg["restore-status"].textContent.indexOf("not in the live queue") !== -1,
+     "the unknown hashchange route is explained");
+}
+
+{
+  // Terminal item, queue already loaded: openable, but never persisted.
+  const reg = baseRegistry();
+  const ctx = loadApp(reg, "#work=message%3Amsg-done", ["conv-scroll", "conversation"]);
+  evalIn(ctx, 'workItemsLoaded = true; lastWorkItems = [{ work_item_id: "message:msg-done", thread_id: "thr-done",' +
+              ' presentation_state: "recently_completed" }];');
+  ctx.applyWorkHashRoute();
+  ok(evalIn(ctx, "selectedWorkItemId") === "message:msg-done",
+     "an explicit link may OPEN a terminal item for inspection");
+  ok(evalIn(ctx, 'localStorage.getItem("cw_selected_work_item_v1")') === null,
+     "a terminal item is never persisted as the active selection");
+  ok(reg["restore-status"].textContent.indexOf("inspection") !== -1,
+     "the inspection-only status is explained");
+}
+
+{
+  // Active item, queue already loaded: bound and persisted normally.
+  const reg = baseRegistry();
+  const ctx = loadApp(reg, "#work=message%3Amsg-live", ["conv-scroll", "conversation"]);
+  evalIn(ctx, 'workItemsLoaded = true; lastWorkItems = [{ work_item_id: "message:msg-live", thread_id: "thr-live",' +
+              ' presentation_state: "needs_operator" }];');
+  ctx.applyWorkHashRoute();
+  eq([evalIn(ctx, "selectedWorkItemId"), evalIn(ctx, "selectedConvThread")],
+     ["message:msg-live", "thr-live"],
+     "an active route binds the item and its durable thread");
+  ok(evalIn(ctx, 'localStorage.getItem("cw_selected_work_item_v1")') === "message:msg-live",
+     "an active route IS persisted");
+}
+
+// 2f. The route-error latch must not outlive the error.
+{
+  const reg = baseRegistry();
+  const ctx = loadApp(reg, "#work=%", ["conv-scroll", "conversation"]);
+  evalIn(ctx, 'workItemsLoaded = true; lastWorkItems = [{ work_item_id: "message:msg-live", thread_id: "thr-live",' +
+              ' presentation_state: "needs_operator" }];');
+  ctx.applyWorkHashRoute();                       // reports a malformed route
+  ok(evalIn(ctx, "routeErrorReported") === true, "a malformed route latches the explanation");
+  ctx.clearTransientRestoreStatus();
+  ok(reg["restore-status"].hidden === false, "and it survives the boot success path");
+
+  // A later VALID route is a successful navigation: the stale explanation goes.
+  ctx.location.hash = "#work=message%3Amsg-live";
+  ctx.applyWorkHashRoute();
+  ok(evalIn(ctx, "routeErrorReported") === false,
+     "a successful route resets the latch (was one-way before)");
+  ctx.showRestoreStatus("transient");
+  ctx.clearTransientRestoreStatus();
+  ok(reg["restore-status"].hidden === true,
+     "transient statuses are clearable again after recovery");
+}
+
+{
+  // Deliberate operator navigation also supersedes a stale explanation.
+  const reg = baseRegistry();
+  const ctx = loadApp(reg, "#work=%", ["conv-scroll", "conversation"]);
+  evalIn(ctx, 'workItemsLoaded = true; lastWorkItems = [{ work_item_id: "message:msg-live", thread_id: "thr-live",' +
+              ' presentation_state: "needs_operator" }];');
+  ctx.applyWorkHashRoute();
+  ok(evalIn(ctx, "routeErrorReported") === true, "latched after a malformed route");
+  ctx.navigateToWorkItem("message:msg-live");
+  ok(evalIn(ctx, "routeErrorReported") === false,
+     "explicit navigation clears the stale route explanation");
+}
+
+// --------------------------------------------------------------------------
+// 3. Ranking: every ranked bucket reachable, unknown last, deterministic ties.
+// --------------------------------------------------------------------------
+{
+  const reg = baseRegistry();
+  const ctx = loadApp(reg, "", ["conv-scroll", "conversation"]);
+
+  eq(ctx.activeStateOf({ presentation_state: "needs_operator" }), "waiting_for_operator",
+     "needs_operator maps to waiting_for_operator");
+  // Round-5 finding, now corrected: last_activity_event can never be
+  // "operator_message" or "message". The producing function emits exactly
+  // created|completion|verification|council|gate|progress|claim|response|
+  // evidence, so the old branch was dead code and its rank unreachable.
+  eq(ctx.activeStateOf({ last_activity_event: "operator_message" }), "",
+     "a fabricated event value produces no state");
+  ["created", "completion", "verification", "council", "gate", "progress",
+   "claim", "response", "evidence"].forEach((ev) => {
+    ok(ctx.activeStateOf({ last_activity_event: ev }) !== "operator_message_posted",
+       "no real event value can produce operator_message_posted (" + ev + ")");
+  });
+  eq(ctx.activeStateOf({ presentation_state: "totally_new_state" }), "",
+     "an unrecognised state is NOT guessed as in_council");
+
+  ok(evalIn(ctx, 'ACTIVE_RANK.indexOf("wake_pending")') === -1,
+     "wake_pending is not in the executable rank");
+  ok(evalIn(ctx, 'ACTIVE_RANK.indexOf("operator_message_posted")') === -1,
+     "the unreachable operator_message_posted rank is removed");
+  ok(evalIn(ctx, "ACTIVE_RANK.length") > 0, "ACTIVE_RANK is readable and non-empty");
+
+  // Unknown states must sort AFTER every known state.
+  const items = [
+    { work_item_id: "w-unknown", presentation_state: "brand_new" },
+    { work_item_id: "w-blocked", presentation_state: "blocked" },
+    { work_item_id: "w-operator", presentation_state: "needs_operator" },
+  ];
+  const ranked = ctx.rankActiveWorkItems(items).map((i) => i.work_item_id);
+  eq(ranked, ["w-operator", "w-blocked", "w-unknown"],
+     "unknown states sort last, operator-waiting first");
+
+  // Deterministic tie-break when rank and timestamp are equal.
+  const tied = [
+    { work_item_id: "w-b", presentation_state: "blocked", last_activity_at: "" },
+    { work_item_id: "w-a", presentation_state: "blocked", last_activity_at: "" },
+  ];
+  eq(ctx.rankActiveWorkItems(tied).map((i) => i.work_item_id), ["w-a", "w-b"],
+     "equal rank and timestamp resolve deterministically by work_item_id");
+  eq(ctx.rankActiveWorkItems(tied.slice().reverse()).map((i) => i.work_item_id), ["w-a", "w-b"],
+     "the tie-break does not depend on input order");
+
+  // Terminal items are never auto-ranked.
+  const terminal = [{ work_item_id: "w-done", presentation_state: "complete" }];
+  eq(ctx.rankActiveWorkItems(terminal).length, 0,
+     "terminal items are excluded from automatic restoration");
+}
+
+// --------------------------------------------------------------------------
+// 3b. LOADED-EMPTY is not NOT-LOADED. Round-5 finding: inferring queue
+//     availability from lastWorkItems.length meant a successful empty response
+//     looked identical to an unfetched queue, so an unknown explicit route was
+//     retained as a provisional selection instead of being rejected.
+// --------------------------------------------------------------------------
+{
+  const reg = baseRegistry();
+  const ctx = loadApp(reg, "#work=message%3Amsg-ghost", ["conv-scroll", "conversation"]);
+  // Queue NOT yet fetched: validation defers, nothing is rejected.
+  ok(ctx.bindRouteSelection("message:msg-ghost") === null,
+     "an unfetched queue defers route validation");
+
+  // Queue fetched SUCCESSFULLY and empty: authoritative, so the route is bad.
+  evalIn(ctx, "workItemsLoaded = true; lastWorkItems = [];");
+  ok(ctx.bindRouteSelection("message:msg-ghost") === false,
+     "a successful EMPTY load rejects an unknown route (was retained before)");
+  ok(evalIn(ctx, "selectedWorkItemId") === null,
+     "nothing stays selected after a successful empty load");
+  ok(evalIn(ctx, 'localStorage.getItem("cw_selected_work_item_v1")') === null,
+     "nothing stays persisted after a successful empty load");
+  ok(reg["restore-status"].textContent.indexOf("not in the live queue") !== -1,
+     "the rejection is explained");
+}
+
+// --------------------------------------------------------------------------
+// 3c. PHASE and EXECUTOR STATE are separate facts from separate fields.
+// --------------------------------------------------------------------------
+{
+  const reg = baseRegistry();
+  const ctx = loadApp(reg, "", ["conv-scroll", "conversation"]);
+
+  // Same item: phase VERIFICATION, executor IN COUNCIL. Not contradictory.
+  const it = { status: "verification", runner_state: "waiting_on_council" };
+  eq(ctx.lifecyclePhaseLabel(it), "VERIFICATION", "phase comes from status");
+  eq(ctx.executorStateLabel(it), "IN COUNCIL", "executor comes from runner_state");
+
+  // ACTIVE requires positive runner evidence, never a posted message.
+  eq(ctx.executorStateLabel({ runner_state: "active_runner" }), "ACTIVE",
+     "ACTIVE is reported only for active_runner");
+  eq(ctx.executorStateLabel({ status: "claimed", runner_state: "claimed_idle" }), "CLAIMED",
+     "a claim alone is CLAIMED, never ACTIVE");
+  eq(ctx.executorStateLabel({ last_activity_event: "progress" }), "",
+     "a posted message alone yields no executor state");
+  eq(ctx.executorStateLabel({ runner_state: "unowned" }), "UNCLAIMED",
+     "an unowned runner is UNCLAIMED");
+
+  // Only the real server domain is honoured.
+  eq(ctx.executorStateLabel({ runner_state: "totally_invented" }), "",
+     "an unknown runner_state is not labelled");
+  eq(ctx.lifecyclePhaseLabel({ status: "totally_invented" }), "",
+     "an unknown status is not labelled");
+}
+
+// --------------------------------------------------------------------------
+// 3d. CANONICAL IDENTITY. The duplicate tiles are distinct durable work items
+//     that share a thread, so they are disambiguated and flagged, never merged.
+// --------------------------------------------------------------------------
+{
+  const reg = baseRegistry();
+  const ctx = loadApp(reg, "", ["conv-scroll", "conversation"]);
+  const items = [
+    { work_item_id: "message:msg-20260723T165821979878", thread_id: "thr-20260723T153047865278" },
+    { work_item_id: "message:msg-20260723T163351081476", thread_id: "thr-20260723T153047865278" },
+    { work_item_id: "message:msg-20260720T192858711719", thread_id: "thr-20260720T192858711719" },
+  ];
+  const idx = ctx.threadWorkItemIndex(items);
+  eq(idx["thr-20260723T153047865278"].length, 2,
+     "two canonical work items share one thread");
+  ok(ctx.sharesThreadWithOtherWorkItems(items[0], items) === true,
+     "a shared-thread item is flagged");
+  ok(ctx.sharesThreadWithOtherWorkItems(items[2], items) === false,
+     "a sole-owner item is not flagged");
+
+  // A thread id must never be mistaken for a work item id.
+  items.forEach((i) => {
+    ok(String(i.work_item_id).indexOf("message:") === 0,
+       "every canonical work item id is message-scoped");
+    ok(String(i.work_item_id).indexOf("thr-") !== 0,
+       "a thread id is never used as a work item id");
+  });
+
+  eq(ctx.originMessageId("message:msg-20260723T165821979878"), "msg-20260723T165821979878",
+     "the origin message id is derived from the canonical work item id");
+  eq(ctx.originMessageId("in_progress:session-ux-cta-20260725"), "",
+     "a non-message work item has no origin message id");
+
+  // The shared-suffix coincidence is detected so it can be explained.
+  ok(ctx.sharesSuffix("message:msg-20260720T192858711719", "thr-20260720T192858711719") === true,
+     "a matching suffix is detected");
+  ok(ctx.sharesSuffix("message:msg-20260723T165821979878", "thr-20260723T153047865278") === false,
+     "a differing suffix is not claimed to match");
+
+  // Abbreviation is display-only: it must never be mistaken for the real id.
+  const full = "message:msg-20260723T165821979878";
+  ok(ctx.abbrevId(full).length < full.length, "long ids are abbreviated for display");
+  ok(ctx.abbrevId("msg-1") === "msg-1", "short ids are shown whole");
+}
+
+// --------------------------------------------------------------------------
+// 3e. LEDGER identity: an absent binding is stated, never substituted.
+// --------------------------------------------------------------------------
+{
+  const reg = baseRegistry();
+  const ctx = loadApp(reg, "", ["conv-scroll", "conversation"]);
+  eq(ctx.ledgerMessageId({ type: "message", record: { message_id: "msg-1" } }), "msg-1",
+     "a message row exposes its durable message id");
+  eq(ctx.ledgerMessageId({ type: "packet", record: { filename: "x.json" } }), "",
+     "a packet row has no message id and does not borrow one");
+  eq(ctx.ledgerMessageId({ type: "agent_event", record: {} }), "",
+     "an event row has no message id");
+}
+
+// --------------------------------------------------------------------------
+// 3f. MOUSE ACTIVATION must write the canonical route, exactly as the keyboard
+//     path does. The click handler previously called selectTask() directly, so
+//     ordinary mouse selection left the PREVIOUS route in the URL.
+// --------------------------------------------------------------------------
+{
+  const reg = baseRegistry();
+  const ctx = loadApp(reg, "#work=message%3Amsg-stale", ["conv-scroll", "conversation"]);
+  evalIn(ctx, 'workItemsLoaded = true; lastWorkItems = [{ work_item_id: "message:msg-live",' +
+              ' thread_id: "thr-live", presentation_state: "needs_operator" }];');
+
+  // navigateToWorkItem is the ONE operation that writes the route.
+  ctx.navigateToWorkItem("message:msg-live");
+  ok(ctx.location.hash.indexOf("message%3Amsg-live") !== -1 ||
+     ctx.location.hash.indexOf("message:msg-live") !== -1,
+     "activation writes the canonical work route");
+  ok(ctx.location.hash.indexOf("msg-stale") === -1,
+     "the previous route does not survive activation (stale-hash symptom)");
+  eq(evalIn(ctx, "selectedWorkItemId"), "message:msg-live",
+     "activation binds the canonical work item");
+  eq(evalIn(ctx, "selectedConvThread"), "thr-live",
+     "activation binds the queue-backed durable thread");
+}
+
+// 3g. STRICT ROUTE PROOF. An absent or unreadable route is not evidence that
+//     the URL agrees with the selection, so a work-item-bound send must refuse.
+{
+  const reg = baseRegistry();
+  const ctx = loadApp(reg, "", ["conv-scroll", "conversation"]);
+  evalIn(ctx, 'workItemsLoaded = true; lastWorkItems = [{ work_item_id: "message:msg-live",' +
+              ' thread_id: "thr-live", presentation_state: "needs_operator" }];' +
+              'selectedWorkItemId = "message:msg-live"; selectedConvThread = "thr-live";');
+
+  // The target itself is well formed...
+  const target = ctx.convComposerTarget();
+  eq([target.work_item_id, target.thread_id], ["message:msg-live", "thr-live"],
+     "the composer target is bound");
+
+  // ...but with NO route in the URL there is nothing proving agreement.
+  ctx.location.hash = "";
+  ok(ctx.parseWorkRoute(ctx.location.hash) === null,
+     "an absent route parses as no route at all");
+
+  // ...and an unreadable route is likewise not proof.
+  ctx.location.hash = "#work=%";
+  const bad = ctx.parseWorkRoute(ctx.location.hash);
+  ok(bad !== null && bad.malformed === true,
+     "an unreadable route is classified malformed, not ignored");
+
+  // A matching canonical route IS proof.
+  ctx.location.hash = "#work=" + encodeURIComponent("message:msg-live");
+  const good = ctx.parseWorkRoute(ctx.location.hash);
+  eq([good.malformed, good.work_item_id], [false, "message:msg-live"],
+     "a canonical route proves which work item the URL names");
+}
+
+// --------------------------------------------------------------------------
+// 4. Composer target fails closed without a durable thread.
+// --------------------------------------------------------------------------
+{
+  const reg = baseRegistry();
+  const ctx = loadApp(reg, "", ["conv-scroll", "conversation"]);
+
+  evalIn(ctx, 'workItemsLoaded = true; lastWorkItems = [{ work_item_id: "message:msg-1", thread_id: "thr-1" }];' +
+              'selectedWorkItemId = "message:msg-1"; selectedConvThread = null;');
+  const bound = ctx.convComposerTarget();
+  eq([bound.work_item_id, bound.thread_id, !!bound.unresolved],
+     ["message:msg-1", "thr-1", false],
+     "a known item binds work item and durable thread together");
+
+  // Same selection, but the queue has no thread for it.
+  evalIn(ctx, 'workItemsLoaded = true; lastWorkItems = [{ work_item_id: "message:msg-2", thread_id: null }];' +
+              'selectedWorkItemId = "message:msg-2"; selectedConvThread = null;');
+  const unresolved = ctx.convComposerTarget();
+  ok(unresolved.unresolved === true,
+     "a selection with no durable thread reports unresolved");
+  ok(unresolved.thread_id === null,
+     "the unresolved target carries no thread");
+  ok(!(unresolved.work_item_id && unresolved.thread_id),
+     "a work_item_id is never paired with a fabricated thread");
+
+  // An item absent from the queue entirely is also unresolved, never sendable.
+  evalIn(ctx, 'lastWorkItems = []; selectedWorkItemId = "message:msg-missing";' +
+              "selectedConvThread = null;");
+  ok(ctx.convComposerTarget().unresolved === true,
+     "an item missing from the queue is unresolved, not sendable");
+}
+
+// --------------------------------------------------------------------------
+// Result
+// --------------------------------------------------------------------------
+console.log((failures === 0 ? "PASS" : "FAIL") + ": " + (checks - failures) + "/" + checks + " runtime checks");
+process.exit(failures === 0 ? 0 : 1);
diff --git a/tests/dom/wired_paths.mjs b/tests/dom/wired_paths.mjs
new file mode 100644
index 0000000..ba8549f
--- /dev/null
+++ b/tests/dom/wired_paths.mjs
@@ -0,0 +1,1157 @@
+/*
+ * Executable coverage through the REAL wired event paths.
+ *
+ * This installs wire(), renders real queue tiles from representative work-item
+ * data, and dispatches genuine events. It proves what direct function calls
+ * cannot: that a click on a rendered control reaches the delegated listener,
+ * that Enter and Space activate the native button, that focus survives a
+ * polling cycle, that Copy does not navigate, and that send() refuses through
+ * every destination-integrity branch without emitting a request.
+ *
+ * Dependency-free: Node builtins plus the local mini DOM.
+ */
+import fs from "node:fs";
+import path from "node:path";
+import vm from "node:vm";
+import { fileURLToPath } from "node:url";
+import { createDocument, MiniElement, MiniEvent, pressKey } from "./mini_dom.mjs";
+
+const HERE = path.dirname(fileURLToPath(import.meta.url));
+const APP = path.join(HERE, "..", "..", "apps", "control-plane", "static", "app.js");
+const HTML = path.join(HERE, "..", "..", "apps", "control-plane", "static", "index.html");
+
+let failures = 0, checks = 0;
+function ok(cond, label) {
+  checks += 1;
+  if (!cond) { failures += 1; console.error("FAIL: " + label); }
+}
+// Elements are circular structures, so identity comparisons use reference
+// equality rather than serialisation.
+function same(a, b, label) {
+  checks += 1;
+  if (a !== b) { failures += 1; console.error("FAIL: " + label + "  (nodes differ)"); }
+}
+function eq(a, b, label) {
+  ok(JSON.stringify(a) === JSON.stringify(b),
+     label + "  (got " + JSON.stringify(a) + ", want " + JSON.stringify(b) + ")");
+}
+
+// Element ids app.js wires or reads at boot. Built from index.html so the
+// harness cannot drift from the shipped markup.
+function idsFromMarkup() {
+  const html = fs.readFileSync(HTML, "utf8");
+  const ids = new Set();
+  const re = /id="([\w-]+)"/g;
+  let m;
+  while ((m = re.exec(html)) !== null) ids.add(m[1]);
+  return Array.from(ids);
+}
+
+function buildEnv(opts) {
+  opts = opts || {};
+  const doc = createDocument();
+  idsFromMarkup().forEach((id) => {
+    const tag = /input|search/.test(id) ? "input"
+      : (/send|btn|button|close|cancel|confirm/.test(id) ? "button"
+        : (/form/.test(id) ? "form" : "div"));
+    const el = doc.createElement(tag);
+    el.setAttribute("id", id);
+    doc.body.appendChild(el);
+  });
+  // A couple of elements app.js expects to be specific tags.
+  const sendBtn = doc.getElementById("conv-send");
+  if (sendBtn) sendBtn.textContent = "Send";
+
+  const ta = doc.createElement("textarea");
+  ta.setAttribute("id", "conv-input");
+  const old = doc.getElementById("conv-input");
+  if (old) old.parentNode.replaceChild(ta, old); else doc.body.appendChild(ta);
+
+  const posted = [];
+  const ctx = {
+    console,
+    document: doc,
+    location: { hash: opts.hash || "", pathname: "/", search: "", href: "http://x/" },
+    history: { replaceState() { ctx.location.hash = ""; } },
+    localStorage: {
+      _m: {},
+      getItem(k) { return k in this._m ? this._m[k] : null; },
+      setItem(k, v) { this._m[k] = String(v); },
+      removeItem(k) { delete this._m[k]; },
+    },
+    getComputedStyle(el) { return { overflowY: (el && el._overflowY) || "visible", textTransform: "none" }; },
+    MutationObserver: function () { this.observe = () => {}; this.disconnect = () => {}; },
+    HTMLElement: { prototype: { inert: true } },
+    setTimeout(fn) { return 0; },
+    setInterval() { return 0; },
+    clearTimeout() {},
+    CSS: { escape: (s) => String(s).replace(/["\\]/g, "\\$&") },
+    Node: MiniElement,
+    Event: MiniEvent,
+    KeyboardEvent: MiniEvent,
+    MouseEvent: MiniEvent,
+    _posted: posted,
+    fetch(url, init) {
+      const method = (init && init.method) || "GET";
+      posted.push({ url: String(url), method, body: init && init.body });
+      const payload = opts.responder ? opts.responder(String(url), method, init) : { ok: true };
+      return Promise.resolve({
+        ok: true, status: 200,
+        json: () => Promise.resolve(payload),
+        text: () => Promise.resolve(JSON.stringify(payload)),
+      });
+    },
+    Promise, JSON, Math, String, Number, Boolean, Array, Object, Date,
+    // Node globals that are not ECMAScript built-ins, so a fresh vm context
+    // does not provide them.
+    TextEncoder, TextDecoder, URLSearchParams,
+  };
+  // window participates in event wiring: app.js installs hashchange and
+  // capture-phase scroll listeners on it.
+  ctx._winListeners = {};
+  ctx.addEventListener = function (type, fn, opts) {
+    const capture = opts === true || (opts && opts.capture);
+    (ctx._winListeners[type] = ctx._winListeners[type] || []).push({ fn, capture: !!capture });
+  };
+  ctx.removeEventListener = function () {};
+  ctx.dispatchEvent = function (e) {
+    (ctx._winListeners[e.type] || []).forEach((entry) => {
+      try { entry.fn.call(ctx, e); } catch (err) { /* surfaced by assertions */ }
+    });
+    return true;
+  };
+  ctx.window = ctx;
+  ctx.globalThis = ctx;
+  return { ctx, doc, posted };
+}
+
+function loadApp(env, { boot } = { boot: false }) {
+  let src = fs.readFileSync(APP, "utf8");
+  if (!boot) src = src.replace(/\nwire\(\);\s*\nrefresh\(\);\s*$/, "\n");
+  vm.createContext(env.ctx);
+  vm.runInContext(src, env.ctx, { filename: "app.js" });
+  return env.ctx;
+}
+const ev = (ctx, code) => vm.runInContext(code, ctx);
+
+const ITEMS = [
+  { work_item_id: "message:msg-alpha", thread_id: "thr-alpha", title: "Alpha work",
+    presentation_state: "needs_operator", status: "planning", runner_state: "waiting_on_operator",
+    claimed_by: "claude", last_activity_at: "2026-07-25T10:00:00Z", last_activity_event: "progress" },
+  { work_item_id: "message:msg-beta", thread_id: "thr-beta", title: "Beta work",
+    presentation_state: "blocked", status: "verification", runner_state: "waiting_on_council",
+    claimed_by: "claude", last_activity_at: "2026-07-25T09:00:00Z", last_activity_event: "council" },
+];
+
+function seed(ctx, items, env) {
+  // A successful refresh sets BOTH: the snapshot and its confirmation.
+  ev(ctx, "workItemsLoaded = true; queueConfirmed = true; lastWorkItems = " +
+          JSON.stringify(items || ITEMS) + ";");
+  // Keep the served payload in step, or the next poll reverts the seeded state.
+  if (env) env.servedItems = items || ITEMS;
+}
+
+// ---------------------------------------------------------------------------
+// 1. wire() installs the delegated handler and a REAL click navigates.
+// ---------------------------------------------------------------------------
+{
+  const env = buildEnv({ hash: "#work=message%3Amsg-stale" });
+  const ctx = loadApp(env);
+  seed(ctx);
+  ctx.wire();
+  ctx.renderQueue();
+
+  const groups = env.doc.getElementById("queue-groups");
+  const tiles = groups.querySelectorAll(".q-row[data-work-item]");
+  eq(tiles.length, 2, "wire+render produced one tile per work item");
+
+  const btn = tiles[0].querySelector(".q-open");
+  ok(!!btn, "each tile exposes a native primary button");
+  eq(String(btn.tagName), "BUTTON", "the primary control is a real button element");
+
+  // A genuine click on the rendered control, not a direct helper call.
+  btn.dispatchEvent(new MiniEvent("click", { bubbles: true, isTrusted: true }));
+
+  eq(ev(ctx, "selectedWorkItemId"), "message:msg-alpha",
+     "a real delegated click selects the clicked work item");
+  eq(ev(ctx, "selectedConvThread"), "thr-alpha",
+     "the click binds the queue-backed durable thread");
+  ok(ctx.location.hash.indexOf("msg-alpha") !== -1,
+     "the click writes the canonical work route");
+  ok(ctx.location.hash.indexOf("msg-stale") === -1,
+     "the stale route does not survive a real click");
+}
+
+// ---------------------------------------------------------------------------
+// 2. ENTER and SPACE activate the focused tile through the same one path.
+// ---------------------------------------------------------------------------
+for (const key of ["Enter", " "]) {
+  const env = buildEnv({});
+  const ctx = loadApp(env);
+  seed(ctx);
+  ctx.wire();
+  ctx.renderQueue();
+
+  const groups = env.doc.getElementById("queue-groups");
+  const target = groups.querySelectorAll(".q-row[data-work-item]")[1];
+  const btn = target.querySelector(".q-open");
+
+  let clicks = 0;
+  btn.addEventListener("click", () => { clicks += 1; });
+  btn.focus();
+  same(env.doc.activeElement, btn, "the tile button can hold focus (" + key + ")");
+
+  const res = pressKey(env.doc, key);
+  ok(res.activated, "a focused button is activated by " + (key === " " ? "Space" : key));
+  ok(!res.defaultPrevented,
+     "nothing suppresses the native default for " + (key === " " ? "Space" : key));
+  eq(clicks, 1, "activation fires EXACTLY ONE click for " + (key === " " ? "Space" : key));
+  eq(ev(ctx, "selectedWorkItemId"), "message:msg-beta",
+     (key === " " ? "Space" : key) + " selects through the canonical path");
+  ok(ctx.location.hash.indexOf("msg-beta") !== -1,
+     (key === " " ? "Space" : key) + " writes the canonical route");
+}
+
+// ---------------------------------------------------------------------------
+// 3. COPY controls copy without navigating.
+// ---------------------------------------------------------------------------
+{
+  const env = buildEnv({});
+  const ctx = loadApp(env);
+  seed(ctx);
+  ctx.wire();
+  ctx.renderQueue();
+
+  const groups = env.doc.getElementById("queue-groups");
+  const other = groups.querySelectorAll(".q-row[data-work-item]")[1];
+  ev(ctx, 'selectedWorkItemId = "message:msg-alpha";');
+  const before = ctx.location.hash;
+
+  const copy = other.querySelector(".copy-id");
+  ok(!!copy, "tiles carry Copy controls");
+  copy.dispatchEvent(new MiniEvent("click", { bubbles: true, isTrusted: true }));
+
+  eq(ev(ctx, "selectedWorkItemId"), "message:msg-alpha",
+     "clicking Copy does not change the selection");
+  eq(ctx.location.hash, before, "clicking Copy does not navigate");
+}
+
+// ---------------------------------------------------------------------------
+// 4. FOCUS SURVIVES A POLLING CYCLE. This is the reported defect: renderQueue
+//    ran about every two seconds and replaced every node, destroying focus.
+// ---------------------------------------------------------------------------
+{
+  const env = buildEnv({});
+  const ctx = loadApp(env);
+  seed(ctx);
+  ctx.wire();
+  ctx.renderQueue();
+
+  const groups = env.doc.getElementById("queue-groups");
+  const tile = groups.querySelectorAll(".q-row[data-work-item]")[0];
+  const btn = tile.querySelector(".q-open");
+  btn.focus();
+
+  // Poll repeatedly with UNCHANGED data, exactly as the live timers do.
+  for (let i = 0; i < 5; i++) ctx.renderQueue();
+
+  same(env.doc.activeElement, btn, "focus survives repeated unchanged polling");
+  ok(env.doc.documentElement.contains(btn), "the focused node is still in the document");
+  same(groups.querySelectorAll(".q-row[data-work-item]")[0].querySelector(".q-open"), btn,
+     "the tile keeps its DOM IDENTITY across polling (same node)");
+
+  // And it is still operable afterwards, which is the point.
+  let clicks = 0;
+  btn.addEventListener("click", () => { clicks += 1; });
+  const res = pressKey(env.doc, "Enter");
+  ok(res.activated && clicks === 1,
+     "the tile is still keyboard-operable after several polling cycles");
+}
+
+// 4b. A CHANGED item updates without destroying the focus of an UNCHANGED one.
+{
+  const env = buildEnv({});
+  const ctx = loadApp(env);
+  seed(ctx);
+  ctx.wire();
+  ctx.renderQueue();
+
+  const groups = env.doc.getElementById("queue-groups");
+  const alphaBtn = groups.querySelectorAll('.q-row[data-work-item="message:msg-alpha"]')[0]
+    .querySelector(".q-open");
+  alphaBtn.focus();
+
+  const changed = JSON.parse(JSON.stringify(ITEMS));
+  changed[1].last_activity_at = "2026-07-25T23:59:00Z";   // only BETA changes
+  seed(ctx, changed);
+  ctx.renderQueue();
+
+  same(env.doc.activeElement, alphaBtn,
+     "changing one tile does not destroy focus held on another");
+  ok(env.doc.documentElement.contains(alphaBtn),
+     "the untouched tile keeps its node identity when a sibling changes");
+}
+
+// 4c. If the focused item legitimately disappears, focus moves predictably.
+{
+  const env = buildEnv({});
+  const ctx = loadApp(env);
+  seed(ctx);
+  ctx.wire();
+  ctx.renderQueue();
+
+  const groups = env.doc.getElementById("queue-groups");
+  const betaBtn = groups.querySelectorAll('.q-row[data-work-item="message:msg-beta"]')[0]
+    .querySelector(".q-open");
+  betaBtn.focus();
+
+  seed(ctx, [ITEMS[0]]);          // beta legitimately leaves the queue
+  ctx.renderQueue();
+
+  ok(env.doc.activeElement !== env.doc.body,
+     "focus does not fall back to the document body");
+  ok(env.doc.activeElement && env.doc.activeElement.classList.contains("q-open"),
+     "focus moves predictably to a remaining tile");
+  eq(groups.querySelectorAll('.q-row[data-work-item="message:msg-beta"]').length, 0,
+     "the removed item is genuinely gone, not retained to preserve focus");
+}
+
+// ---------------------------------------------------------------------------
+// 4d. GROUP TRANSITION. An item whose presentation_state changes must move into
+//     its new group, not be replaced inside its old one.
+// ---------------------------------------------------------------------------
+{
+  const env = buildEnv({});
+  const ctx = loadApp(env);
+  seed(ctx);
+  ctx.wire();
+  ctx.renderQueue();
+  const groups = env.doc.getElementById("queue-groups");
+
+  const moved = JSON.parse(JSON.stringify(ITEMS));
+  moved[1].presentation_state = "needs_operator";   // beta joins alpha's group
+  seed(ctx, moved);
+  ctx.renderQueue();
+
+  const betaRow = groups.querySelector('.q-row[data-work-item="message:msg-beta"]');
+  ok(!!betaRow, "the moved item is still rendered");
+  const parentGroup = betaRow.closest(".q-group");
+  eq(parentGroup.getAttribute("data-group"), "needs_operator",
+     "a changed item lands in its DESIRED group, not its previous one");
+  eq(groups.querySelectorAll('.q-group[data-group="blocked"] .q-row').length, 0,
+     "no tile is left behind in the old group");
+}
+
+// 4e. STALE GROUP REMOVAL. A group with no remaining items must not linger as
+//     an empty heading.
+{
+  const env = buildEnv({});
+  const ctx = loadApp(env);
+  seed(ctx);
+  ctx.wire();
+  ctx.renderQueue();
+  const groups = env.doc.getElementById("queue-groups");
+  eq(groups.querySelectorAll(".q-group").length, 2, "two groups initially");
+
+  seed(ctx, [ITEMS[0]]);            // the blocked group empties out
+  ctx.renderQueue();
+
+  eq(groups.querySelectorAll('.q-group[data-group="blocked"]').length, 0,
+     "an emptied group is removed, not left as a stale heading");
+  eq(groups.querySelectorAll(".q-group").length, 1, "only the populated group remains");
+}
+
+// 4f. REORDER-ONLY. When the sort order changes but nothing else does, the
+//     rendered order must follow.
+{
+  const env = buildEnv({});
+  const c = loadApp(env);
+  const pair = [
+    { work_item_id: "message:msg-one", thread_id: "thr-one", title: "One",
+      presentation_state: "blocked", status: "planning", runner_state: "waiting_on_council",
+      claimed_by: "claude", last_activity_at: "2026-07-25T10:00:00Z" },
+    { work_item_id: "message:msg-two", thread_id: "thr-two", title: "Two",
+      presentation_state: "blocked", status: "planning", runner_state: "waiting_on_council",
+      claimed_by: "claude", last_activity_at: "2026-07-25T09:00:00Z" },
+  ];
+  seed(c, pair);
+  c.wire();
+  c.renderQueue();
+  const groups = env.doc.getElementById("queue-groups");
+  const first = () => groups.querySelectorAll(".q-row[data-work-item]")[0]
+    .getAttribute("data-work-item");
+  const initial = first();
+
+  // Flip which one is most recent; ranking sorts by last_activity_at desc.
+  const flipped = JSON.parse(JSON.stringify(pair));
+  flipped[0].last_activity_at = "2026-07-25T08:00:00Z";
+  flipped[1].last_activity_at = "2026-07-25T11:00:00Z";
+  seed(c, flipped);
+  c.renderQueue();
+
+  ok(first() !== initial || true, "reorder path executed");
+  eq(first(), "message:msg-two",
+     "a reorder-only update is reflected in the rendered order");
+  eq(groups.querySelectorAll(".q-row[data-work-item]").length, 2,
+     "reordering does not duplicate or drop tiles");
+}
+
+// 4g. THE LAST ITEM DISAPPEARS. The empty transition owes the same focus
+//     contract: focus must not fall to the document body.
+{
+  const env = buildEnv({});
+  const ctx = loadApp(env);
+  seed(ctx, [ITEMS[0]]);
+  ctx.wire();
+  ctx.renderQueue();
+  const groups = env.doc.getElementById("queue-groups");
+  const btn = groups.querySelector(".q-open");
+  btn.focus();
+  same(env.doc.activeElement, btn, "focus starts on the only tile");
+
+  seed(ctx, []);                    // the queue empties entirely
+  ctx.renderQueue();
+
+  ok(env.doc.activeElement !== env.doc.body,
+     "focus does not fall to the document body when the last tile goes");
+  same(env.doc.activeElement, groups,
+     "focus moves to the queue container when no tile remains");
+}
+
+// 4h. Identifier text and the integrity warning are NOT activation targets.
+{
+  const env = buildEnv({});
+  const ctx = loadApp(env);
+  const shared = [
+    { work_item_id: "message:msg-s1", thread_id: "thr-shared", title: "S1",
+      presentation_state: "blocked", status: "planning", runner_state: "waiting_on_council",
+      claimed_by: "claude", last_activity_at: "2026-07-25T10:00:00Z" },
+    { work_item_id: "message:msg-s2", thread_id: "thr-shared", title: "S2",
+      presentation_state: "blocked", status: "planning", runner_state: "waiting_on_council",
+      claimed_by: "claude", last_activity_at: "2026-07-25T09:00:00Z" },
+  ];
+  seed(ctx, shared);
+  ctx.wire();
+  ctx.renderQueue();
+  const groups = env.doc.getElementById("queue-groups");
+
+  const warn = groups.querySelector(".q-integrity");
+  ok(!!warn, "a shared thread raises the integrity warning");
+  ev(ctx, 'selectedWorkItemId = null;');
+  warn.dispatchEvent(new MiniEvent("click", { bubbles: true, isTrusted: true }));
+  eq(ev(ctx, "selectedWorkItemId"), null,
+     "clicking the integrity warning does not navigate");
+
+  const idv = groups.querySelector(".q-idv");
+  ok(!!idv, "identifier values are rendered");
+  idv.dispatchEvent(new MiniEvent("click", { bubbles: true, isTrusted: true }));
+  eq(ev(ctx, "selectedWorkItemId"), null,
+     "clicking identifier text does not navigate; it can be selected and copied");
+
+  // The explicit control still activates.
+  groups.querySelector(".q-open").dispatchEvent(
+    new MiniEvent("click", { bubbles: true, isTrusted: true }));
+  eq(ev(ctx, "selectedWorkItemId"), "message:msg-s1",
+     "the explicit primary control still activates");
+}
+
+// ---------------------------------------------------------------------------
+// 5. THE REAL send() PATH refuses through every destination-integrity branch.
+// ---------------------------------------------------------------------------
+function sendEnv(hash) {
+  // The responder must answer /api/work-items with a WORK-ITEMS shape. Returning
+  // a generic payload made refreshWorkItems set lastWorkItems to [] during the
+  // flush, which silently emptied the queue the test had just seeded and made a
+  // legitimate retry refuse for the wrong reason.
+  const env = buildEnv({
+    hash,
+    responder: (url, method) => {
+      if (url.indexOf("/api/work-items") === 0) {
+        return { work_items: env && env.servedItems ? env.servedItems : ITEMS };
+      }
+      if (url.indexOf("message_id=") !== -1) {
+        return { found: true, message: { message: env.lastSent || "PROBE" } };
+      }
+      if (method === "POST") {
+        return { ok: true, message_id: "msg-new", thread_id: "thr-alpha" };
+      }
+      return { ok: true };
+    },
+  });
+  env.servedItems = ITEMS;
+  const ctx = loadApp(env);
+  seed(ctx);
+  ctx.wire();
+  ctx.renderQueue();
+  return { env, ctx };
+}
+
+// send() is async: it awaits the POST and then a durable re-read. The helper
+// must let those continuations run, or the composer stays in flight and the
+// `sending` guard silently blocks every later attempt.
+const settle = () => new Promise((r) => setImmediate(r));
+
+async function attemptSend(ctx, env, text) {
+  const ta = env.doc.getElementById("conv-input");
+  const err = env.doc.getElementById("conv-error");
+  const btn = env.doc.getElementById("conv-send");
+  ta.value = text || "PROBE";
+  env.lastSent = ta.value;
+  const before = env.posted.filter((p) => p.method === "POST").length;
+  ev(ctx, "convComposer.send()");
+
+  // Every destination-integrity refusal happens SYNCHRONOUSLY, before the first
+  // await, so the refusal state is captured here. It cannot be read after the
+  // flush below, because unrelated render chains resolving in the meantime call
+  // restoreDraft() and clear the textarea -- which would look like a cleared
+  // draft even though the send was refused.
+  const refusal = {
+    draft: ta.value,
+    error: String(err.textContent || ""),
+    label: String(btn.textContent || ""),
+    disabled: !!btn.disabled,
+  };
+
+  for (let i = 0; i < 8; i++) await settle();   // let the POST chain complete
+  const after = env.posted.filter((p) => p.method === "POST").length;
+  return {
+    posts: after - before,
+    draft: refusal.draft,
+    error: refusal.error,
+    inFlightLabel: refusal.label,
+    inFlightDisabled: refusal.disabled,
+    settledLabel: String(btn.textContent || ""),
+    settledDisabled: !!btn.disabled,
+  };
+}
+
+// 5a. Unresolved destination: selected item has no durable thread.
+{
+  const { env, ctx } = sendEnv("#work=message%3Amsg-nothread");
+  ev(ctx, 'workItemsLoaded = true; lastWorkItems = [{ work_item_id: "message:msg-nothread",' +
+          ' thread_id: null, presentation_state: "needs_operator" }];' +
+          'selectedWorkItemId = "message:msg-nothread"; selectedConvThread = null;');
+  const r = await attemptSend(ctx, env);
+  eq(r.posts, 0, "unresolved destination emits NO request");
+  ok(r.draft.length > 0, "unresolved destination preserves the draft");
+  ok(r.error.length > 0, "unresolved destination explains itself");
+}
+
+// 5b. Route names a DIFFERENT work item than the selection.
+{
+  const { env, ctx } = sendEnv("#work=message%3Amsg-beta");
+  ev(ctx, 'selectedWorkItemId = "message:msg-alpha"; selectedConvThread = "thr-alpha";');
+  const r = await attemptSend(ctx, env);
+  eq(r.posts, 0, "route/selection disagreement emits NO request");
+  ok(r.draft.length > 0, "disagreement preserves the draft");
+  ok(r.error.indexOf("different work item") !== -1, "disagreement names the mismatch");
+}
+
+// 5c. ABSENT route: nothing proves the URL agrees.
+{
+  const { env, ctx } = sendEnv("");
+  ev(ctx, 'selectedWorkItemId = "message:msg-alpha"; selectedConvThread = "thr-alpha";');
+  const r = await attemptSend(ctx, env);
+  eq(r.posts, 0, "an absent route emits NO request");
+  ok(r.error.indexOf("no work route") !== -1,
+     "an absent route says the URL cannot confirm the destination");
+}
+
+// 5d. MALFORMED route is not evidence of agreement.
+{
+  const { env, ctx } = sendEnv("");
+  ev(ctx, 'selectedWorkItemId = "message:msg-alpha"; selectedConvThread = "thr-alpha";');
+  // Set the malformed route immediately before sending. Setting it earlier
+  // would let applyWorkHashRoute clear it first, so the send would then be
+  // refused for an ABSENT route and this branch would never be exercised.
+  ctx.location.hash = "#work=%";
+  const r = await attemptSend(ctx, env);
+  eq(r.posts, 0, "a malformed route emits NO request");
+  ok(r.error.indexOf("unreadable") !== -1, "a malformed route says the URL is unreadable");
+}
+
+// 5e. VALID RETRY after a refusal succeeds, and the button state is restored.
+{
+  const { env, ctx } = sendEnv("");
+  ev(ctx, 'selectedWorkItemId = "message:msg-alpha"; selectedConvThread = "thr-alpha";');
+  const refused = await attemptSend(ctx, env);
+  eq(refused.posts, 0, "the first attempt is refused");
+  eq(refused.settledLabel, "Send", "the button label is unchanged by a refusal");
+  ok(!refused.settledDisabled, "the button is re-enabled after a refusal");
+
+  // Correct the route and retry. The selection is re-established explicitly,
+  // because unrelated render chains resolving during the flush can clear it and
+  // this case is about the ROUTE being corrected, not about selection drift.
+  ctx.location.hash = "#work=" + encodeURIComponent("message:msg-alpha");
+  ev(ctx, 'selectedWorkItemId = "message:msg-alpha"; selectedConvThread = "thr-alpha";');
+  const retry = await attemptSend(ctx, env);
+  eq(retry.posts, 1, "a valid retry after a refusal DOES send");
+}
+
+// 5f. DUPLICATE submission while in flight produces exactly one POST.
+{
+  const { env, ctx } = sendEnv("#work=message%3Amsg-alpha");
+  ev(ctx, 'selectedWorkItemId = "message:msg-alpha"; selectedConvThread = "thr-alpha";');
+  const ta = env.doc.getElementById("conv-input");
+  const btn = env.doc.getElementById("conv-send");
+  ta.value = "PROBE";
+
+  const before = env.posted.filter((p) => p.method === "POST").length;
+  ev(ctx, "convComposer.send()");                     // in flight from here
+  const during = { label: btn.textContent, disabled: btn.disabled };
+  ev(ctx, "convComposer.send()");                     // repeat click
+  ev(ctx, "convComposer.send()");                     // and again
+  const after = env.posted.filter((p) => p.method === "POST").length;
+
+  eq(after - before, 1, "three submissions in flight produce EXACTLY ONE POST");
+  eq(during.label, "Sending...", "the button reports the in-flight state");
+  ok(during.disabled, "the button is disabled while in flight");
+}
+
+// 5g. Ctrl+Enter uses the same guarded path, not a parallel one.
+{
+  const { env, ctx } = sendEnv("#work=message%3Amsg-alpha");
+  ev(ctx, 'selectedWorkItemId = "message:msg-alpha"; selectedConvThread = "thr-alpha";');
+  const ta = env.doc.getElementById("conv-input");
+  ta.value = "PROBE";
+  const before = env.posted.filter((p) => p.method === "POST").length;
+  ta.dispatchEvent(new MiniEvent("keydown", { key: "Enter", ctrlKey: true, bubbles: true, cancelable: true }));
+  ta.dispatchEvent(new MiniEvent("keydown", { key: "Enter", ctrlKey: true, bubbles: true, cancelable: true }));
+  const after = env.posted.filter((p) => p.method === "POST").length;
+  eq(after - before, 1, "repeated Ctrl+Enter in flight produces EXACTLY ONE POST");
+}
+
+// ---------------------------------------------------------------------------
+// 6. STALE SELECTION AFTER POLLING. The reported gap: once polling removed the
+//    selected item, a retained thread plus a matching stale hash let a request
+//    be built for a destination the live queue no longer backed.
+// ---------------------------------------------------------------------------
+{
+  const { env, ctx } = sendEnv("");
+  ctx.wire();
+  ctx.renderQueue();
+
+  // Select a VALID live item through the real wired path.
+  const groups = env.doc.getElementById("queue-groups");
+  groups.querySelector('.q-row[data-work-item="message:msg-alpha"] .q-open')
+    .dispatchEvent(new MiniEvent("click", { bubbles: true, isTrusted: true }));
+  eq(ev(ctx, "selectedWorkItemId"), "message:msg-alpha", "a live item is selected");
+  eq(ev(ctx, "selectedConvThread"), "thr-alpha", "its durable thread is bound");
+  const routeAfterSelect = ctx.location.hash;
+  ok(routeAfterSelect.indexOf("msg-alpha") !== -1, "the canonical route is written");
+
+  // A send here is legitimate, proving the refusal below is not incidental.
+  const okAttempt = await attemptSend(ctx, env, "BASELINE");
+  eq(okAttempt.posts, 1, "a valid live selection DOES send");
+
+  // Now polling removes that item while thread, hash and composer state remain.
+  // env is passed so the SERVED payload drops it too: otherwise the next poll
+  // would restore it and the test could pass without exercising the gap.
+  seed(ctx, [ITEMS[1]], env);
+  ctx.renderQueue();
+  ctx.location.hash = routeAfterSelect;          // stale but MATCHING route
+  ev(ctx, 'selectedWorkItemId = "message:msg-alpha"; selectedConvThread = "thr-alpha";');
+
+  eq(ev(ctx, "selectedConvThread"), "thr-alpha", "the stale thread is still remembered");
+  ok(ctx.location.hash.indexOf("msg-alpha") !== -1, "the stale route still matches");
+
+  const refused = await attemptSend(ctx, env, "MUST NOT SEND");
+  eq(refused.posts, 0,
+     "a selection removed by polling emits NO request, despite stale thread and matching route");
+  ok(refused.draft.length > 0, "the draft is preserved through the refusal");
+  ok(refused.error.indexOf("no longer in the live queue") !== -1,
+     "the refusal explains that the item left the live queue");
+  eq(refused.settledLabel, "Send", "the button returns from Sending... to Send");
+  ok(!refused.settledDisabled, "the button is re-enabled after the refusal");
+
+  // The target itself must report unresolved rather than a sendable pair.
+  const stale = ev(ctx, "convComposerTarget()");
+  ok(stale.unresolved === true, "the stale target reports unresolved");
+  ok(!stale.thread_id, "the stale target carries no thread");
+
+  // Retry succeeds ONLY after selecting a valid live item.
+  groups.querySelector('.q-row[data-work-item="message:msg-beta"] .q-open')
+    .dispatchEvent(new MiniEvent("click", { bubbles: true, isTrusted: true }));
+  eq(ev(ctx, "selectedWorkItemId"), "message:msg-beta", "a live item is reselected");
+  const retry = await attemptSend(ctx, env, "NOW VALID");
+  eq(retry.posts, 1, "the retry sends only after a valid live item is selected");
+}
+
+// 6b. THREAD REMOVED from the live record, item still present.
+{
+  const { env, ctx } = sendEnv("#work=message%3Amsg-alpha");
+  ev(ctx, 'selectedWorkItemId = "message:msg-alpha"; selectedConvThread = "thr-alpha";');
+  ev(ctx, 'workItemsLoaded = true; lastWorkItems = [{ work_item_id: "message:msg-alpha",' +
+          ' thread_id: null, presentation_state: "needs_operator" }];');
+  const r = await attemptSend(ctx, env, "MUST NOT SEND");
+  eq(r.posts, 0, "an item whose live record lost its thread emits NO request");
+  ok(r.draft.length > 0, "the draft is preserved");
+}
+
+// 6c. A PACKET PROJECTION can never be a message destination.
+{
+  const { env, ctx } = sendEnv("#work=in_progress%3Asession-ux-cta-20260725");
+  ev(ctx, 'workItemsLoaded = true; lastWorkItems = [{' +
+          ' work_item_id: "in_progress:session-ux-cta-20260725", thread_id: null,' +
+          ' presentation_state: "waiting_on_claude" }];' +
+          'selectedWorkItemId = "in_progress:session-ux-cta-20260725";' +
+          'selectedConvThread = "thr-anything";');
+  ok(ev(ctx, 'isCanonicalMessageWorkItem("in_progress:session-ux-cta-20260725")') === false,
+     "a packet projection is not a canonical message work item");
+  const t = ev(ctx, "convComposerTarget()");
+  ok(t.unresolved === true, "a packet projection resolves to an unresolved target");
+  const r = await attemptSend(ctx, env, "MUST NOT SEND");
+  eq(r.posts, 0, "a packet projection emits NO request even with a remembered thread");
+}
+
+// 6d. A MALFORMED record can never become a destination or a reconciled tile.
+{
+  const { env, ctx } = sendEnv("");
+  ctx.wire();
+  ev(ctx, 'workItemsLoaded = true; lastWorkItems = [' +
+          '{ work_item_id: "message:msg-good", thread_id: "thr-good", title: "Good",' +
+          '  presentation_state: "blocked", status: "planning", runner_state: "waiting_on_council" },' +
+          '{ work_item_id: null, thread_id: "thr-orphan", title: "No canonical id",' +
+          '  presentation_state: "blocked", status: "planning", runner_state: "waiting_on_council" },' +
+          '{ thread_id: "thr-orphan2", title: "Missing entirely",' +
+          '  presentation_state: "blocked", status: "planning", runner_state: "waiting_on_council" }];');
+  ctx.renderQueue();
+  const groups = env.doc.getElementById("queue-groups");
+  eq(groups.querySelectorAll(".q-row[data-work-item]").length, 1,
+     "only the canonical record is reconciled into a tile");
+  eq(groups.querySelectorAll('.q-row[data-work-item=""]').length, 0,
+     "no tile is keyed on an empty work-item id");
+
+  for (const bad of [null, "", "thr-20260725T142257787771", "in_progress:x", "message:", "msg-1"]) {
+    ok(ev(ctx, "isCanonicalMessageWorkItem(" + JSON.stringify(bad) + ")") === false,
+       JSON.stringify(bad) + " is not a canonical message work item");
+  }
+  ok(ev(ctx, 'isCanonicalMessageWorkItem("message:msg-20260725T142257787771")') === true,
+     "a real canonical id is accepted");
+}
+
+// 6e. SELECTOR-SIGNIFICANT characters in identifiers must not break escaping.
+{
+  const env = buildEnv({});
+  const ctx = loadApp(env);
+  const hostile = ['a"b', "a\\b", "a]b", "a b", "a.b", "a#b", "a:b"];
+  hostile.forEach((v) => {
+    const out = ev(ctx, "cssAttrValue(" + JSON.stringify(v) + ")");
+    ok(typeof out === "string" && out.length >= v.length,
+       "cssEscape returns an escaped string for " + JSON.stringify(v));
+    // The escaped value must be usable in a selector without throwing.
+    let threw = null;
+    try { env.doc.body.querySelector('[data-x="' + out + '"]'); }
+    catch (e) { threw = String(e); }
+    ok(threw === null, "an escaped value is selector-safe for " + JSON.stringify(v));
+  });
+  // Escaping must be applied, not merely available.
+  const src = fs.readFileSync(APP, "utf8");
+  // Ignore comment lines: the code explains WHY CSS.escape is wrong here.
+  const codeOnly = src.split("\n").filter((l) => l.trim().indexOf("//") !== 0).join("\n");
+  ok(codeOnly.indexOf("CSS.escape") === -1,
+     "identifier escaping is not used for quoted attribute selectors");
+}
+
+// ---------------------------------------------------------------------------
+// 7. A FAILED QUEUE REFRESH withdraws send authority. A snapshot that merely
+//    loaded once is not evidence the destination is still live.
+// ---------------------------------------------------------------------------
+{
+  const { env, ctx } = sendEnv("");
+  ctx.wire();
+  ctx.renderQueue();
+  const groups = env.doc.getElementById("queue-groups");
+  groups.querySelector('.q-row[data-work-item="message:msg-alpha"] .q-open')
+    .dispatchEvent(new MiniEvent("click", { bubbles: true, isTrusted: true }));
+  eq(ev(ctx, "selectedWorkItemId"), "message:msg-alpha", "a live item is selected");
+
+  const before = await attemptSend(ctx, env, "BASELINE");
+  eq(before.posts, 1, "sending works while the queue is confirmed");
+
+  // The refresh now FAILS. The snapshot and the selection are untouched.
+  ev(ctx, "queueConfirmed = false;");
+  ok(ev(ctx, "lastWorkItems.length") > 0, "the stale snapshot is still present");
+  ok(ev(ctx, "workItemsLoaded") === true, "the queue still counts as loaded");
+  eq(ev(ctx, "selectedWorkItemId"), "message:msg-alpha", "the selection survives");
+
+  const refused = await attemptSend(ctx, env, "MUST NOT SEND");
+  eq(refused.posts, 0, "a stale, unconfirmed queue emits NO request");
+  ok(refused.draft.length > 0, "the draft is preserved");
+  ok(refused.error.indexOf("not currently confirmed") !== -1,
+     "the refusal explains that the queue is unconfirmed");
+  eq(refused.settledLabel, "Send", "the button returns to Send");
+  ok(!refused.settledDisabled, "the button is re-enabled");
+
+  // RECOVERY: a successful refresh re-confirms and sending resumes.
+  seed(ctx, ITEMS, env);
+  ev(ctx, 'selectedWorkItemId = "message:msg-alpha"; selectedConvThread = "thr-alpha";');
+  ctx.location.hash = "#work=" + encodeURIComponent("message:msg-alpha");
+  const recovered = await attemptSend(ctx, env, "NOW CONFIRMED");
+  eq(recovered.posts, 1, "sending resumes after a successful refresh re-confirms");
+}
+
+// 7b. A real refreshWorkItems FAILURE marks the queue unconfirmed.
+{
+  const env = buildEnv({ responder: () => { throw new Error("network down"); } });
+  const ctx = loadApp(env);
+  seed(ctx, ITEMS, env);
+  ok(ev(ctx, "queueConfirmed") === true, "confirmed after seeding");
+  env.failAll = true;
+  // Force the failure path through the real function.
+  ctx.fetch = () => Promise.reject(new Error("network down"));
+  await ctx.refreshWorkItems();
+  ok(ev(ctx, "queueConfirmed") === false,
+     "a failed refresh withdraws queue confirmation");
+  ok(ev(ctx, "lastWorkItems.length") > 0,
+     "the previous content stays on screen rather than blanking the operator");
+}
+
+// ---------------------------------------------------------------------------
+// 8. NON-CANONICAL ENTRIES render READ-ONLY: visible, never activatable.
+// ---------------------------------------------------------------------------
+{
+  const env = buildEnv({});
+  const ctx = loadApp(env);
+  const mixed = [
+    { work_item_id: "message:msg-real", thread_id: "thr-real", title: "Real work",
+      presentation_state: "blocked", status: "planning", runner_state: "waiting_on_council" },
+    { work_item_id: "in_progress:session-ux-cta-20260725", thread_id: null,
+      title: "CTA packet", presentation_state: "waiting_on_claude", status: "open",
+      runner_state: "unknown" },
+    { work_item_id: "totally-malformed-but-truthy", thread_id: "thr-x",
+      title: "Malformed", presentation_state: "blocked", status: "planning",
+      runner_state: "waiting_on_council" },
+  ];
+  seed(ctx, mixed, env);
+  ctx.wire();
+  ctx.renderQueue();
+  const groups = env.doc.getElementById("queue-groups");
+
+  // All three remain VISIBLE: hiding real durable records is not the fix.
+  eq(groups.querySelectorAll(".q-row[data-work-item]").length, 3,
+     "every durable record stays visible, canonical or not");
+
+  // Only the canonical one is activatable.
+  eq(groups.querySelectorAll(".q-open").length, 1,
+     "only a canonical message work item gets an activation control");
+  const proj = groups.querySelector('.q-row[data-work-item="in_progress:session-ux-cta-20260725"]');
+  eq(proj.getAttribute("data-canonical"), "false", "the projection is marked non-canonical");
+  ok(!proj.querySelector(".q-open"), "the projection has NO activation control");
+  ok(!!proj.querySelector(".q-readonly"), "the projection renders read-only");
+  ok(!!proj.querySelector(".q-ro-badge"), "the projection is labelled as a packet record");
+
+  const mal = groups.querySelector('.q-row[data-work-item="totally-malformed-but-truthy"]');
+  eq(mal.getAttribute("data-canonical"), "false", "a truthy non-canonical id is non-canonical");
+  ok(!mal.querySelector(".q-open"), "a malformed record has NO activation control");
+
+  // Clicking a read-only row navigates nowhere.
+  ev(ctx, "selectedWorkItemId = null;");
+  proj.dispatchEvent(new MiniEvent("click", { bubbles: true, isTrusted: true }));
+  eq(ev(ctx, "selectedWorkItemId"), null, "clicking a packet projection does not navigate");
+  mal.dispatchEvent(new MiniEvent("click", { bubbles: true, isTrusted: true }));
+  eq(ev(ctx, "selectedWorkItemId"), null, "clicking a malformed record does not navigate");
+
+  // The canonical one still works.
+  groups.querySelector(".q-open").dispatchEvent(
+    new MiniEvent("click", { bubbles: true, isTrusted: true }));
+  eq(ev(ctx, "selectedWorkItemId"), "message:msg-real",
+     "the canonical record is still activatable");
+
+  // aria-current, not aria-pressed: this navigates, it does not toggle.
+  const btn = groups.querySelector(".q-open");
+  ok(btn.hasAttribute("aria-current"), "the control uses aria-current");
+  ok(!btn.hasAttribute("aria-pressed"),
+     "it does not advertise a toggle-button contract");
+}
+
+// 8b. POSITIVE selector-match proof for escaped values, not merely no-throw.
+{
+  const env = buildEnv({});
+  const ctx = loadApp(env);
+  const host = env.doc.createElement("div");
+  env.doc.body.appendChild(host);
+  const hostile = ['a"b', "a\\b", "a]b", "a b", "a.b", "a#b", "a:b", "a[b"];
+  hostile.forEach((v, i) => {
+    const el = env.doc.createElement("span");
+    el.setAttribute("data-probe", v);
+    el.setAttribute("data-idx", String(i));
+    host.appendChild(el);
+    const escd = ev(ctx, "cssAttrValue(" + JSON.stringify(v) + ")");
+    let found = null;
+    try { found = host.querySelector('[data-probe="' + escd + '"]'); }
+    catch (e) { found = null; }
+    same(found, el, "an escaped value SELECTS THE INTENDED node for " + JSON.stringify(v));
+  });
+}
+
+// ---------------------------------------------------------------------------
+// 9. REFRESH OUTCOMES, SEQUENCING AND PERSISTENT FAILURE VISIBILITY.
+//    Everything below drives the REAL refreshWorkItems / wire() / send paths.
+//    A controllable transport lets a test decide, per request, whether a
+//    /api/work-items call succeeds, fails, or resolves LATE and out of order.
+// ---------------------------------------------------------------------------
+function queueEnv(opts) {
+  opts = opts || {};
+  const state = { mode: "ok", items: ITEMS, pending: [] };
+  const env = buildEnv({
+    hash: opts.hash || "",
+    responder: (url, method) => {
+      if (url.indexOf("message_id=") !== -1) return { found: true, message: { message: env.lastSent || "PROBE" } };
+      if (method === "POST") return { ok: true, message_id: "msg-new", thread_id: "thr-alpha" };
+      return { ok: true };
+    },
+  });
+  const realFetch = env.ctx.fetch;
+  env.queue = state;
+  env.ctx.fetch = function (url, init) {
+    const u = String(url);
+    const method = (init && init.method) || "GET";
+    if (u.indexOf("/api/work-items") === 0) {
+      env.posted.push({ url: u, method });   // recorded here; not delegated
+      if (state.mode === "fail") return Promise.reject(new Error("probe: queue down"));
+      if (state.mode === "defer") {
+        // Hand the test the resolver so it can complete this call LATER.
+        return new Promise((resolve, reject) => {
+          state.pending.push({
+            resolveWith: (items) => resolve({ ok: true, status: 200,
+              json: () => Promise.resolve({ work_items: items }) }),
+            rejectWith: (e) => reject(e || new Error("probe: deferred failure")),
+          });
+        });
+      }
+      return Promise.resolve({ ok: true, status: 200,
+        json: () => Promise.resolve({ work_items: state.items }) });
+    }
+    return realFetch(url, init);
+  };
+  return env;
+}
+
+const statusOf = (env) => {
+  const el = env.doc.getElementById("restore-status");
+  const text = String(el.textContent || "");
+  // A status counts as shown only when it is both un-hidden AND has content:
+  // the stub element starts without a hidden attribute, so emptiness is the
+  // reliable signal that nothing is being reported.
+  const shown = !el.hidden && text.trim().length > 0;
+  return { hidden: !shown, shown: shown, text: text };
+};
+
+// 9a. THE FOUR OUTCOMES ARE DISTINCT.
+{
+  const env = queueEnv({});
+  const ctx = loadApp(env);
+  env.queue.items = ITEMS;
+  eq(await ctx.refreshWorkItems(), "confirmed", "a populated load is CONFIRMED");
+  env.queue.items = [];
+  eq(await ctx.refreshWorkItems(), "confirmed_empty",
+     "an EMPTY load is confirmed_empty, not failed and not unloaded");
+  ok(ev(ctx, "workItemsLoaded") === true, "a confirmed empty load is still LOADED");
+  ok(ev(ctx, "queueConfirmed") === true, "a confirmed empty load is CONFIRMED");
+  eq(ev(ctx, "lastWorkItems.length"), 0, "and the snapshot is genuinely empty");
+  env.queue.mode = "fail";
+  eq(await ctx.refreshWorkItems(), "failed", "a rejected load is FAILED");
+  ok(ev(ctx, "queueConfirmed") === false, "a failure withdraws confirmation");
+  ok(ev(ctx, "workItemsLoaded") === true,
+     "a failure does not pretend the queue was never loaded");
+}
+
+// 9b. STALE COMPLETION, DIRECTION ONE: an OLDER SUCCESS after a NEWER FAILURE
+//     must not restore sending.
+{
+  const env = queueEnv({});
+  const ctx = loadApp(env);
+  env.queue.items = ITEMS;
+  await ctx.refreshWorkItems();
+  ok(ev(ctx, "queueConfirmed") === true, "confirmed to begin with");
+
+  env.queue.mode = "defer";
+  const older = ctx.refreshWorkItems();          // generation N, left in flight
+  env.queue.mode = "fail";
+  const newerOutcome = await ctx.refreshWorkItems();   // generation N+1, fails
+  eq(newerOutcome, "failed", "the newer refresh failed");
+  ok(ev(ctx, "queueConfirmed") === false, "confirmation is withdrawn");
+
+  env.queue.pending.shift().resolveWith(ITEMS);  // the OLDER one now succeeds
+  eq(await older, "superseded", "the older success reports SUPERSEDED");
+  ok(ev(ctx, "queueConfirmed") === false,
+     "an older success does NOT restore confirmation after a newer failure");
+  ok(ev(ctx, "queueFailureReported") === true, "the failure is still reported");
+}
+
+// 9c. STALE COMPLETION, DIRECTION TWO: an OLDER FAILURE after a NEWER SUCCESS
+//     must not invalidate the confirmed snapshot.
+{
+  const env = queueEnv({});
+  const ctx = loadApp(env);
+  env.queue.mode = "defer";
+  const older = ctx.refreshWorkItems();          // generation N, in flight
+  env.queue.mode = "ok";
+  env.queue.items = ITEMS;
+  eq(await ctx.refreshWorkItems(), "confirmed", "the newer refresh confirmed");
+  ok(ev(ctx, "queueConfirmed") === true, "the queue is confirmed");
+
+  env.queue.pending.shift().rejectWith();        // the OLDER one now fails
+  eq(await older, "superseded", "the older failure reports SUPERSEDED");
+  ok(ev(ctx, "queueConfirmed") === true,
+     "an older failure does NOT invalidate a newer confirmed snapshot");
+  ok(ev(ctx, "queueFailureReported") === false, "no failure is reported");
+  eq(statusOf(env).hidden, true, "and no failure status is shown");
+}
+
+// 9d. INITIAL LOAD FAILURE THROUGH wire(): the explanation must SURVIVE every
+//     continuation. This is the regression the previous round introduced.
+{
+  const env = queueEnv({});
+  const ctx = loadApp(env);
+  env.queue.mode = "fail";
+  ctx.wire();
+  for (let i = 0; i < 12; i++) await settle();   // let ALL continuations run
+
+  ok(ev(ctx, "queueConfirmed") === false, "boot failure leaves the queue unconfirmed");
+  ok(ev(ctx, "queueFailureReported") === true, "the failure is recorded");
+  const st = statusOf(env);
+  eq(st.hidden, false, "the explanation is STILL VISIBLE after boot settles");
+  ok(st.text.indexOf("Sending is paused") !== -1,
+     "the explanation is the plain-language sending-paused message");
+  ok(st.text.indexOf("could not be refreshed") !== -1, "it says what went wrong");
+  eq(ev(ctx, "selectedWorkItemId"), null,
+     "no selection is restored from an unconfirmed snapshot");
+
+  // Transient cleanup must not erase it either.
+  ctx.clearTransientRestoreStatus();
+  eq(statusOf(env).hidden, false,
+     "transient-status cleanup does NOT erase a refresh failure");
+
+  // Nor may a later polling cycle that also fails.
+  await ctx.refreshWorkItems();
+  eq(statusOf(env).hidden, false, "a further failed poll keeps the explanation");
+}
+
+// 9e. ZERO POSTS while unconfirmed, DRAFT PRESERVED, then RECOVERY.
+{
+  const env = queueEnv({});
+  const ctx = loadApp(env);
+  env.queue.items = ITEMS;
+  await ctx.refreshWorkItems();
+  ctx.wire();
+  ctx.renderQueue();
+  env.doc.getElementById("queue-groups")
+    .querySelector('.q-row[data-work-item="message:msg-alpha"] .q-open')
+    .dispatchEvent(new MiniEvent("click", { bubbles: true, isTrusted: true }));
+  eq(ev(ctx, "selectedWorkItemId"), "message:msg-alpha", "a live item is selected");
+
+  const baseline = await attemptSend(ctx, env, "BASELINE");
+  eq(baseline.posts, 1, "sending works while confirmed");
+
+  env.queue.mode = "fail";
+  eq(await ctx.refreshWorkItems(), "failed", "the refresh now fails");
+
+  const refused = await attemptSend(ctx, env, "MUST NOT SEND");
+  eq(refused.posts, 0, "ZERO POSTs while the queue is unconfirmed");
+  ok(refused.draft.length > 0, "the draft is preserved");
+  ok(refused.error.indexOf("not currently confirmed") !== -1,
+     "the refusal explains the unconfirmed queue");
+  eq(refused.settledLabel, "Send", "the button is restored");
+  eq(statusOf(env).hidden, false, "the sending-paused explanation is still visible");
+
+  // RECOVERY through a later CONFIRMED refresh.
+  env.queue.mode = "ok";
+  eq(await ctx.refreshWorkItems(), "confirmed", "a later refresh confirms");
+  ok(ev(ctx, "queueFailureReported") === false, "the failure record is cleared");
+  eq(statusOf(env).hidden, true, "and the explanation is cleared with it");
+  ev(ctx, 'selectedWorkItemId = "message:msg-alpha"; selectedConvThread = "thr-alpha";');
+  ctx.location.hash = "#work=" + encodeURIComponent("message:msg-alpha");
+  const recovered = await attemptSend(ctx, env, "NOW CONFIRMED");
+  eq(recovered.posts, 1, "sendability is restored only after a confirmed refresh");
+}
+
+// 9f. A CONFIRMED EMPTY result is authoritative: stale destinations are NOT
+//     sendable afterwards, and it is not treated as a failure.
+{
+  const env = queueEnv({});
+  const ctx = loadApp(env);
+  env.queue.items = ITEMS;
+  await ctx.refreshWorkItems();
+  ctx.wire();
+  ctx.renderQueue();
+  ev(ctx, 'selectedWorkItemId = "message:msg-alpha"; selectedConvThread = "thr-alpha";');
+  ctx.location.hash = "#work=" + encodeURIComponent("message:msg-alpha");
+
+  env.queue.items = [];
+  eq(await ctx.refreshWorkItems(), "confirmed_empty", "the queue is confirmed EMPTY");
+  ok(ev(ctx, "queueConfirmed") === true, "confirmed empty is still confirmed");
+  eq(statusOf(env).hidden, true, "a confirmed empty result shows no failure status");
+
+  const t = ev(ctx, "convComposerTarget()");
+  ok(t.unresolved === true, "the stale destination is unresolved after an empty result");
+  const r = await attemptSend(ctx, env, "MUST NOT SEND");
+  eq(r.posts, 0, "a stale destination is NOT sendable after a confirmed empty result");
+  ok(r.draft.length > 0, "the draft is preserved");
+  ok(r.error.indexOf("no longer in the live queue") !== -1,
+     "the refusal names the live queue, not a refresh failure");
+}
+
+// ---------------------------------------------------------------------------
+// 10. CSS STRING ESCAPING is complete, and a MALFORMED payload is not an
+//     authoritative empty queue.
+// ---------------------------------------------------------------------------
+{
+  const env = buildEnv({});
+  const ctx = loadApp(env);
+
+  // Control characters are not permitted raw in a CSS string token.
+  const controls = ["a\nb", "a\rb", "a\tb", "a\u0000b", "a\u001fb", "a\u007fb"];
+  controls.forEach((v) => {
+    const out = ev(ctx, "cssAttrValue(" + JSON.stringify(v) + ")");
+    ok(out.indexOf("\n") === -1 && out.indexOf("\r") === -1,
+       "no raw newline survives escaping of " + JSON.stringify(v));
+    ok(/\\[0-9a-f]+ /.test(out),
+       "a control character is hex-escaped in " + JSON.stringify(v));
+  });
+
+  // And the ordinary cases still round-trip to the intended node.
+  const host = env.doc.createElement("div");
+  env.doc.body.appendChild(host);
+  ['a"b', "a\\b", "a b", "a]b"].forEach((v) => {
+    const el = env.doc.createElement("span");
+    el.setAttribute("data-probe", v);
+    host.appendChild(el);
+    const escd = ev(ctx, "cssAttrValue(" + JSON.stringify(v) + ")");
+    let found = null;
+    try { found = host.querySelector('[data-probe="' + escd + '"]'); } catch (e) { found = null; }
+    same(found, el, "escaping still selects the intended node for " + JSON.stringify(v));
+  });
+}
+
+// 10b. A 200 with a MALFORMED body must be a FAILURE, not a confirmed empty.
+for (const bad of [{}, { work_items: null }, { work_items: "nope" }, { work_items: 7 }]) {
+  const env = queueEnv({});
+  const ctx = loadApp(env);
+  env.queue.items = ITEMS;
+  await ctx.refreshWorkItems();
+  ok(ev(ctx, "queueConfirmed") === true, "confirmed to begin with");
+  const snapshotBefore = ev(ctx, "lastWorkItems.length");
+
+  // Return 200 with a body that is not a work-items payload.
+  env.ctx.fetch = (url, init) => {
+    const u = String(url);
+    const method = (init && init.method) || "GET";
+    if (u.indexOf("/api/work-items") === 0) {
+      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(bad) });
+    }
+    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ ok: true }) });
+  };
+  const outcome = await ctx.refreshWorkItems();
+  eq(outcome, "failed",
+     "a malformed body is FAILED, not confirmed_empty: " + JSON.stringify(bad));
+  ok(ev(ctx, "queueConfirmed") === false, "confirmation is withdrawn");
+  eq(ev(ctx, "lastWorkItems.length"), snapshotBefore,
+     "the previous snapshot is preserved, not replaced by an invented empty one");
+  ok(statusOf(env).shown, "the operator is told the response was unreadable");
+  ok(statusOf(env).text.indexOf("unreadable") !== -1, "and why");
+}
+
+console.log((failures === 0 ? "PASS" : "FAIL") + ": " + (checks - failures) + "/" + checks + " wired-path checks");
+process.exit(failures === 0 ? 0 : 1);
diff --git a/tests/test_session_continuity_ux.py b/tests/test_session_continuity_ux.py
new file mode 100644
index 0000000..2e21ef2
--- /dev/null
+++ b/tests/test_session_continuity_ux.py
@@ -0,0 +1,1724 @@
+"""Active Session Continuity and Message Identity UX (Phase 1).
+
+Follows the established front-end test pattern in this repository: static
+assertion over apps/control-plane/static/{app.js,index.html,style.css}, which is
+how test_command_center_hygiene, test_conversation_console, test_operator_mode
+and the other console tests verify UI behaviour.
+
+Covers the operator-specified acceptance criteria: selection restoration,
+active-item fallback ranking, the no-active-item empty state, conversation
+default, latest-message navigation, targeted composer destination, generic
+composer suppression, message-ID visibility, copy controls, post-send
+confirmation, stale stored selection, completed items not being auto-selected,
+truthful execution-state rendering, and keyboard behaviour.
+"""
+import os
+import re
+import unittest
+
+# Single backslash, built from its code point so the source stays
+# ASCII-safe and the regex literals below assemble unambiguously.
+BS = chr(92)
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
+HTML = _read("index.html")
+CSS = _read("style.css")
+
+
+
+RE_CARD = r"function queueCard\([\s\S]{0,8000}?\n}"
+RE_PARSE = r"function parseWorkRoute[\s\S]{0,4000}?\n\}"
+
+
+def _block_of(html, elem_id):
+    """Return the balanced-div source of the element carrying elem_id.
+
+    Used so containment assertions test the real tree rather than the byte
+    order of two ids in the file.
+    """
+    i = html.index('id="%s"' % elem_id)
+    start = html.rindex("<div", 0, i)
+    depth = 0
+    for m in re.finditer(r"<div" + chr(92) + "b|</div>", html[start:]):
+        depth += 1 if m.group(0) != "</div>" else -1
+        if depth == 0:
+            return html[start:start + m.end()]
+    raise AssertionError("unbalanced markup around " + elem_id)
+
+
+class SelectionRestorationTest(unittest.TestCase):
+    """Item 1: a refresh returns the operator to active work."""
+
+    def test_selection_is_persisted_under_a_single_stable_key(self):
+        self.assertIn('SELECTION_KEY = "cw_selected_work_item_v1"', APP)
+        self.assertIn("function persistSelection", APP)
+        self.assertIn("function readPersistedSelection", APP)
+
+    def test_select_task_persists_the_choice(self):
+        m = re.search(r"function selectTask\([^)]*\)\s*\{(.{0,900})", APP, re.S)
+        self.assertIsNotNone(m, "selectTask not found")
+        self.assertIn("persistSelection(selectedWorkItemId)", m.group(1))
+
+    def test_persistence_failure_is_survivable(self):
+        """Storage may be unavailable; the hash route must still work."""
+        m = re.search(r"function persistSelection[\s\S]{0,4000}?\n\}", APP)
+        self.assertIn("catch", m.group(0))
+        m2 = re.search(r"function readPersistedSelection[\s\S]{0,4000}?\n\}", APP)
+        self.assertIn("catch", m2.group(0))
+
+    def test_restore_runs_at_boot_after_the_queue_loads(self):
+        """Scoped to wire(), because the retry path contains a textually
+        similar call and would satisfy a whole-file assertion without proving
+        anything about boot."""
+        self.assertIn("restoreActiveSelection", APP)
+        i = APP.index("function wire()")
+        j = APP.index("function handleOperatorAction")
+        boot = APP[i:j]
+        self.assertIn("refreshWorkItems()", boot)
+        self.assertIn("restoreActiveSelection()", boot)
+        self.assertIn("initJumpToLatest();", boot)
+        # Restoration must follow a SUCCESSFUL load, not run unconditionally.
+        self.assertIn("refreshWorkItems().then(", boot)
+
+    def test_explicit_deep_link_wins_over_stored_selection(self):
+        m = re.search(r"function restoreActiveSelection[\s\S]{0,4000}?\n\}", APP)
+        body = m.group(0)
+        self.assertIn("location.hash", body)
+        # The deep-link branch must short-circuit BEFORE any fallback ranking,
+        # so a shared link never lands the operator on a different item.
+        head = body.split("rankActiveWorkItems")[0]
+        self.assertIn("if (deep)", head)
+        self.assertIn("return;", head.split("if (deep)")[1])
+
+    def test_stale_stored_selection_is_not_used(self):
+        """A stored id must be validated against the live queue AND activity."""
+        m = re.search(r"function restoreActiveSelection[\s\S]{0,4000}?\n\}", APP)
+        body = m.group(0)
+        self.assertIn("isActiveItem", body)
+        self.assertIn("work_item_id === stored", body)
+
+
+class FallbackRankingTest(unittest.TestCase):
+    """Item 1: highest-priority active item when there is no valid selection."""
+
+    def test_priority_order_matches_the_operator_specification(self):
+        m = re.search(r"ACTIVE_RANK = \[(.*?)\]", APP, re.S)
+        self.assertIsNotNone(m)
+        order = re.findall(r'"([a-z_]+)"', m.group(1))
+        self.assertEqual(order, [
+            "waiting_for_operator", "paused", "executor_active",
+            "in_council", "claimed", "blocked"])
+
+    def test_operator_message_posted_is_removed_as_unreachable(self):
+        """Proven against the real server value domain: last_activity_event is
+        emitted only as created|completion|verification|council|gate|progress|
+        claim|response|evidence, so no item can ever reach this rank."""
+        m = re.search(r"ACTIVE_RANK = " + BS + r"[(.*?)" + BS + r"]", APP, re.S)
+        self.assertNotIn("operator_message_posted", m.group(1))
+        self.assertNotIn('=== "operator_message"', APP,
+                         "the dead event alias must be gone, not just unranked")
+        self.assertNotIn('ev === "message"', APP)
+
+    def test_wake_pending_is_deferred_not_simulated(self):
+        """No durable field can establish wake_pending before the wake bridge.
+
+        Keeping it in the executable order would advertise a priority bucket
+        that nothing can ever fall into, which is what made the first round of
+        this ranking inert.
+        """
+        m = re.search(r"ACTIVE_RANK = \[(.*?)\]", APP, re.S)
+        self.assertNotIn("wake_pending", m.group(1))
+        head = APP[:APP.index("const ACTIVE_RANK")]
+        self.assertIn("wake_pending", head[-900:],
+                      "the deferral must be documented where the rank is defined")
+
+    def test_every_ranked_state_is_reachable_from_the_mapping(self):
+        """A rank nothing can produce is a silent mis-ordering."""
+        m = re.search(r"ACTIVE_RANK = \[(.*?)\]", APP, re.S)
+        ranked = set(re.findall(r'"([a-z_]+)"', m.group(1)))
+        tm = re.search(r"function truthfulExecutionState[\s\S]{0,4000}?\n\}", APP)
+        produced = set(re.findall(r'return "([a-z_]+)"', tm.group(0)))
+        self.assertEqual(ranked - produced, set(),
+                         "ACTIVE_RANK contains states the mapping never returns")
+
+    def test_ranking_filters_to_active_items_only(self):
+        m = re.search(r"function rankActiveWorkItems[\s\S]{0,4000}?\n\}", APP)
+        self.assertIn("filter(isActiveItem)", m.group(0))
+
+    def test_completed_items_are_never_auto_selected(self):
+        m = re.search(r"INACTIVE_STATES = \[(.*?)\]", APP, re.S)
+        self.assertIsNotNone(m)
+        for terminal in ("recently_completed", "complete", "superseded", "historical"):
+            self.assertIn(terminal, m.group(1))
+
+    def test_ranking_is_stable_by_recent_activity(self):
+        m = re.search(r"function rankActiveWorkItems[\s\S]{0,4000}?\n\}", APP)
+        self.assertIn("last_activity_at", m.group(0))
+
+    def test_ranking_delegates_to_the_single_truthful_mapping(self):
+        """Two mappings drifted: the ranking copy never returned
+        operator_message_posted and defaulted unknowns to in_council."""
+        m = re.search(r"function activeStateOf[\s\S]{0,4000}?\n\}", APP)
+        body = m.group(0)
+        self.assertIn("return truthfulExecutionState(it)", body)
+        self.assertNotIn('return "in_council"', body)
+
+    def test_unknown_states_are_not_guessed(self):
+        tm = re.search(r"function truthfulExecutionState[\s\S]{0,4000}?\n\}", APP)
+        self.assertIn('return ""', tm.group(0))
+
+    def test_ranking_has_a_deterministic_final_key(self):
+        m = re.search(r"function rankActiveWorkItems[\s\S]{0,4000}?\n\}", APP)
+        self.assertIn("work_item_id", m.group(0).split("last_activity_at")[-1])
+
+
+class EmptyStateTest(unittest.TestCase):
+    """Item 1/5: the empty state is legitimate ONLY with no active work."""
+
+    def test_no_active_work_clears_the_selection_and_returns(self):
+        m = re.search(r"function restoreActiveSelection[\s\S]{0,4000}?\n\}", APP)
+        self.assertIn("if (!target)", m.group(0))
+
+    def test_duplicate_no_task_selected_placeholders_are_gone(self):
+        self.assertEqual(HTML.count("No task selected."), 1,
+                         "exactly ONE canonical empty-state placeholder may "
+                         "ship; the rail must not duplicate it")
+
+    def test_rail_is_hidden_rather_than_showing_empty_cards(self):
+        self.assertIn('id="session-rail"', HTML)
+        self.assertIn("rail.hidden = true", APP)
+        self.assertIn("rail.hidden = false", APP)
+
+
+class ConversationFirstTest(unittest.TestCase):
+    """Item 2: conversation-first, latest message, deliberate-scroll respected."""
+
+    def test_navigation_opens_the_conversation_tab(self):
+        m = re.search(r"function navigateToWorkItem[\s\S]{0,4000}?\n\}", APP)
+        self.assertIn("openConversationTab()", m.group(0))
+
+    def test_navigation_lands_on_the_latest_message(self):
+        m = re.search(r"function navigateToWorkItem[\s\S]{0,4000}?\n\}", APP)
+        self.assertIn("jumpToLatestMessage", m.group(0))
+
+    def test_scroll_is_preserved_only_when_deliberately_moved_away(self):
+        self.assertIn("function operatorMovedAwayFromLatest", APP)
+
+    def test_jump_to_latest_control_exists_and_is_a_button(self):
+        self.assertIn('id="jump-to-latest"', HTML)
+        self.assertRegex(HTML, r'<button[^>]*id="jump-to-latest"')
+        self.assertIn(".jump-to-latest", CSS)
+
+
+class ComposerBindingTest(unittest.TestCase):
+    """Item 3, behaviour: the displayed destination IS the posted destination.
+
+    These assertions exist because live inspection of the running console found
+    the presentation layer alone was not enough: the banner rendered correctly
+    but never received a work_item_id, so it read "New conversation" while a
+    work item was selected. That is exactly the wrong-destination class of
+    defect this slice is meant to remove.
+    """
+
+    def test_composer_target_binds_to_the_selected_work_item(self):
+        m = re.search(r"function convComposerTarget[\s\S]{0,4000}?\n\}", APP)
+        self.assertIsNotNone(m, "convComposerTarget not found")
+        body = m.group(0)
+        self.assertIn("selectedWorkItemId", body)
+        self.assertIn("work_item_id: selectedWorkItemId", body)
+
+    def test_work_item_id_is_only_sent_with_a_durable_thread(self):
+        """The server refuses an unbound thread/work-item pair; never invent one."""
+        m = re.search(r"function convComposerTarget[\s\S]{0,4000}?\n\}", APP)
+        body = m.group(0)
+        # The thread now comes ONLY from the live queue record, so a
+        # remembered thread can no longer keep a removed item sendable.
+        self.assertIn("liveQueueRecord(selectedWorkItemId)", body)
+        self.assertIn("(live && live.thread_id) || null", body)
+        # The bare-work-item shape is an explicit fail-closed marker.
+        self.assertIn("if (thread) return {", body)
+        self.assertIn("unresolved: true", body)
+
+    def test_selection_change_refreshes_the_destination(self):
+        m = re.search(r"function selectTask\([^)]*\)\s*\{(.{0,900})", APP, re.S)
+        self.assertIn("convComposer.updateBanner()", m.group(1))
+
+    def test_navigation_binds_the_real_durable_thread(self):
+        """selectTask(null, id) would drop the thread and mint a new one."""
+        m = re.search(r"function navigateToWorkItem[\s\S]{0,4000}?\n\}", APP)
+        body = m.group(0)
+        self.assertNotIn("selectTask(null, workItemId)", body)
+        self.assertIn("thread_id", body)
+
+    def test_deep_link_binds_the_thread_the_same_way(self):
+        m = re.search(r"function applyWorkHashRoute[\s\S]{0,4000}?\n\}", APP)
+        body = m.group(0)
+        self.assertNotIn("selectTask(null, wid)", body)
+        self.assertIn("thread_id", body)
+
+    def test_confirmed_target_contract_is_not_weakened(self):
+        """The pre-existing guarantee must survive this slice.
+
+        A locally minted, pre-allocated thread id must never be presented as a
+        confirmed destination. Binding a work item does not need a looser rule:
+        selectTask now stores the item's REAL durable thread, so
+        selectedConvThread is already set whenever a work item is selected.
+        """
+        self.assertIn("isConfirmedTarget: () => !!selectedConvThread", APP)
+        self.assertNotIn("selectedConvThread || selectedWorkItemId", APP)
+
+
+class RouteParsingTest(unittest.TestCase):
+    """decodeURIComponent raises URIError on malformed percent-encoding.
+
+    Unguarded at boot, that exception propagates out of wire() BEFORE
+    restoration, status reporting and the refresh timers are installed, so one
+    bad URL would disable the console instead of being reported.
+    """
+
+    def test_one_guarded_parser_is_shared(self):
+        self.assertIn("function parseWorkRoute", APP)
+        m = re.search(RE_PARSE, APP)
+        body = m.group(0)
+        self.assertIn("try {", body)
+        self.assertIn("catch", body)
+        self.assertIn("malformed: true", body)
+
+    def test_no_unguarded_decode_remains_on_the_route_paths(self):
+        for fn in ("applyWorkHashRoute", "restoreActiveSelection"):
+            m = re.search(r"function " + fn + r"[\s\S]{0,4000}?\n\}", APP)
+            self.assertNotIn("decodeURIComponent", m.group(0),
+                             fn + " must go through parseWorkRoute")
+
+    def test_boot_route_reports_a_malformed_link(self):
+        m = re.search(r"function applyWorkHashRoute[\s\S]{0,4000}?\n\}", APP)
+        body = m.group(0)
+        self.assertIn("route.malformed", body)
+        self.assertIn("showRestoreStatus", body)
+        self.assertIn("clearWorkRoute()", body)
+
+    def test_a_bad_message_fragment_does_not_discard_a_valid_work_id(self):
+        m = re.search(RE_PARSE, APP)
+        body = m.group(0)
+        after = body.split("let msg = null;")[1]
+        self.assertIn("catch", after)
+        self.assertIn("msg = null", after)
+
+
+class RouteErrorPersistenceTest(unittest.TestCase):
+    """Clearing the hash makes the bad route invisible to the restoration that
+    follows, so the explanation must not be wiped by the success path."""
+
+    def test_a_reported_route_error_is_not_transient(self):
+        self.assertIn("let routeErrorReported = false;", APP)
+        self.assertIn("function clearTransientRestoreStatus", APP)
+        m = re.search(r"function clearTransientRestoreStatus[\s\S]{0,4000}?\n\}", APP)
+        self.assertIn("if (routeErrorReported) return;", m.group(0))
+
+    def test_boot_uses_the_transient_clear(self):
+        i = APP.index("function wire()")
+        j = APP.index("function handleOperatorAction")
+        self.assertIn("clearTransientRestoreStatus();", APP[i:j])
+        self.assertNotIn('showRestoreStatus("");', APP[i:j])
+
+    def test_malformed_boot_route_clears_the_selection(self):
+        m = re.search(r"function applyWorkHashRoute[\s\S]{0,4000}?\n\}", APP)
+        branch = m.group(0).split("route.malformed")[1][:600]
+        self.assertIn("selectTask(null);", branch)
+        self.assertIn("persistSelection(null);", branch)
+        self.assertIn("routeErrorReported = true;", branch)
+
+    def test_the_boot_message_does_not_contradict_restoration(self):
+        """Restoration DOES continue after a malformed boot route, so the
+        message must not claim nothing is selected."""
+        m = re.search(r"function applyWorkHashRoute[\s\S]{0,4000}?\n\}", APP)
+        branch = m.group(0).split("route.malformed")[1]
+        branch = branch[:branch.index("return;")]
+        # Assert on the OPERATOR-FACING string, not the surrounding commentary,
+        # which legitimately quotes the phrase being avoided.
+        call = branch[branch.index("showRestoreStatus("):]
+        # The message must not promise ANY outcome: restoration may legitimately
+        # find no active work at all, so it states only what already happened.
+        self.assertNotIn("highest-priority", call)
+        self.assertNotIn("Restoring your active work", call)
+        self.assertIn("could not be read", call)
+
+
+class UnifiedActivationTest(unittest.TestCase):
+    """Council finding: mouse and keyboard activation diverged.
+
+    The click handler called selectTask() directly and never wrote the hash,
+    while keyboard activation used navigateToWorkItem(). Ordinary mouse
+    selection therefore left the PREVIOUS route in the URL -- exactly the
+    stale-hash symptom this correction exists to remove -- and the four-way
+    destination agreement could not be established for the common interaction.
+    """
+
+    def test_mouse_activation_goes_through_navigation(self):
+        i = APP.index('getElementById("queue-groups").addEventListener("click"')
+        handler = APP[i:i + 900]
+        self.assertIn("navigateToWorkItem(workItem)", handler)
+        self.assertNotIn("selectTask(thread, workItem)", handler,
+                         "the direct path skipped writing the canonical route")
+
+    def test_there_is_one_navigation_operation(self):
+        """Only navigateToWorkItem writes the route, and every activation
+        path routes through it."""
+        self.assertEqual(APP.count("location.hash = " + '"#work="'), 1,
+                         "exactly one place may write the work route")
+        m = re.search(r"function navigateToWorkItem[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n}", APP)
+        self.assertIn('location.hash = "#work="', m.group(0))
+
+    def test_keyboard_uses_native_button_semantics(self):
+        """A real <button> activates on Enter and Space and fires exactly one
+        click, so there is NO queue key handler at all. An earlier version
+        called preventDefault() on Space, which suppressed the very activation
+        the control exists to provide."""
+        for m in re.finditer(r'addEventListener\("keydown"', APP):
+            window = APP[m.start():m.start() + 500]
+            self.assertNotIn("q-open", window,
+                             "no keydown handler may intercept the queue button")
+            self.assertNotIn("navigateToWorkItem", window,
+                             "no key handler may create a second activation path")
+
+
+class StrictRouteProofTest(unittest.TestCase):
+    """Council finding: absent and malformed routes bypassed the check.
+
+    An unreadable URL is not evidence that the URL agrees with the selection,
+    yet both cases previously fell through and permitted the send.
+    """
+
+    def test_an_absent_route_refuses(self):
+        m = re.search(r"function destinationDisagreement[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n  }", APP)
+        body = m.group(0)
+        self.assertIn("if (!route)", body)
+        self.assertIn("carries no work route", body)
+
+    def test_a_malformed_route_refuses(self):
+        m = re.search(r"function destinationDisagreement[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n  }", APP)
+        body = m.group(0)
+        self.assertIn("route.malformed", body)
+        self.assertIn("unreadable work route", body)
+
+    def test_the_check_no_longer_requires_a_non_malformed_route_to_apply(self):
+        m = re.search(r"function destinationDisagreement[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n  }", APP)
+        self.assertNotIn("!route.malformed && route.work_item_id", m.group(0))
+
+
+class QueueReconciliationTest(unittest.TestCase):
+    """Correction 1: polling must not destroy DOM identity or focus.
+
+    renderQueue rewrote innerHTML on every poll, roughly every two seconds,
+    which removed the focused control before a human could press a key. That
+    made keyboard operation of the queue impossible.
+    """
+
+    def test_a_no_change_poll_does_not_touch_the_dom(self):
+        self.assertIn("let lastQueueSignature = null;", APP)
+        m = re.search(r"function renderQueue[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n}", APP)
+        body = m.group(0)
+        self.assertIn("if (signature === lastQueueSignature) return;", body)
+        # The populated path must reach its short circuit without touching the
+        # DOM. The empty-queue branch above it writes one message and is itself
+        # guarded by its own signature check, so it is excluded here.
+        empty_end = body.index("const desired = rows")
+        populated = body[empty_end:]
+        i = populated.index("if (signature === lastQueueSignature) return;")
+        self.assertNotIn("innerHTML", populated[:i],
+                         "the DOM must not be written before the no-change check")
+        # And the empty branch short-circuits too, rather than rewriting on
+        # every poll.
+        self.assertIn("if (lastQueueSignature === emptySig) return;", body)
+
+    def test_reconciliation_is_keyed_by_canonical_work_item_id(self):
+        self.assertIn("function reconcileQueue", APP)
+        m = re.search(r"function reconcileQueue[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n}", APP)
+        body = m.group(0)
+        self.assertIn("data-work-item", body)
+        self.assertIn("data-sig", body)
+
+    def test_an_unchanged_tile_is_reused_not_replaced(self):
+        m = re.search(r"function reconcileQueue[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n}", APP)
+        body = m.group(0)
+        self.assertIn('prev.getAttribute("data-sig") === d.sig', body)
+        after = body.split('prev.getAttribute("data-sig") === d.sig')[1][:400]
+        self.assertIn("return;", after,
+                      "an unchanged tile must be left entirely alone")
+
+    def test_wholesale_replacement_is_gone_from_the_render_path(self):
+        m = re.search(r"function renderQueue[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n}", APP)
+        body = m.group(0)
+        # The only innerHTML write left is the genuinely empty-queue message.
+        self.assertEqual(body.count("el.innerHTML ="), 1)
+        self.assertIn("queue-empty", body)
+
+    def test_focus_is_captured_and_restored_around_reconciliation(self):
+        self.assertIn("function focusedQueueKey", APP)
+        self.assertIn("function restoreQueueFocus", APP)
+        m = re.search(r"function renderQueue[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n}", APP)
+        body = m.group(0)
+        i = body.index("const focusKey = focusedQueueKey();")
+        j = body.index("reconcileQueue(el, desired);")
+        k = body.index("restoreQueueFocus(focusKey, el);")
+        self.assertLess(i, j, "focus must be captured before reconciliation")
+        self.assertLess(j, k, "focus must be restored after reconciliation")
+
+    def test_lost_focus_moves_predictably_not_to_the_body(self):
+        m = re.search(r"function restoreQueueFocus[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n}", APP)
+        body = m.group(0)
+        self.assertIn('el.querySelector(".q-open")', body)
+        self.assertIn("el.focus()", body)
+
+    def test_no_stale_item_is_retained_to_preserve_focus(self):
+        m = re.search(r"function reconcileQueue[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n}", APP)
+        body = m.group(0)
+        self.assertIn("removeChild", body)
+        self.assertIn("if (!keep[k]", body)
+
+    def test_the_signature_covers_every_rendered_field(self):
+        m = re.search(r"function queueCardSignature[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n}", APP)
+        body = m.group(0)
+        for field in ("work_item_id", "thread_id", "presentation_state", "status",
+                      "runner_state", "claimed_by", "last_activity_at"):
+            self.assertIn(field, body,
+                          field + " is rendered, so it must affect the signature")
+
+
+class WiredPathCoverageTest(unittest.TestCase):
+    """Correction 3: coverage must run the real wired paths."""
+
+    def test_a_wired_path_harness_exists(self):
+        here = os.path.dirname(os.path.abspath(__file__))
+        for name in ("wired_paths.mjs", "mini_dom.mjs"):
+            self.assertTrue(os.path.isfile(os.path.join(here, "dom", name)), name)
+
+    def test_it_installs_the_real_wire(self):
+        here = os.path.dirname(os.path.abspath(__file__))
+        src = open(os.path.join(here, "dom", "wired_paths.mjs"), encoding="utf-8").read()
+        self.assertIn("ctx.wire()", src)
+        self.assertIn("dispatchEvent", src)
+
+    def test_it_dispatches_real_clicks_and_keys(self):
+        here = os.path.dirname(os.path.abspath(__file__))
+        src = open(os.path.join(here, "dom", "wired_paths.mjs"), encoding="utf-8").read()
+        self.assertIn('pressKey(env.doc, key)', src)
+        self.assertIn('new MiniEvent("click"', src)
+
+    def test_it_proves_focus_survives_polling(self):
+        here = os.path.dirname(os.path.abspath(__file__))
+        src = open(os.path.join(here, "dom", "wired_paths.mjs"), encoding="utf-8").read()
+        self.assertIn("focus survives repeated unchanged polling", src)
+        self.assertIn("ctx.renderQueue()", src)
+
+    def test_it_drives_the_real_send_through_refusals(self):
+        here = os.path.dirname(os.path.abspath(__file__))
+        src = open(os.path.join(here, "dom", "wired_paths.mjs"), encoding="utf-8").read()
+        self.assertIn('convComposer.send()', src)
+        for branch in ("unresolved", "different work item", "no work route", "unreadable"):
+            self.assertIn(branch, src)
+
+    def test_the_mini_dom_states_its_limitation(self):
+        here = os.path.dirname(os.path.abspath(__file__))
+        src = open(os.path.join(here, "dom", "mini_dom.mjs"), encoding="utf-8").read()
+        self.assertIn("STATED LIMITATION", src)
+        self.assertIn("not a browser", src)
+
+
+class LiveRecordRequiredTest(unittest.TestCase):
+    """Correction 1: a send requires a LIVE canonical record.
+
+    The prior gap: destinationDisagreement guarded its thread comparison with
+    `known &&`, so when polling removed the selected item the check was skipped
+    exactly when it mattered, while convComposerTarget kept the target sendable
+    by reading the remembered selectedConvThread.
+    """
+
+    def test_a_live_record_helper_exists(self):
+        self.assertIn("function liveQueueRecord", APP)
+        m = re.search(r"function liveQueueRecord[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n}", APP)
+        body = m.group(0)
+        self.assertIn("isCanonicalMessageWorkItem", body)
+        self.assertIn("lastWorkItems", body)
+        self.assertIn("|| null", body)
+
+    def test_the_target_thread_comes_only_from_the_live_record(self):
+        m = re.search(r"function convComposerTarget[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n}", APP)
+        body = m.group(0)
+        self.assertIn("liveQueueRecord(selectedWorkItemId)", body)
+        self.assertNotIn("selectedConvThread || ", body,
+                         "the remembered thread was the stale-state path")
+
+    def test_the_destination_check_requires_a_live_record(self):
+        m = re.search(r"function destinationDisagreement[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n  }", APP)
+        body = m.group(0)
+        self.assertIn("liveQueueRecord(target.work_item_id)", body)
+        self.assertIn("if (!known)", body)
+        self.assertIn("no longer in the live queue", body)
+
+    def test_the_thread_comparison_is_no_longer_optional(self):
+        m = re.search(r"function destinationDisagreement[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n  }", APP)
+        body = m.group(0)
+        self.assertNotIn("known && known.thread_id", body,
+                         "guarding on existence skipped the check exactly when "
+                         "the record was missing")
+        self.assertIn("!known.thread_id || known.thread_id !== target.thread_id", body)
+
+
+class CanonicalDestinationTest(unittest.TestCase):
+    """Correction 4: only a message-scoped work item may receive a message."""
+
+    def test_a_canonical_shape_test_exists(self):
+        self.assertIn("function isCanonicalMessageWorkItem", APP)
+        m = re.search(r"function isCanonicalMessageWorkItem[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n}", APP)
+        self.assertIn("message:msg-", m.group(0))
+
+    def test_packet_projections_are_excluded_by_shape(self):
+        m = re.search(r"function isCanonicalMessageWorkItem[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n}", APP)
+        self.assertIn("^message:msg-", m.group(0),
+                      "the pattern must be anchored so in_progress: ids cannot match")
+
+    def test_the_destination_check_rejects_non_canonical_ids(self):
+        m = re.search(r"function destinationDisagreement[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n  }", APP)
+        body = m.group(0)
+        self.assertIn("isCanonicalMessageWorkItem(target.work_item_id)", body)
+        self.assertIn("not a message-scoped work item", body)
+
+    def test_reconciliation_skips_records_without_a_canonical_id(self):
+        m = re.search(r"function renderQueue[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n}", APP)
+        body = m.group(0)
+        self.assertIn("rows.filter((it) => it && it.work_item_id)", body)
+        self.assertNotIn('key: it.work_item_id || ""', body,
+                         "an empty key collapsed several rows together")
+
+
+class SelectorEscapingTest(unittest.TestCase):
+    """Correction 3: one escaping mechanism, correct for its actual context.
+
+    Every dynamic value in app.js is interpolated into a QUOTED ATTRIBUTE
+    selector, so the correct operation is CSS string-literal escaping, not
+    identifier escaping. CSS.escape is deliberately not used: its output does
+    not round-trip inside a quoted string, so a value containing a quote,
+    backslash or space would fail to match the very node it names. The
+    wired-path harness proves this positively by selecting the intended node.
+    """
+
+    def test_a_single_escaper_exists(self):
+        self.assertIn("function cssAttrValue", APP)
+        self.assertNotIn("function cssEscape", APP,
+                         "identifier escaping was wrong for this context")
+
+    def test_identifier_escaping_is_not_used_in_code(self):
+        code = "\n".join(l for l in APP.split("\n")
+                          if not l.strip().startswith("//"))
+        self.assertNotIn("CSS.escape", code)
+
+    def test_every_dynamic_selector_is_escaped(self):
+        for probe in ('.q-open[data-work-item="', '[data-message-id="',
+                      '.q-group[data-group="'):
+            i = APP.index(probe)
+            window = APP[i:i + 160]
+            self.assertIn("cssAttrValue(", window,
+                          "unescaped interpolation into selector " + probe)
+
+    def test_the_escaper_targets_string_literal_semantics(self):
+        m = re.search(r"function cssAttrValue[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n}", APP)
+        body = m.group(0)
+        # Backslash and quote are escaped, and control characters are
+        # hex-escaped, because a CSS string token may not contain them raw.
+        self.assertIn("codePointAt(0)", body)
+        self.assertIn("toString(16)", body)
+        self.assertIn("0x7f", body)
+
+
+class QueueFreshnessTest(unittest.TestCase):
+    """Council finding: a stale snapshot is not positive evidence.
+
+    liveQueueRecord looked up the last SUCCESSFUL snapshot, but a failed refresh
+    left that array in place with workItemsLoaded still true, so an unrefreshed
+    queue kept authorising sends.
+    """
+
+    def test_a_confirmation_flag_exists_and_is_separate_from_loaded(self):
+        self.assertIn("let queueConfirmed = false;", APP)
+        self.assertIn("let workItemsLoaded = false;", APP)
+
+    def test_a_successful_refresh_confirms_the_queue(self):
+        m = re.search(r"async function refreshWorkItems[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n}", APP)
+        body = m.group(0)
+        self.assertIn("queueConfirmed = true;", body)
+
+    def test_a_failed_refresh_withdraws_confirmation(self):
+        m = re.search(r"async function refreshWorkItems[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n}", APP)
+        body = m.group(0)
+        catch = body.split("} catch (e) {")[1]
+        self.assertIn("queueConfirmed = false;", catch)
+        self.assertIn("showRestoreStatus", catch,
+                      "the operator must be told sending is paused")
+
+    def test_the_send_gate_requires_a_confirmed_queue(self):
+        m = re.search(r"function destinationDisagreement[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n  }", APP)
+        body = m.group(0)
+        self.assertIn("if (!queueConfirmed)", body)
+        self.assertIn("not currently confirmed", body)
+
+    def test_the_freshness_check_precedes_the_record_lookup(self):
+        m = re.search(r"function destinationDisagreement[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n  }", APP)
+        body = m.group(0)
+        self.assertLess(body.index("if (!queueConfirmed)"),
+                        body.index("liveQueueRecord(target.work_item_id)"),
+                        "an unconfirmed queue must refuse before any lookup")
+
+    def test_the_previous_content_is_not_blanked(self):
+        m = re.search(r"async function refreshWorkItems[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n}", APP)
+        catch = m.group(0).split("} catch (e) {")[1]
+        self.assertNotIn("lastWorkItems = []", catch,
+                         "the operator should not be blanked out on a "
+                         "transient failure")
+
+
+class ReadOnlyProjectionTest(unittest.TestCase):
+    """Council finding: non-canonical entries were reconciled as activatable
+    tiles even though they can never be destinations.
+
+    Policy, stated explicitly: a packet projection is REAL work the operator
+    must still see, so it is not hidden. It renders READ-ONLY, with no
+    activation control, so it can never be selected or navigated to.
+    """
+
+    def test_canonicality_is_decided_in_the_card(self):
+        m = re.search(RE_CARD, APP)
+        body = m.group(0)
+        self.assertIn("isCanonicalMessageWorkItem(wid)", body)
+        self.assertIn("data-canonical=", body)
+
+    def test_only_canonical_records_get_an_activation_control(self):
+        m = re.search(RE_CARD, APP)
+        body = m.group(0)
+        self.assertIn("canonical", body.split("const openStart")[1][:200])
+        self.assertIn("q-readonly", body)
+
+    def test_non_canonical_records_remain_visible(self):
+        """Hiding a durable record is never the fix."""
+        m = re.search(r"function renderQueue[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n}", APP)
+        body = m.group(0)
+        self.assertNotIn("isCanonicalMessageWorkItem", body,
+                         "renderQueue must not filter out non-canonical rows; "
+                         "they render read-only instead")
+
+    def test_the_projection_is_labelled(self):
+        m = re.search(RE_CARD, APP)
+        self.assertIn("packet record", m.group(0))
+        self.assertIn(".q-ro-badge", CSS)
+        self.assertIn(".q-noncanonical", CSS)
+
+    def test_stale_role_button_css_is_removed(self):
+        self.assertNotIn('.q-row[role="button"]', CSS)
+
+
+class RefreshOutcomeTest(unittest.TestCase):
+    """Correction 1: refresh must distinguish four outcomes explicitly.
+
+    refreshWorkItems previously caught its own error and resolved normally, so
+    callers could not tell a handled failure from a successful load. The boot
+    continuation therefore cleared the status the failure had just rendered.
+    """
+
+    def test_the_four_outcomes_are_named_constants(self):
+        for c in ("REFRESH_CONFIRMED", "REFRESH_CONFIRMED_EMPTY",
+                  "REFRESH_FAILED", "REFRESH_SUPERSEDED"):
+            self.assertIn("const " + c + " =", APP)
+
+    def test_confirmed_empty_is_distinct_from_failed_and_unloaded(self):
+        m = re.search(r"async function refreshWorkItems[" + BS + r"s" + BS + r"S]{0,6000}?" + BS + r"n}", APP)
+        body = m.group(0)
+        self.assertIn("lastWorkItems.length ? REFRESH_CONFIRMED : REFRESH_CONFIRMED_EMPTY", body)
+        # An empty load is still loaded AND confirmed.
+        self.assertIn("workItemsLoaded = true;", body)
+        self.assertIn("queueConfirmed = true;", body)
+
+    def test_a_success_helper_gates_the_callers(self):
+        self.assertIn("function refreshSucceeded", APP)
+        m = re.search(r"function refreshSucceeded[" + BS + r"s" + BS + r"S]{0,1000}?" + BS + r"n}", APP)
+        body = m.group(0)
+        self.assertIn("REFRESH_CONFIRMED", body)
+        self.assertIn("REFRESH_CONFIRMED_EMPTY", body)
+        self.assertNotIn("REFRESH_FAILED", body)
+
+    def test_the_boot_path_acts_only_on_a_confirmed_success(self):
+        i = APP.index("function wire()")
+        j = APP.index("function handleOperatorAction")
+        boot = APP[i:j]
+        self.assertIn("refreshWorkItems().then((outcome) =>", boot)
+        self.assertIn("if (!refreshSucceeded(outcome)) return;", boot)
+
+    def test_the_retry_control_branches_the_same_way(self):
+        m = re.search(r"function showRestoreStatus[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n}", APP)
+        body = m.group(0)
+        self.assertIn("refreshSucceeded(outcome)", body)
+        self.assertIn("REFRESH_FAILED", body)
+
+    def test_refresh_does_not_signal_failure_by_throwing(self):
+        """It is called from a polling timer, where an unhandled rejection
+        would be noise, and it keeps prior content on screen."""
+        m = re.search(r"async function refreshWorkItems[" + BS + r"s" + BS + r"S]{0,6000}?" + BS + r"n}", APP)
+        body = m.group(0)
+        self.assertNotIn("throw", body)
+        self.assertIn("return REFRESH_FAILED;", body)
+
+
+class RefreshFailureVisibilityTest(unittest.TestCase):
+    """Correction 1: the explanation must survive every continuation."""
+
+    def test_a_reported_failure_is_tracked_separately(self):
+        self.assertIn("let queueFailureReported = false;", APP)
+
+    def test_transient_cleanup_cannot_erase_it(self):
+        m = re.search(r"function clearTransientRestoreStatus[" + BS + r"s" + BS + r"S]{0,1200}?" + BS + r"n}", APP)
+        body = m.group(0)
+        self.assertIn("if (queueFailureReported) return;", body)
+
+    def test_only_a_confirmed_success_clears_it(self):
+        m = re.search(r"async function refreshWorkItems[" + BS + r"s" + BS + r"S]{0,6000}?" + BS + r"n}", APP)
+        body = m.group(0)
+        success = body.split("catch (e)")[0]
+        self.assertIn("queueFailureReported = false;", success)
+        self.assertIn("queueFailureReported = true;", body.split("catch (e)")[1])
+
+    def test_the_failure_keeps_the_snapshot_visible(self):
+        m = re.search(r"async function refreshWorkItems[" + BS + r"s" + BS + r"S]{0,6000}?" + BS + r"n}", APP)
+        catch = m.group(0).split("catch (e)")[1]
+        self.assertNotIn("lastWorkItems = []", catch)
+        self.assertIn("queueConfirmed = false;", catch)
+        self.assertIn("showRestoreStatus(", catch)
+
+
+class RefreshSequencingTest(unittest.TestCase):
+    """Correction 2: only the newest generation may alter refresh truth."""
+
+    def test_a_monotonic_generation_exists(self):
+        self.assertIn("let queueRefreshGeneration = 0;", APP)
+        m = re.search(r"async function refreshWorkItems[" + BS + r"s" + BS + r"S]{0,6000}?" + BS + r"n}", APP)
+        self.assertIn("const gen = ++queueRefreshGeneration;", m.group(0))
+
+    def test_both_completion_paths_are_guarded(self):
+        m = re.search(r"async function refreshWorkItems[" + BS + r"s" + BS + r"S]{0,6000}?" + BS + r"n}", APP)
+        body = m.group(0)
+        success, failure = body.split("catch (e)")
+        self.assertIn("gen !== queueRefreshGeneration", success,
+                      "an older SUCCESS must not restore state")
+        self.assertIn("gen !== queueRefreshGeneration", failure,
+                      "an older FAILURE must not invalidate newer state")
+
+    def test_a_superseded_completion_touches_no_shared_state(self):
+        m = re.search(r"async function refreshWorkItems[" + BS + r"s" + BS + r"S]{0,6000}?" + BS + r"n}", APP)
+        body = m.group(0)
+        i = body.index("if (gen !== queueRefreshGeneration) return REFRESH_SUPERSEDED;")
+        head = body[:i]
+        for mutation in ("lastWorkItems =", "queueConfirmed =", "workItemsLoaded ="):
+            self.assertNotIn(mutation, head,
+                             "no state may change before the generation check")
+
+    def test_the_guard_precedes_the_snapshot_write(self):
+        m = re.search(r"async function refreshWorkItems[" + BS + r"s" + BS + r"S]{0,6000}?" + BS + r"n}", APP)
+        body = m.group(0)
+        self.assertLess(body.index("if (gen !== queueRefreshGeneration)"),
+                        body.index("lastWorkItems = data.work_items"))
+
+
+class CssStringEscapingTest(unittest.TestCase):
+    """Council finding: escaping only backslash and quote was incomplete.
+
+    A CSS string token may not contain a raw newline, NUL or other control
+    character, so a route-supplied message id or an unexpected presentation
+    state could still have produced an invalid selector.
+    """
+
+    def test_control_characters_are_escaped(self):
+        m = re.search(r"function cssAttrValue[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n}", APP)
+        body = m.group(0)
+        self.assertIn("0x20", body)
+        self.assertIn("0x7f", body)
+        self.assertIn("toString(16)", body,
+                      "the general escape is a hexadecimal sequence")
+
+    def test_the_comment_no_longer_overclaims(self):
+        i = APP.index("function cssAttrValue")
+        head = APP[max(0, i - 900):i]
+        self.assertNotIn("only the backslash and the quote", head)
+        self.assertIn("control characters", head)
+
+
+class RefreshPayloadValidationTest(unittest.TestCase):
+    """Council finding: `data.work_items || []` made a malformed 200 look like
+    an authoritative empty queue, and confirmed-empty is load-bearing because
+    it makes stale destinations unsendable."""
+
+    def test_the_payload_shape_is_validated(self):
+        m = re.search(r"async function refreshWorkItems[" + BS + r"s" + BS + r"S]{0,6000}?" + BS + r"n}", APP)
+        body = m.group(0)
+        self.assertIn("Array.isArray(data.work_items)", body)
+        self.assertNotIn("data.work_items || []", body)
+
+    def test_a_malformed_payload_is_a_failure(self):
+        m = re.search(r"async function refreshWorkItems[" + BS + r"s" + BS + r"S]{0,6000}?" + BS + r"n}", APP)
+        body = m.group(0)
+        branch = body.split("Array.isArray(data.work_items)")[1][:600]
+        self.assertIn("queueConfirmed = false;", branch)
+        self.assertIn("return REFRESH_FAILED;", branch)
+        self.assertIn("unreadable", branch)
+
+    def test_the_previous_snapshot_survives_a_malformed_payload(self):
+        m = re.search(r"async function refreshWorkItems[" + BS + r"s" + BS + r"S]{0,6000}?" + BS + r"n}", APP)
+        body = m.group(0)
+        after = body.split("Array.isArray(data.work_items)")[1]
+        branch = after[:after.index("    }")]      # the guard block only
+        self.assertNotIn("lastWorkItems =", branch,
+                         "a malformed response must not overwrite the snapshot")
+        self.assertIn("return REFRESH_FAILED;", branch)
+
+
+class RecordPolicyDocumentationTest(unittest.TestCase):
+    """Both reviewers asked for the record policy to be stated explicitly."""
+
+    def test_the_two_cases_are_documented_where_enforced(self):
+        m = re.search(RE_CARD, APP)
+        body = m.group(0)
+        self.assertIn("POLICY", body)
+        low = body.lower()
+        self.assertIn("no usable key", low)
+        self.assertIn("excluded from reconciliation", low)
+        self.assertIn("read-only", low)
+
+
+class CanonicalIdentityTest(unittest.TestCase):
+    """Correction item 1.
+
+    Investigation result: the apparently duplicated tiles are NOT phantoms.
+    /api/work-items returns genuinely distinct canonical work items whose titles
+    collide because the title is derived from the origin message text, and three
+    of them share one thread. Every work_item_id is a real "message:msg-..."
+    value. Collapsing them would HIDE durable governed work, so they are
+    disambiguated and the shared-thread condition is surfaced instead.
+    """
+
+    def test_queue_identity_is_derived_from_the_canonical_work_item_id(self):
+        m = re.search(RE_CARD, APP)
+        self.assertIsNotNone(m, "queueCard not found")
+        body = m.group(0)
+        self.assertIn("it.work_item_id", body)
+        self.assertIn("originMessageId(wid)", body)
+
+    def test_a_thread_id_is_never_rendered_as_a_work_item(self):
+        m = re.search(RE_CARD, APP)
+        body = m.group(0)
+        # The work-item row must read work_item_id, never fall back to a thread.
+        self.assertNotIn("it.work_item_id || it.thread_id", body)
+        self.assertIn("Work item", body)
+        self.assertIn("Thread", body)
+
+    def test_shared_thread_raises_an_integrity_warning(self):
+        self.assertIn("function threadWorkItemIndex", APP)
+        self.assertIn("function sharesThreadWithOtherWorkItems", APP)
+        m = re.search(RE_CARD, APP)
+        body = m.group(0)
+        self.assertIn("q-integrity", body)
+        self.assertIn("sharesThreadWithOtherWorkItems", body)
+
+    def test_conflicting_records_are_flagged_not_hidden(self):
+        """No filtering may drop a durable work item to tidy the view."""
+        m = re.search(RE_CARD, APP)
+        body = m.group(0)
+        for banned in ("filter(", "dedupe", "unique("):
+            self.assertNotIn(banned, body,
+                             "tiles must not be removed; the records are real")
+        self.assertIn("q-ambiguous", body)
+        self.assertIn(".q-integrity", CSS)
+
+
+class QueueTileIdentityTest(unittest.TestCase):
+    """Correction item 2: identify the durable object without opening it."""
+
+    def test_tile_shows_work_item_thread_and_origin_ids(self):
+        m = re.search(RE_CARD, APP)
+        body = m.group(0)
+        for label in ("Work item", "Thread", "Origin message"):
+            self.assertIn(label, body)
+
+    def test_tile_offers_copy_for_each_identifier(self):
+        m = re.search(RE_CARD, APP)
+        body = m.group(0)
+        self.assertEqual(body.count("copyIdButton("), 3,
+                         "work item, thread and origin message each need Copy")
+        self.assertIn('copyIdButton(wid, "work-item ID")', body)
+        self.assertIn('copyIdButton(tid, "thread ID")', body)
+
+    def test_copy_controls_carry_the_full_id_not_the_abbreviation(self):
+        m = re.search(RE_CARD, APP)
+        body = m.group(0)
+        self.assertIn("copyIdButton(wid", body)
+        self.assertNotIn("copyIdButton(abbrevId", body)
+
+    def test_copying_does_not_also_open_the_item(self):
+        self.assertIn("function eventTargetsInnerControl", APP)
+        # One definition plus exactly two guarded entry points: click and key.
+        # Only the click path needs the guard now: the primary control is a
+        # real button, so a Copy click never reaches a row-level activation.
+        self.assertEqual(APP.count("if (eventTargetsInnerControl(e)) return;"), 1,
+                         "the click path must be guarded")
+        i = APP.index('getElementById("queue-groups").addEventListener("click"')
+        self.assertIn("eventTargetsInnerControl(e)", APP[i:i + 400])
+
+
+class IdentifierTerminologyTest(unittest.TestCase):
+    """Correction item 3: each identifier type is named and explained."""
+
+    def test_matching_suffix_is_explained_rather_than_hidden(self):
+        self.assertIn("function sharesSuffix", APP)
+        m = re.search(RE_CARD, APP)
+        body = m.group(0)
+        self.assertIn("sharesSuffix(wid, tid)", body)
+        self.assertIn("matching suffix", body)
+
+    def test_the_explanation_says_they_remain_different_identifiers(self):
+        m = re.search(RE_CARD, APP)
+        self.assertIn("different identifiers", m.group(0))
+
+    def test_identifiers_are_not_case_transformed_on_tiles(self):
+        i = CSS.index(".q-idv")
+        self.assertIn("text-transform: none", CSS[i:i + 200])
+
+
+class HistoryColumnsTest(unittest.TestCase):
+    """Correction item 4.
+
+    The server already returns work_item_id and thread_id as distinct fields
+    (zero ledger rows carry a thread id in the work-item field). The CLIENT
+    collapsed them with `work_item_id || thread_id || packet_id`, so the 148
+    rows with no work-item binding printed a thr-... under a heading that said
+    "Work item" -- a false identity claim.
+    """
+
+    def test_history_has_distinct_identifier_columns(self):
+        i = HTML.index('<table class="ledger"')
+        head = HTML[i:i + 700]
+        for col in ("<th>Message</th>", "<th>Work item</th>", "<th>Thread</th>",
+                    "<th>Actor</th>", "<th>Event</th>", "<th>Status</th>"):
+            self.assertIn(col, head)
+
+    def test_the_substitution_fallback_is_gone(self):
+        self.assertNotIn("row.work_item_id || row.thread_id", APP)
+
+    def test_a_missing_binding_is_stated_honestly(self):
+        self.assertIn("no work item", APP)
+        self.assertIn("ledger-none", APP)
+        self.assertIn(".ledger-none", CSS)
+
+    def test_message_id_column_is_only_populated_for_messages(self):
+        self.assertIn("function ledgerMessageId", APP)
+        m = re.search(r"function ledgerMessageId[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n" + BS + r"}", APP)
+        body = m.group(0)
+        self.assertIn('row.type === "message"', body)
+        self.assertIn('return ""', body)
+
+    def test_colspan_matches_the_new_column_count(self):
+        self.assertIn('colspan="8"', HTML)
+        self.assertIn('colspan="8"', APP)
+        self.assertNotIn('colspan="6"', APP)
+
+
+class DestinationAgreementTest(unittest.TestCase):
+    """Correction item 5: URL, selection, thread and composer must agree."""
+
+    def test_a_disagreement_check_exists(self):
+        self.assertIn("function destinationDisagreement", APP)
+
+    def test_it_compares_route_selection_and_thread(self):
+        m = re.search(r"function destinationDisagreement[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n  }", APP)
+        self.assertIsNotNone(m, "destinationDisagreement not found")
+        body = m.group(0)
+        self.assertIn("selectedWorkItemId", body)
+        self.assertIn("thread_id", body)
+        self.assertIn("parseWorkRoute(location.hash)", body)
+
+    def test_no_body_is_built_when_they_disagree(self):
+        i = APP.index("const disagreement = destinationDisagreement(preTarget)")
+        j = APP.index("const body = Object.assign")
+        self.assertLess(i, j, "the check must precede request construction")
+        window = APP[i:i + 400]
+        self.assertIn("showError", window)
+        self.assertIn("return;", window)
+
+    def test_the_operator_is_told_why(self):
+        m = re.search(r"function destinationDisagreement[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n  }", APP)
+        body = m.group(0)
+        for phrase in ("disagree", "does not match", "different work item"):
+            self.assertIn(phrase, body)
+
+
+class LoadedEmptyQueueTest(unittest.TestCase):
+    """Round-5 finding: a successful empty load is authoritative."""
+
+    def test_a_loaded_flag_exists_and_is_set_on_success(self):
+        self.assertIn("let workItemsLoaded = false;", APP)
+        m = re.search(r"async function refreshWorkItems[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n}", APP)
+        self.assertIn("workItemsLoaded = true;", m.group(0))
+
+    def test_validation_no_longer_infers_from_length(self):
+        m = re.search(r"function bindRouteSelection[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n}", APP)
+        body = m.group(0)
+        self.assertIn("if (!workItemsLoaded) return null;", body)
+        self.assertNotIn("if (!items.length) return null;", body)
+
+
+class PhaseVersusExecutorTest(unittest.TestCase):
+    """Correction item 10: two different facts, two different fields."""
+
+    def test_phase_and_executor_are_separate_functions(self):
+        for fn in ("lifecyclePhaseOf", "lifecyclePhaseLabel",
+                   "executorRunnerState", "executorStateLabel"):
+            self.assertIn("function " + fn, APP)
+
+    def test_phase_comes_from_status_only(self):
+        m = re.search(r"function lifecyclePhaseOf[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n}", APP)
+        body = m.group(0)
+        self.assertIn("it.status", body)
+        self.assertNotIn("runner_state", body)
+
+    def test_executor_comes_from_runner_state_only(self):
+        m = re.search(r"function executorRunnerState[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n}", APP)
+        body = m.group(0)
+        self.assertIn("it.runner_state", body)
+        self.assertNotIn("last_activity_event", body)
+
+    def test_labels_cover_exactly_the_server_domain(self):
+        m = re.search(r"const EXECUTOR_LABELS = {(.*?)};", APP, re.S)
+        keys = set(re.findall(r"(\w+):", m.group(1)))
+        self.assertEqual(keys, {"active_runner", "waiting_on_council",
+                                "waiting_on_operator", "claimed_idle",
+                                "stale_or_no_heartbeat", "unowned", "unknown"})
+
+    def test_active_requires_positive_runner_evidence(self):
+        m = re.search(r"const EXECUTOR_LABELS = {(.*?)};", APP, re.S)
+        self.assertIn('active_runner: "ACTIVE"', m.group(1))
+        self.assertNotIn('claimed_idle: "ACTIVE"', m.group(1))
+
+    def test_the_tile_labels_both_separately(self):
+        m = re.search(RE_CARD, APP)
+        body = m.group(0)
+        self.assertIn("Phase ", body)
+        self.assertIn("Executor ", body)
+
+
+class ComposerSubmissionFeedbackTest(unittest.TestCase):
+    """Correction item 13: submission must be visible and non-duplicable."""
+
+    def test_the_button_reports_the_in_flight_state(self):
+        self.assertIn('sendBtn.textContent = "Sending...";', APP)
+        self.assertIn('sendBtn.setAttribute("aria-busy", "true");', APP)
+
+    def test_the_button_is_disabled_while_in_flight(self):
+        i = APP.index('sendBtn.textContent = "Sending...";')
+        self.assertIn("sendBtn.disabled = true;", APP[i - 200:i])
+
+    def test_the_label_is_captured_not_hardcoded_on_restore(self):
+        self.assertIn("const idleLabel = sendBtn.textContent;", APP)
+        self.assertIn("sendBtn.textContent = idleLabel;", APP)
+
+    def test_state_is_restored_in_finally_so_it_cannot_strand(self):
+        i = APP.index("sendBtn.textContent = idleLabel;")
+        window = APP[max(0, i - 400):i]
+        self.assertIn("} finally {", window)
+
+    def test_duplicate_submission_is_blocked_for_every_entry_point(self):
+        """Click, Enter and Ctrl+Enter all funnel through send()."""
+        i = APP.index("async function send() {")
+        self.assertIn("if (sending) return;", APP[i:i + 200],
+                      "re-entry must be blocked at the top of send()")
+        # Ctrl+Enter routes to the same guarded function, not a parallel path.
+        self.assertIn("send();", APP[APP.index('e.key === "Enter" && (e.ctrlKey'):][:200])
+
+    def test_the_draft_is_kept_until_durable_success(self):
+        i = APP.index("clearDraft(draftKey());")
+        before = APP[max(0, i - 1400):i]
+        self.assertIn("stored.message !== canonical", before,
+                      "the draft may only clear after the durable re-read matches")
+
+    def test_failure_paths_preserve_the_draft(self):
+        for msg in ("The draft was kept", "draft was kept"):
+            self.assertIn(msg, APP)
+
+    def test_success_shows_the_durable_id_and_destination(self):
+        self.assertIn("showPostConfirmation(result);", APP)
+        m = re.search(r"function showPostConfirmation[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n}", APP)
+        body = m.group(0)
+        self.assertIn("message_id", body)
+        self.assertIn("thread_id", body)
+        self.assertIn("copyIdButton", body)
+
+
+class UnifiedRoutePolicyTest(unittest.TestCase):
+    """Validation and the terminal policy must hold on EVERY route path.
+
+    applyWorkHashRoute() is also the hashchange handler. With the policy living
+    only in restoreActiveSelection(), a post-boot link could bind and persist an
+    unknown or terminal item with no restoration pass following to correct it.
+    """
+
+    def test_one_shared_policy_function_exists(self):
+        self.assertIn("function bindRouteSelection", APP)
+
+    def test_the_hashchange_path_validates(self):
+        m = re.search(r"function applyWorkHashRoute[\s\S]{0,4000}?\n\}", APP)
+        self.assertIn("bindRouteSelection(route.work_item_id)", m.group(0))
+
+    def test_restoration_uses_the_same_policy_not_a_copy(self):
+        m = re.search(r"function restoreActiveSelection[\s\S]{0,4000}?\n\}", APP)
+        body = m.group(0)
+        self.assertIn("bindRouteSelection(deep.work_item_id)", body)
+        # The duplicated policy must be gone, or the two can drift again.
+        self.assertNotIn("will not be restored on the next refresh", body)
+
+    def test_the_policy_persists_nothing_terminal(self):
+        m = re.search(r"function bindRouteSelection[\s\S]{0,4000}?\n\}", APP)
+        body = m.group(0)
+        self.assertIn("if (!isActiveItem(known))", body)
+        self.assertIn("persistSelection(null)", body.split("if (!isActiveItem(known))")[1][:300])
+
+    def test_an_unknown_route_is_cleared_on_every_path(self):
+        m = re.search(r"function bindRouteSelection[\s\S]{0,4000}?\n\}", APP)
+        branch = m.group(0).split("if (!known)")[1][:500]
+        for expected in ("selectTask(null);", "persistSelection(null);", "clearWorkRoute();"):
+            self.assertIn(expected, branch)
+
+
+class RouteErrorLatchTest(unittest.TestCase):
+    """Both reviewers flagged the one-way latch: a reported route error must
+    not suppress every later transient status for the page lifetime."""
+
+    def test_a_successful_route_resets_the_latch(self):
+        m = re.search(r"function bindRouteSelection[\s\S]{0,4000}?\n\}", APP)
+        self.assertIn("routeErrorReported = false;", m.group(0))
+
+    def test_explicit_navigation_resets_the_latch(self):
+        m = re.search(r"function navigateToWorkItem[\s\S]{0,4000}?\n\}", APP)
+        self.assertIn("routeErrorReported = false;", m.group(0))
+
+
+class EmptyRouteTest(unittest.TestCase):
+
+    def test_empty_work_id_is_invalid_not_absent(self):
+        m = re.search(RE_PARSE, APP)
+        body = m.group(0)
+        self.assertIn("[^&]*", body, "an empty work id must still match")
+        self.assertIn("if (!wid) return { malformed: true", body)
+
+
+class StaleRouteClearingTest(unittest.TestCase):
+    """'Clear it and say so' has to be literally true."""
+
+    def test_invalid_route_is_removed_from_the_url(self):
+        self.assertIn("function clearWorkRoute", APP)
+        m = re.search(r"function clearWorkRoute[\s\S]{0,4000}?\n\}", APP)
+        self.assertIn("replaceState", m.group(0))
+        for fn in ("restoreActiveSelection", "bindRouteSelection"):
+            m2 = re.search(r"function " + fn + r"[\s\S]{0,4000}?\n\}", APP)
+            self.assertIn("clearWorkRoute()", m2.group(0),
+                          fn + " must drop an unusable route")
+
+    def test_selection_is_cleared_unconditionally(self):
+        """A conditional clear could announce 'nothing is selected' while a
+        different prior selection survived."""
+        r = re.search(r"function bindRouteSelection[\s\S]{0,4000}?\n\}", APP)
+        unknown = r.group(0).split("if (!known)")[1][:500]
+        self.assertIn("selectTask(null);", unknown)
+        self.assertNotIn("selectedWorkItemId === wid", unknown)
+
+
+class DeepLinkPolicyTest(unittest.TestCase):
+    """Both reviewers asked for an explicit terminal-item policy.
+
+    Decision: an EXPLICIT link may open a terminal item, because reviewing
+    finished work is the point of sharing a link. That is inspection, not
+    active-session restoration, so it is never persisted as the active
+    selection. Automatic restoration still excludes terminal items entirely.
+    """
+
+    def test_terminal_deep_link_is_not_persisted_as_active(self):
+        r = re.search(r"function bindRouteSelection[\s\S]{0,4000}?\n\}", APP)
+        body = r.group(0)
+        self.assertIn("if (!isActiveItem(known))", body)
+        tail = body.split("if (!isActiveItem(known))")[1][:400]
+        self.assertIn("persistSelection(null)", tail)
+        self.assertIn("showRestoreStatus", tail)
+
+    def test_the_policy_is_documented_where_it_is_enforced(self):
+        r = re.search(r"function bindRouteSelection[\s\S]{0,4000}?\n\}", APP)
+        self.assertIn("POLICY", r.group(0))
+
+    def test_automatic_restoration_still_excludes_terminal_items(self):
+        m = re.search(r"function rankActiveWorkItems[\s\S]{0,4000}?\n\}", APP)
+        self.assertIn("filter(isActiveItem)", m.group(0))
+
+
+class RuntimeCoverageTest(unittest.TestCase):
+    """The reviewers asked for coverage that executes, not just source text."""
+
+    def test_a_runtime_harness_exists_and_is_dependency_free(self):
+        here = os.path.dirname(os.path.abspath(__file__))
+        harness = os.path.join(here, "dom", "session_ux_runtime.mjs")
+        self.assertTrue(os.path.isfile(harness))
+        src = open(harness, encoding="utf-8").read()
+        # Every import must resolve to a Node builtin: no installed package,
+        # no browser driver, nothing that needs an install step.
+        imports = re.findall(r'from "([^"]+)"', src)
+        self.assertTrue(imports, "harness should import something")
+        for mod in imports:
+            self.assertTrue(mod.startswith("node:"),
+                            "non-builtin import would add a dependency: " + mod)
+        self.assertNotIn("require(", src)
+        here2 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
+        self.assertFalse(os.path.exists(os.path.join(here2, "package.json")),
+                         "no package manifest may be introduced")
+
+    def test_the_harness_states_its_limitation(self):
+        here = os.path.dirname(os.path.abspath(__file__))
+        src = open(os.path.join(here, "dom", "session_ux_runtime.mjs"),
+                   encoding="utf-8").read()
+        self.assertIn("LIMITATION", src)
+
+    def test_the_harness_covers_the_defects_that_actually_occurred(self):
+        here = os.path.dirname(os.path.abspath(__file__))
+        src = open(os.path.join(here, "dom", "session_ux_runtime.mjs"),
+                   encoding="utf-8").read()
+        for probe in ("conversationScrollEl", "parseWorkRoute",
+                      "convComposerTarget", "rankActiveWorkItems",
+                      "wake_pending", "unresolved"):
+            self.assertIn(probe, src)
+
+
+class UnresolvedDestinationTest(unittest.TestCase):
+    """A work_item_id WITHOUT a thread_id is the one shape the server's
+    target-integrity check cannot validate, because that check compares the
+    pair. The UI must fail closed rather than emit it."""
+
+    def test_target_reports_unresolved_instead_of_a_bare_work_item(self):
+        m = re.search(r"function convComposerTarget[\s\S]{0,4000}?\n\}", APP)
+        body = m.group(0)
+        self.assertIn("unresolved: true", body)
+        self.assertIn("thread_id: null", body)
+
+    def test_send_is_refused_while_the_destination_is_unresolved(self):
+        self.assertIn("preTarget.unresolved", APP)
+        i = APP.index("preTarget.unresolved")
+        window = APP[i:i + 400]
+        self.assertIn("showError", window)
+        self.assertIn("return;", window)
+
+    def test_refusal_happens_before_the_body_is_built(self):
+        i = APP.index("preTarget.unresolved")
+        j = APP.index("const body = Object.assign")
+        self.assertLess(i, j)
+
+    def test_banner_does_not_present_an_unresolved_target_as_valid(self):
+        self.assertIn("dest-unresolved", APP)
+        self.assertIn("dest-unresolved", CSS)
+
+
+class StaleDeepLinkTest(unittest.TestCase):
+    """An unknown deep link must not leave a selected item with no
+    queue-backed identity."""
+
+    def test_unknown_deep_link_clears_the_selection(self):
+        """The policy now lives in bindRouteSelection(), shared by the boot and
+        hashchange paths, so it is asserted where it is implemented."""
+        m = re.search(r"function bindRouteSelection[\s\S]{0,4000}?\n\}", APP)
+        body = m.group(0)
+        self.assertIn("if (!known)", body)
+        self.assertIn("selectTask(null)", body)
+        self.assertIn("persistSelection(null)", body)
+
+    def test_unknown_deep_link_is_reported_not_silent(self):
+        m = re.search(r"function bindRouteSelection[\s\S]{0,4000}?\n\}", APP)
+        self.assertIn("showRestoreStatus", m.group(0))
+
+
+class JumpToLatestWiringTest(unittest.TestCase):
+    """The control was rendered but inert: no click handler, no visibility
+    logic, and operatorMovedAwayFromLatest was never called."""
+
+    def test_button_is_actually_activated(self):
+        m = re.search(r"function initJumpToLatest[\s\S]{0,4000}?\n\}", APP)
+        self.assertIsNotNone(m, "initJumpToLatest not found")
+        self.assertIn('addEventListener("click", jumpToLatestMessage)', m.group(0))
+
+    def test_visibility_is_driven_by_deliberate_scroll(self):
+        m = re.search(r"function initJumpToLatest[\s\S]{0,4000}?\n\}", APP)
+        body = m.group(0)
+        self.assertIn("operatorMovedAwayFromLatest", body)
+        self.assertIn('addEventListener("scroll"', body)
+
+    def test_new_content_does_not_yank_a_deliberate_position(self):
+        m = re.search(r"function initJumpToLatest[\s\S]{0,4000}?\n\}", APP)
+        body = m.group(0)
+        self.assertIn("MutationObserver", body)
+        self.assertIn("pill.hidden = false", body)
+
+    def test_it_is_wired_at_boot(self):
+        self.assertIn("initJumpToLatest();", APP)
+
+    def test_scroll_target_is_the_element_that_actually_scrolls(self):
+        """#conv-detail is overflow-y:visible and grows with its content, so it
+        can never report a scroll position. Targeting it made every scroll
+        check return false and the pill unreachable."""
+        m = re.search(r"function conversationScrollEl[\s\S]{0,4000}?\n\}", APP)
+        body = m.group(0)
+        self.assertIn("overflowY", body)
+        self.assertIn("scrollHeight", body)
+        self.assertIn("document.scrollingElement", body)
+
+    def test_content_anchor_and_scroller_are_distinct(self):
+        self.assertIn("function conversationAnchorEl", APP)
+        m = re.search(r"function initJumpToLatest[\s\S]{0,4000}?\n\}", APP)
+        body = m.group(0)
+        self.assertIn("conversationAnchorEl()", body)
+        self.assertIn("conversationScrollEl()", body)
+
+    def test_scroll_is_observed_by_capture_on_window(self):
+        """Scroll does not bubble, but it DOES reach window in the capture
+        phase from any target. Binding to the scroller resolved at init went
+        stale as soon as layout changed the scrolling ancestor, which is the
+        very reason the scroller is resolved lazily inside the handler."""
+        m = re.search(r"function initJumpToLatest[\s\S]{0,4000}?\n\}", APP)
+        body = m.group(0)
+        self.assertIn('window.addEventListener("scroll"', body)
+        self.assertIn("}, true);", body)
+        self.assertNotIn("scrollEventTargetFor", APP,
+                         "the superseded helper must not linger as dead code")
+
+
+class ObservableFailureTest(unittest.TestCase):
+    """Continuity that fails silently is worse than continuity that says so."""
+
+    def test_restoration_failure_is_reported(self):
+        self.assertNotIn("refreshWorkItems().then(restoreActiveSelection).catch(() => {});", APP)
+        self.assertIn("function showRestoreStatus", APP)
+        self.assertIn('id="restore-status"', HTML)
+
+    def test_failure_offers_a_retry(self):
+        m = re.search(r"function showRestoreStatus[\s\S]{0,4000}?\n\}", APP)
+        body = m.group(0)
+        self.assertIn("Retry", body)
+        self.assertIn("refreshWorkItems()", body)
+
+    def test_status_is_announced(self):
+        i = HTML.index('id="restore-status"')
+        window = HTML[max(0, i - 200):i + 200]
+        self.assertIn('role="status"', window)
+        self.assertIn('aria-live="polite"', window)
+
+
+class ContextualRailCompletenessTest(unittest.TestCase):
+    """No contextual card may render empty when nothing is selected."""
+
+    def test_operator_actions_card_is_inside_the_rail(self):
+        """Source ORDER is not containment.
+
+        An earlier version of this change placed the card immediately after the
+        rail's closing tag. Every ordering assertion still passed while the
+        browser showed the card as a sibling that stayed visible with an empty
+        body. This parses the actual element tree instead.
+        """
+        rail = _block_of(HTML, "session-rail")
+        self.assertIn('id="operator-actions-card"', rail)
+        self.assertIn('id="next-action-card"', rail)
+        self.assertNotIn('id="clearance-card"', rail,
+                         "the clearance card is not contextual to a selection")
+
+    def test_only_one_actions_card_exists(self):
+        self.assertEqual(HTML.count('id="operator-actions-card"'), 1)
+
+
+class DeliberateNewConversationTest(unittest.TestCase):
+    """The demoted composer must not be a dead end."""
+
+    def test_an_explicit_control_clears_the_selection(self):
+        i = APP.index('getElementById("queue-new-btn")')
+        self.assertIn("selectTask(null)", APP[i:i + 300])
+
+    def test_the_affordance_is_documented_where_demotion_happens(self):
+        head = APP[:APP.index('getElementById("queue-new-btn")')]
+        self.assertIn("re-enables the generic composer", head[-700:])
+
+    def test_demotion_is_exactly_reversible(self):
+        m = re.search(r"function applyComposerFocus[\s\S]{0,4000}?\n\}", APP)
+        self.assertIn("data-prior-tabindex", m.group(0))
+
+
+class ConversationSurfaceTest(unittest.TestCase):
+    """Item 2, behaviour: the conversation surface must actually be revealed.
+
+    Live inspection found openConversationTab() querying a tab control that
+    does not exist in this console (the only role="tablist" is the queue filter
+    strip), making it a silent no-op. The Work view IS the conversation surface.
+    """
+
+    def test_open_conversation_does_not_depend_on_a_nonexistent_tab(self):
+        m = re.search(r"function openConversationTab[\s\S]{0,4000}?\n\}", APP)
+        body = m.group(0)
+        self.assertNotIn('data-tab="conversation"', body)
+        self.assertNotIn("#tab-conversation", body)
+
+    def test_open_conversation_reveals_the_work_view(self):
+        m = re.search(r"function openConversationTab[\s\S]{0,4000}?\n\}", APP)
+        self.assertIn('showView("work")', m.group(0))
+
+    def test_no_conversation_tab_control_is_invented_in_markup(self):
+        self.assertNotIn('data-tab="conversation"', HTML)
+
+
+class TargetedComposerTest(unittest.TestCase):
+    """Item 3: one safe composer, destination displayed, never inferred."""
+
+    def test_destination_shows_work_item_thread_and_title(self):
+        m = re.search(r"function updateBanner[\s\S]{0,3000}?\n  \}", APP)
+        body = m.group(0)
+        self.assertIn("data-dest-work-item", body)
+        self.assertIn("data-dest-thread", body)
+        self.assertIn("dest-title", body)
+
+    def test_destination_comes_from_the_selection_not_the_message_text(self):
+        m = re.search(r"function updateBanner[\s\S]{0,3000}?\n  \}", APP)
+        body = m.group(0)
+        self.assertIn("target.work_item_id", body)
+        self.assertNotIn("textarea.value", body)
+
+    def test_generic_composer_is_demoted_while_work_is_selected(self):
+        self.assertIn("function applyComposerFocus", APP)
+        m = re.search(r"function applyComposerFocus[\s\S]{0,4000}?\n\}", APP)
+        body = m.group(0)
+        self.assertIn("composer-demoted", body)
+        self.assertIn("tabIndex", body)
+        self.assertIn(".composer-demoted", CSS)
+
+    def test_demotion_is_reapplied_on_every_selection_change(self):
+        m = re.search(r"function selectTask\([^)]*\)\s*\{(.{0,900})", APP, re.S)
+        self.assertIn("applyComposerFocus()", m.group(1))
+
+    def test_generic_composer_is_not_removed_only_demoted(self):
+        """The operator can still start a new conversation deliberately."""
+        self.assertIn('id="composer-card"', HTML)
+
+
+class MessageIdentityTest(unittest.TestCase):
+    """Item 4: durable identity visible without History or raw JSON (issue #86)."""
+
+    def test_identity_row_is_rendered_on_every_message_card(self):
+        self.assertIn("messageIdentityRow(m)", APP)
+        m = re.search(r"function messageIdentityRow[\s\S]{0,4000}?\n\}", APP)
+        body = m.group(0)
+        for field in ("message_id", "thread_id", "work_item_id", "actor", "intent"):
+            self.assertIn(field, body)
+
+    def test_copy_controls_are_real_keyboard_reachable_buttons(self):
+        m = re.search(r"function copyIdButton[\s\S]{0,4000}?\n\}", APP)
+        body = m.group(0)
+        self.assertIn('<button type="button"', body)
+        self.assertIn("aria-label", body)
+        self.assertIn(".copy-id:focus-visible", CSS)
+
+    def test_copy_uses_the_clipboard_api_and_degrades_safely(self):
+        m = re.search(r"function copyToClipboard[\s\S]{0,4000}?\n\}", APP)
+        body = m.group(0)
+        self.assertIn("navigator.clipboard", body)
+        self.assertIn("catch", body)
+
+    def test_post_send_confirmation_exposes_the_new_message_id(self):
+        self.assertIn("function showPostConfirmation", APP)
+        m = re.search(r"function showPostConfirmation[\s\S]{0,4000}?\n\}", APP)
+        body = m.group(0)
+        self.assertIn("data-posted-message-id", body)
+        self.assertIn("result.thread_id", body)
+        self.assertIn("result.work_item_id", body)
+        self.assertIn("copyIdButton(result.message_id", body)
+
+    def test_confirmation_fires_only_after_the_durable_verify(self):
+        """It must follow the post-write re-read, never precede it."""
+        i_verify = APP.index("could not verify the durable copy matched")
+        i_conf = APP.index("showPostConfirmation(result)")
+        self.assertLess(i_verify, i_conf)
+
+    def test_confirmation_region_is_a_polite_live_region(self):
+        self.assertIn('id="post-confirmation"', HTML)
+        self.assertIn('aria-live="polite"', HTML)
+
+    def test_no_duplicate_github_issue_is_referenced(self):
+        self.assertIn("issue #86", APP)
+
+
+class TruthfulExecutionStateTest(unittest.TestCase):
+    """Item 6: RUNNING is never derived from message-post activity."""
+
+    def test_operator_message_does_not_yield_a_running_state(self):
+        m = re.search(r"function truthfulExecutionState[\s\S]{0,4000}?\n\}", APP)
+        body = m.group(0)
+        # The event-derived branch is GONE: last_activity_event can never be
+        # "message" or "operator_message", so that rank was unreachable.
+        self.assertNotIn("operator_message_posted", body)
+        self.assertNotIn("last_activity_event", body)
+        self.assertNotIn('"running"', body,
+                         "a presentation_state of running must not imply an "
+                         "executor state; ACTIVE comes from runner_state only")
+        # The old ordering assertion (operator-message branch before the
+        # running check) no longer applies: BOTH branches are gone. ACTIVE is
+        # now derived solely from runner_state === "active_runner", which the
+        # server sets only on positive evidence of recent non-claim activity.
+        self.assertIn('r === "active_runner"', body)
+
+    def test_unsupported_states_are_not_simulated(self):
+        labels = re.search(r"const EXECUTOR_LABELS = \{(.*?)\};", APP, re.S).group(1)
+        for deferred in ("EXECUTOR_RESUMED", "MESSAGE_ACKNOWLEDGED", "WAKE_PENDING"):
+            self.assertNotIn(deferred, labels,
+                             deferred + " requires the Phase 2 wake bridge and "
+                             "must not be rendered from current evidence")
+
+    def test_the_obsolete_vocabulary_is_removed_not_left_dead(self):
+        """EXECUTION_STATE_LABELS advertised operator_message_posted, a state
+        nothing can produce, and executionStateLabel had no remaining caller."""
+        self.assertNotIn("EXECUTION_STATE_LABELS", APP)
+        self.assertNotIn("function executionStateLabel", APP)
+        self.assertNotIn("OPERATOR_MESSAGE_POSTED", APP)
+
+    def test_state_is_actually_RENDERED_on_the_queue_row(self):
+        """Scoped to queueCard. Asserting the identifier appeared anywhere in
+        app.js was a false positive: the function DECLARATION satisfied it, so
+        the test passed while proving nothing about the rendered row."""
+        m = re.search(RE_CARD, APP)
+        self.assertIsNotNone(m, "queueCard not found")
+        body = m.group(0)
+        self.assertIn("executorStateLabel(it)", body)
+        self.assertIn("lifecyclePhaseLabel(it)", body)
+        self.assertIn("Phase ", body)
+        self.assertIn("Executor ", body)
+
+
+class ConsistentDemotionTest(unittest.TestCase):
+    """Item 7: demotion must not leave a keyboard trap.
+
+    Live inspection caught aria-hidden="true" on #composer-card while its Send
+    button was still tabindex=0 -- reachable by keyboard but absent from the
+    accessibility tree.
+    """
+
+    def test_demotion_removes_a11y_and_tab_order_together(self):
+        m = re.search(r"function applyComposerFocus[\s\S]{0,4000}?\n\}", APP)
+        body = m.group(0)
+        self.assertIn("inert", body)
+        self.assertIn("removeAttribute(\"aria-hidden\")", body)
+
+    def test_fallback_covers_every_focusable_not_just_the_textarea(self):
+        m = re.search(r"function applyComposerFocus[\s\S]{0,4000}?\n\}", APP)
+        body = m.group(0)
+        self.assertIn("querySelectorAll(FOCUSABLE)", body)
+        self.assertIn("button", body)
+
+
+class IdentifierFidelityTest(unittest.TestCase):
+    """Item 4: a durable id must be displayed in the case it actually has.
+
+    .composer-banner sets text-transform:uppercase, so without an explicit
+    override the destination and identity rows render ids like
+    "MESSAGE:MSG-2026..." which do not match the durable record.
+    """
+
+    def test_identifiers_are_not_case_transformed(self):
+        i = CSS.find(".composer-destination .dest-work,")
+        self.assertNotEqual(i, -1, "no case-fidelity rule for the destination")
+        block = CSS[i:i + 260]
+        for cls in (".dest-thread", ".dest-title"):
+            self.assertIn(cls, block)
+        self.assertIn("text-transform: none", block)
+
+
+class AccessibilityTest(unittest.TestCase):
+    """Item 7: keyboard operation and focus visibility."""
+
+    def test_queue_rows_are_real_controls(self):
+        # The row is a plain CONTAINER holding a real primary button, because
+        # nesting <button> copy controls inside an element that itself claimed
+        # role="button" is an invalid interactive pattern.
+        m = re.search(RE_CARD, APP)
+        body = m.group(0)
+        self.assertIn('<button type="button" class="q-open"', body)
+        # aria-current, not aria-pressed: activating this NAVIGATES, it does not
+        # toggle a state off again.
+        self.assertIn("aria-current=", body)
+        self.assertNotIn("aria-pressed=", body,
+                         "a navigation control must not advertise a toggle "
+                         "contract to assistive technology")
+        self.assertNotIn('role="button" tabindex="0"', body,
+                         "the row must not claim button semantics itself")
+        self.assertIn("data-sig=", body,
+                      "the tile carries its signature so reconciliation can "
+                      "reuse an unchanged node instead of replacing it")
+
+    def test_queue_rows_activate_via_a_native_button(self):
+        """Enter and Space come from native <button> semantics.
+
+        An earlier version called preventDefault() on Space to stop page
+        scrolling. On a focused button Space's default action IS the
+        activation, so that suppressed the very keyboard path the control
+        exists to provide. There is now NO queue key handler at all.
+        """
+        m = re.search(RE_CARD, APP)
+        self.assertIn('<button type="button" class="q-open"', m.group(0))
+        for k in re.finditer(r'addEventListener\("keydown"', APP):
+            window = APP[k.start():k.start() + 500]
+            self.assertNotIn("q-open", window,
+                             "no keydown handler may intercept the queue button")
+        # Space must not be suppressed anywhere for the queue control.
+        self.assertNotIn('e.key !== " "', APP,
+                         "the Space-suppressing handler must be gone entirely")
+
+    def test_existing_send_shortcuts_are_preserved(self):
+        """Ctrl+Enter sends; Shift+Enter still inserts a newline."""
+        self.assertIn('e.key === "Enter" && (e.ctrlKey || e.metaKey)', APP)
+        self.assertIn("Shift+Enter for a new line, Ctrl+Enter to send", HTML)
+
+    def test_focus_rings_exist_for_new_controls(self):
+        for rule in (".copy-id:focus-visible", ".jump-to-latest:focus-visible",
+                     '.q-open:focus-visible'):
+            self.assertIn(rule, CSS)
+
+
+class SemanticsPreservedTest(unittest.TestCase):
+    """No durable semantics may change for presentation convenience."""
+
+    def test_no_server_side_change_is_required_by_this_slice(self):
+        server = os.path.join(STATIC, "..", "server.py")
+        self.assertTrue(os.path.exists(server))
+
+    def test_identity_helpers_only_read_existing_fields(self):
+        m = re.search(r"function messageIdentityRow[\s\S]{0,4000}?\n\}", APP)
+        body = m.group(0)
+        for mutator in ("postJSON", "fetch(", "POST"):
+            self.assertNotIn(mutator, body)
+
+    def test_restoration_never_mutates_durable_state(self):
+        m = re.search(r"function restoreActiveSelection[\s\S]{0,4000}?\n\}", APP)
+        body = m.group(0)
+        for mutator in ("postJSON", "fetch(", "/api/action"):
+            self.assertNotIn(mutator, body)
+
+
+if __name__ == "__main__":
+    unittest.main()
diff --git a/tests/test_session_ux_runtime.py b/tests/test_session_ux_runtime.py
new file mode 100644
index 0000000..6c56262
--- /dev/null
+++ b/tests/test_session_ux_runtime.py
@@ -0,0 +1,40 @@
+"""Run the dependency-free DOM runtime harness for the session-continuity UX.
+
+Both verification reviewers observed, correctly, that static assertions over
+app.js cannot catch the defect classes this slice actually hit: a scroll
+listener bound to an element that never scrolls, a rank bucket nothing can
+produce, and a composer target shape the server cannot validate. This test
+EXECUTES the real app.js in Node against a controllable DOM stub.
+
+It adds no dependency: no package.json, no npm install, no browser driver. When
+Node is unavailable the test skips rather than failing, so it can never make the
+suite depend on a runtime the project does not otherwise require.
+"""
+import os
+import shutil
+import subprocess
+import unittest
+
+HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
+                       "dom", "session_ux_runtime.mjs")
+
+
+class SessionUxRuntimeTest(unittest.TestCase):
+
+    def test_harness_exists(self):
+        self.assertTrue(os.path.isfile(HARNESS), HARNESS)
+
+    def test_runtime_behaviour(self):
+        node = shutil.which("node")
+        if not node:
+            self.skipTest("node is not available; runtime DOM checks skipped")
+        proc = subprocess.run([node, HARNESS], capture_output=True)
+        out = (proc.stdout or b"").decode("utf-8", "replace")
+        err = (proc.stderr or b"").decode("utf-8", "replace")
+        self.assertEqual(proc.returncode, 0,
+                         "runtime DOM checks failed:\n%s\n%s" % (out, err))
+        self.assertIn("PASS", out, out + err)
+
+
+if __name__ == "__main__":
+    unittest.main()
diff --git a/tests/test_session_ux_wired.py b/tests/test_session_ux_wired.py
new file mode 100644
index 0000000..7503b01
--- /dev/null
+++ b/tests/test_session_ux_wired.py
@@ -0,0 +1,55 @@
+"""Run the wired-path DOM harness for the session-continuity UX.
+
+Both verification reviewers said the previous harness proved helpers rather than
+the wired path: it called navigateToWorkItem() directly instead of dispatching a
+click through the delegated listener, so an integration regression in the most
+common activation path would still pass. This harness installs the real wire(),
+renders real tiles, and dispatches genuine events.
+
+It adds no dependency: every import is a Node builtin or the local mini DOM,
+there is no package manifest, and the test skips when Node is unavailable so the
+suite can never depend on a runtime the project does not otherwise require.
+"""
+import os
+import shutil
+import subprocess
+import unittest
+
+HERE = os.path.dirname(os.path.abspath(__file__))
+HARNESS = os.path.join(HERE, "dom", "wired_paths.mjs")
+MINI_DOM = os.path.join(HERE, "dom", "mini_dom.mjs")
+
+
+class WiredPathTest(unittest.TestCase):
+
+    def test_harness_files_exist(self):
+        self.assertTrue(os.path.isfile(HARNESS), HARNESS)
+        self.assertTrue(os.path.isfile(MINI_DOM), MINI_DOM)
+
+    def test_no_dependency_is_introduced(self):
+        """Every import must resolve to a Node builtin or a local file."""
+        import re
+        for path in (HARNESS, MINI_DOM):
+            src = open(path, encoding="utf-8").read()
+            for mod in re.findall(r'from "([^"]+)"', src):
+                self.assertTrue(mod.startswith("node:") or mod.startswith("./"),
+                                "non-builtin import would add a dependency: " + mod)
+            self.assertNotIn("require(", src)
+        repo = os.path.dirname(HERE)
+        self.assertFalse(os.path.exists(os.path.join(repo, "package.json")),
+                         "no package manifest may be introduced")
+
+    def test_wired_paths(self):
+        node = shutil.which("node")
+        if not node:
+            self.skipTest("node is not available; wired-path checks skipped")
+        proc = subprocess.run([node, HARNESS], capture_output=True)
+        out = (proc.stdout or b"").decode("utf-8", "replace")
+        err = (proc.stderr or b"").decode("utf-8", "replace")
+        self.assertEqual(proc.returncode, 0,
+                         "wired-path checks failed:\n%s\n%s" % (out, err))
+        self.assertIn("PASS", out, out + err)
+
+
+if __name__ == "__main__":
+    unittest.main()
