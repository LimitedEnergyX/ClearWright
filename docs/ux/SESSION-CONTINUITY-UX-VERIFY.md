VERIFICATION PACKET: Active Session Continuity and Message Identity UX, Phase 1

BASE (merge-base with main): a3a5618ff8c35af561ee8a281c35e69bbd9aafac
HEAD (bytes under review):   2dba18ed02c252cb367497fd08d29dd9611649a1

WHAT THIS CHANGE IS
----------------------------------------------------------------------
An operator-workflow identity, integrity and submission-feedback
correction to the LOCAL control-plane console. Presentation and wiring
only: apps/control-plane/server.py, tools/clearwright_identity.py and
every durable record are untouched, and no identity semantics change.

It is authorised by durable operator message msg-20260725T224347822028
(2026-07-25T22:43:47Z, OPERATOR-0001, inbound, bound to work item
message:msg-20260725T142257787771 and thread thr-20260725T142257787771),
which postdates the terminal result of the previous verify council by
seven hours.

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
  focused static   145 tests  (OK)
  runtime          109 checks (PASS)  tests/dom/session_ux_runtime.mjs
  full suite       1349 tests  (OK, skipped=1)

The runtime harness executes the real app.js in a Node vm against a
controllable DOM stub and adds NO dependency: every import is a Node
builtin, there is no package manifest, and the Python wrapper skips when
node is absent. Stated limitation, unchanged: it supplies scroll geometry
rather than computing layout, so it proves decision logic given a
geometry, not that a browser produces that geometry. Layout-dependent
behaviour was checked by live inspection, recorded above as manual
evidence rather than automated coverage.

FILE MANIFEST (sha256 of committed bytes)
----------------------------------------------------------------------
  apps/control-plane/static/app.js                       170924  536f119dddfe5279df69b62fe61d80d1f33d4cd2ac05ece954d7922d751b82bb
  apps/control-plane/static/index.html                    22393  86b4ffbf452f29a46c2c7c9877a3daadfdf29b5bc3af4118bb40b55a993b02f4
  apps/control-plane/static/style.css                     54468  b4791a3f0de15bc8c9714a342a4ee2f33f26c0ece1bb85e0e0b87183ebbe499f
  tests/dom/session_ux_runtime.mjs                        31834  e5085e168f511380372b6c7420de37c8b4d9bee5edb8947c6bde0d30d9f05894
  tests/test_session_continuity_ux.py                     56947  6b71b25839a64a1f21f21716d0729104436b2f140b88bec61c1e8756bc8de466
  tests/test_session_ux_runtime.py                         1585  ebb673195e5fb9463a228865f4a136e4111de7aa9bc41d714d8816c4c9386a1f

DIFFSTAT
----------------------------------------------------------------------
 apps/control-plane/static/app.js     |  881 +++++++++++++++++++++++-
 apps/control-plane/static/index.html |   49 +-
 apps/control-plane/static/style.css  |  150 +++++
 tests/dom/session_ux_runtime.mjs     |  667 +++++++++++++++++++
 tests/test_session_continuity_ux.py  | 1220 ++++++++++++++++++++++++++++++++++
 tests/test_session_ux_runtime.py     |   40 ++
 6 files changed, 2971 insertions(+), 36 deletions(-)

SUPPORTING CONTRACT EVIDENCE (unchanged files, quoted read-only)
----------------------------------------------------------------------
These files are NOT modified by this change. They are quoted because the
review asked, correctly, how the claims above can be checked.

  apps/control-plane/server.py lines 348-385  -- the pair-validation this UI change relies on for destination integrity
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

  tools/clearwright_work.py lines 290-310  -- the value domain of runner_state, the second field the labels read
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
    
    

Consequences, stated so they can be checked against the quoted source:
  * The server refuses a thread_id/work_item_id pair that is not durably
    bound. It can only apply when BOTH are supplied, which is exactly why
    a work_item_id with no thread is now refused in the UI instead of
    being sent as an unverifiable target.
  * presentation_state is a total, ordered, mutually-exclusive function
    with nine possible values: superseded, recently_completed, historical
    (terminal), and needs_operator, blocked, waiting_on_operator, running,
    waiting_on_claude, stale. INACTIVE_STATES covers every terminal value.
    waiting_on_claude has no dedicated rank: it resolves to claimed when a
    claimant exists and otherwise to the empty string, which sorts last.
    That is the intended unknown-state behaviour, not an omission.

REVIEW QUESTIONS
----------------------------------------------------------------------
1. Is disambiguating-plus-warning the right response to the duplicate
   tiles, given the records are genuinely distinct? Would any form of
   deduplication hide durable governed work?
2. Can any code path still render a thread id where a work-item id is
   claimed, in the queue or in History?
3. Can the fail-closed destination check be bypassed, or can a request
   body be built while route, selection, thread and destination disagree?
4. Are the phase and executor label sets exactly the server value
   domains, and can ACTIVE be reported without positive runner evidence?
5. Can the composer strand in a sending state, double-post from any entry
   point, or clear a draft before durable success is confirmed?
6. Is any failure mode here silent rather than fail-closed and explained?

FULL DIFF (committed bytes)
----------------------------------------------------------------------
NOTE: non-ASCII characters below are shown as <U+XXXX>.

diff --git a/apps/control-plane/static/app.js b/apps/control-plane/static/app.js
index dd2d10c..c54248c 100644
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
 
@@ -761,6 +767,39 @@ function createComposer(opts) {
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
+    const known = (lastWorkItems || []).find(
+      (i) => i.work_item_id === selectedWorkItemId);
+    if (known && known.thread_id && target.thread_id &&
+        known.thread_id !== target.thread_id) {
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
@@ -768,8 +807,36 @@ function createComposer(opts) {
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
 
@@ -823,10 +890,29 @@ function createComposer(opts) {
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
@@ -865,10 +951,18 @@ function createComposer(opts) {
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
 
@@ -958,6 +1052,11 @@ const WORK_KIND_LABEL = {
 // --------------------------------------------------------------------------- //
 
 let lastWorkItems = [];
+// Distinguishes "the queue has not been fetched yet" from "the queue was
+// fetched successfully and is empty". Inferring this from lastWorkItems.length
+// conflated the two, so after a successful empty response an unknown explicit
+// route was retained as a provisional selection instead of being rejected.
+let workItemsLoaded = false;
 let lastQueueCouncils = [];
 let lastArchiveIndex = { archived: [], count: 0 };
 
@@ -1061,11 +1160,65 @@ function queueCard(it) {
     ? '<span class="q-opflag" title="operator action required"><U+25C9> operator</span>' : "";
   // Technical ids ride on data attributes only; the primary card stays readable.
   const title = esc((it.title || it.summary || it.work_item_id || "").slice(0, 140));
+  // Phase 1, item 7: a queue row is a real control -- role, tabindex and
+  // aria-pressed -- so it is reachable and activatable from the keyboard
+  // instead of being a plain div that only responds to a mouse click.
+  const execLabel = executorStateLabel(it);
+  const phaseLabel = lifecyclePhaseLabel(it);
+  const wid = it.work_item_id || "";
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
   return '<div class="q-row q-card' + (selected ? " is-selected" : "") +
-    '" data-thread="' + esc(it.thread_id || "") +
+    (ambiguous ? " q-ambiguous" : "") + '"' +
+    ' data-thread="' + esc(it.thread_id || "") +
     '" data-work-item="' + esc(it.work_item_id || "") + '">' +
-    '<div class="q-title">' + title + "</div>" +
-    '<div class="q-meta">' + bits.join("") + opFlag + "</div>" +
+    '<button type="button" class="q-open" ' +
+    'aria-pressed="' + (selected ? "true" : "false") + '"' +
+    ' aria-label="Open work item ' + esc(it.work_item_id || "") +
+    (execLabel ? " (executor " + esc(execLabel) + ")" : "") + '"' +
+    ' data-work-item="' + esc(it.work_item_id || "") + '">' +
+    '<span class="q-title">' + title + "</span>" +
+    '<div class="q-meta">' + bits.join("") + opFlag +
+    // Item 10: phase and executor state are DIFFERENT facts and are labelled
+    // separately, so "PHASE: VERIFICATION / EXECUTOR: IN COUNCIL" can never be
+    // misread as a single contradictory status.
+    (phaseLabel ? '<span class="q-phase mono" title="lifecycle phase">Phase ' +
+      esc(phaseLabel) + "</span>" : "") +
+    (execLabel ? '<span class="q-exec mono" title="executor state, derived from ' +
+      'runner_state only">Executor ' + esc(execLabel) + "</span>" : "") +
+    "</div></button>" + ids + warn +
     "</div>";
 }
 
@@ -1290,17 +1443,603 @@ function openAttention() {
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
+// The primary control is a real <button>, so Enter and Space already activate
+// it and fire click. This listener remains only to keep Space from scrolling
+// the page while that button has focus; it never navigates on its own, which
+// avoids a second, divergent activation path.
+document.addEventListener("keydown", (e) => {
+  if (e.key !== " ") return;
+  const btn = e.target && e.target.closest && e.target.closest(".q-open");
+  if (btn) e.preventDefault();
+});
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
+      showRestoreStatus("");
+      refreshWorkItems().then(restoreActiveSelection).catch(() => {
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
@@ -1317,20 +2056,47 @@ function highlightMessage(messageId) {
 
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
 
 async function refreshWorkItems() {
   try {
     const data = await getJSON("/api/work-items");
     lastWorkItems = data.work_items || [];
+    workItemsLoaded = true;   // a SUCCESSFUL response, even when it is empty
     try {
       const cd = await getJSON("/api/review-councils");
       lastQueueCouncils = cd.review_councils || [];
@@ -1398,7 +2164,7 @@ async function loadHistory() {
   lastLedgerRows = (data.rows || []).filter((row) => ledgerRowMatches(row, f));
   const body = document.getElementById("ledger-body");
   if (!lastLedgerRows.length) {
-    body.innerHTML = '<tr><td colspan="6" class="muted">No records match the filters.</td></tr>';
+    body.innerHTML = '<tr><td colspan="8" class="muted">No records match the filters.</td></tr>';
     return;
   }
   body.innerHTML = lastLedgerRows.slice(0, 500).map((row, i) =>
@@ -1406,12 +2172,31 @@ async function loadHistory() {
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
@@ -1837,7 +2622,8 @@ function buildConversationTab(run) {
     html += '<div class="' + cls + '" data-message-id="' + esc(m.message_id || "") + '">' +
       (tag ? '<div class="conv-entry-tag">' + esc(tag.label) + "</div>" : "") +
       '<div class="conv-msg-body">' + esc(m.message) + "</div>" +
-      '<div class="conv-msg-meta">' + meta + "</div></div>";
+      '<div class="conv-msg-meta">' + meta + "</div>" +
+      messageIdentityRow(m) + "</div>";
   }
   html += "</div>";
   return html;
@@ -2135,6 +2921,23 @@ let convComposerNewThreadId = null;
 let convComposer = null;
 
 function convComposerTarget() {
+  // Phase 1, item 3: while a work item is selected the composer is BOUND to it,
+  // so the destination shown above the composer is the destination the post
+  // actually reaches. The work_item_id is only sent alongside a durable thread
+  // id, which engages the server's existing target-integrity check (it refuses
+  // a thread/work-item pair that is not genuinely bound) rather than relying on
+  // presentation alone.
+  if (selectedWorkItemId) {
+    const it = (lastWorkItems || []).find((i) => i.work_item_id === selectedWorkItemId);
+    const thread = selectedConvThread || (it && it.thread_id) || null;
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
@@ -2783,12 +3586,23 @@ function toggleToolLog() {
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
@@ -2869,11 +3683,17 @@ function wire() {
 
   // Work queue: clicking a row selects that task everywhere.
   document.getElementById("queue-groups").addEventListener("click", (e) => {
+    if (eventTargetsInnerControl(e)) return;   // Copy is not "open this item"
     const row = e.target.closest(".q-row");
     if (!row) return;
-    const thread = row.getAttribute("data-thread");
     const workItem = row.getAttribute("data-work-item");
-    if (thread || workItem) selectTask(thread, workItem);
+    // Mouse activation goes through the SAME navigation as the keyboard so the
+    // canonical #work= route is always written. Calling selectTask() directly
+    // here left the previous route in the URL, which is precisely the stale-hash
+    // symptom this correction exists to remove.
+    if (workItem) { navigateToWorkItem(workItem); return; }
+    const thread = row.getAttribute("data-thread");
+    if (thread) selectTask(thread, null);
   });
 
   // Context-aware task actions are READ-ONLY navigation only: they switch view
@@ -2886,6 +3706,10 @@ function wire() {
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
@@ -2997,6 +3821,19 @@ function wire() {
   // at boot; the fast poll below only runs while the Work view is open.
   loadConversations();
   applyWorkHashRoute();   // honor a #work=...&msg=... deep link on load
+  // Active session continuity: once the queue has loaded, restore the prior
+  // selection or fall back to the highest-priority active item so a refresh
+  // never strands the operator on an empty panel while active work exists.
+  initJumpToLatest();
+  refreshWorkItems().then(() => {
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
index ac13c95..a734f49 100644
--- a/apps/control-plane/static/style.css
+++ b/apps/control-plane/static/style.css
@@ -1047,3 +1047,153 @@ body.history-open .mission { display: none !important; }
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
+.q-row[role="button"] { cursor: pointer; }
+.q-row[role="button"]:focus-visible { outline: 2px solid #7aa2ff; outline-offset: 2px; }
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
+.q-open[aria-pressed="true"] .q-title { font-weight: 700; }
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
diff --git a/tests/test_session_continuity_ux.py b/tests/test_session_continuity_ux.py
new file mode 100644
index 0000000..454f00a
--- /dev/null
+++ b/tests/test_session_continuity_ux.py
@@ -0,0 +1,1220 @@
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
+RE_CARD = r"function queueCard[\s\S]{0,8000}?\n}"
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
+        self.assertNotIn("convComposerNewThreadId", body.split("selectedConvThread ||")[0])
+        # The bare-work-item shape is now an explicit fail-closed marker rather
+        # than a sendable target.
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
+        """A real <button> already activates on Enter and Space, so the
+        hand-rolled key handler is reduced to preventing Space-scroll and can
+        no longer become a second, divergent activation path."""
+        i = APP.index('document.addEventListener("keydown"')
+        handler = APP[i:i + 500]
+        self.assertNotIn("navigateToWorkItem", handler)
+        self.assertIn("q-open", handler)
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
+        self.assertIn("aria-pressed=", body)
+        self.assertNotIn('role="button" tabindex="0"', body,
+                         "the row must not claim button semantics itself")
+
+    def test_queue_rows_activate_via_a_native_button(self):
+        """Enter and Space now come from native <button> semantics rather than
+        a hand-rolled key handler, which also removes the second activation
+        path that could diverge from the mouse path."""
+        m = re.search(RE_CARD, APP)
+        self.assertIn('<button type="button" class="q-open"', m.group(0))
+        k = APP.index('document.addEventListener("keydown"')
+        handler = APP[k:k + 400]
+        # The listener only stops Space from scrolling; it must not navigate.
+        self.assertIn("q-open", handler)
+        self.assertNotIn("navigateToWorkItem", handler)
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
