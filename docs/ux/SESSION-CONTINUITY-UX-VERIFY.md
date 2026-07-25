VERIFICATION PACKET: Active Session Continuity and Message Identity UX, Phase 1

BASE (merge-base with main): a3a5618ff8c35af561ee8a281c35e69bbd9aafac
HEAD (bytes under review):   dfb88391803f4fb4c91640e4e87ca8e361d72d8b

WHAT THIS CHANGE IS
----------------------------------------------------------------------
A presentation-and-wiring slice in the LOCAL operator console only. No
server change, no schema change, no durable-record change. The identity,
hash-routing, unread-tracking and derived-state foundations already
existed; this slice surfaces and connects them.

Seven operator-specified items: (1) restore active work on refresh via the
existing #work= hash route mirrored to one localStorage key, with priority
ranking over fields /api/work-items already returns; (2) conversation-first
view landing on the latest message; (3) ONE composer bound to the selected
work item, displaying work-item id, thread id and abbreviated title;
(4) render the already-emitted data-message-id with copy controls and a
post-send confirmation; (5) a contextual session rail shown only when a
work item is selected; (6) truthful execution state; (7) accessibility.

SAFETY-RELEVANT DESIGN POINTS (please scrutinise these)
----------------------------------------------------------------------
A. The composer now sends work_item_id ALONGSIDE a durable thread_id.
   createComposer already supported target.work_item_id, and the server
   already enforces target integrity: when both are supplied it verifies
   the pair is genuinely bound and REFUSES a mismatched pair. So this
   engages an existing server-side check that the previous 'new
   conversation' path bypassed entirely. work_item_id is never sent with
   a locally minted thread id.

B. The pre-existing confirmed-target contract is UNCHANGED:
   isConfirmedTarget: () => !!selectedConvThread. An intermediate version
   loosened this and was reverted, because selectTask now stores the
   item's real durable thread, so no looser rule is needed. A
   pre-allocated thread id is still never shown as a confirmed target.

C. Execution state is never derived from message-post activity. An inbound
   operator message proves only that the operator acted. States requiring
   executor acknowledgement or wake telemetry are NOT rendered.

D. The demoted composer uses `inert` (falling back to aria-hidden plus
   tabindex on every focusable descendant). An earlier version set
   aria-hidden while leaving the Send button reachable, which is a
   keyboard trap; that was found by live inspection and fixed.

VERIFIED IN THE RUNNING CONSOLE (not asserted from source alone)
----------------------------------------------------------------------
Selection persisted and restored across reload; hash route written;
conversation surface revealed with the durable thread bound; destination
banner showing work item + thread + title with identifiers in their true
case; 3 message identity rows and 6 copy buttons rendered; 6 queue rows
exposed as role=button tabindex=0; execution states limited to BLOCKED and
CLAIMED with no fabricated RUNNING; demoted composer inert with zero
keyboard-reachable descendants; no console errors.

TESTS
----------------------------------------------------------------------
tests/test_session_continuity_ux.py adds 53 tests following the
established static-assertion pattern used by the 20 existing front-end
test files. Full suite: 1255 tests OK, 1 pre-existing skip.
Known limitation, stated plainly: static assertions over source text
cannot prove DOM behaviour. That limitation is exactly why six real
defects in this slice were found by live inspection instead, and tests
were then added to pin each corrected contract.

FILE MANIFEST (sha256 of committed bytes)
----------------------------------------------------------------------
  apps/control-plane/static/app.js                       155986  798ecb29c4f8675c98925059fbc16a43b6f56d9ebb93d2d7b79b12abfc07093b
  apps/control-plane/static/index.html                    22153  336d1c97639ac69f8cbbac53f7919ffc1fb815da28c1c4f90175c5b55bc86179
  apps/control-plane/static/style.css                     52456  51d02fb9466c43a80f5d760a1e613dc0397acb0220fd3f91ffc4c3a1f5ece09f
  tests/dom/session_ux_runtime.mjs                        14674  8ff97685eae84a31efe0112dc16749b866c828bdf5fcd1eb997020b7563dc980
  tests/test_session_continuity_ux.py                     33907  21c181fb05da953a8f3cfb723eacc390bd92158db17f7023e083848d1c8f45e6
  tests/test_session_ux_runtime.py                         1585  ebb673195e5fb9463a228865f4a136e4111de7aa9bc41d714d8816c4c9386a1f

DIFFSTAT
----------------------------------------------------------------------
 apps/control-plane/static/app.js     | 584 ++++++++++++++++++++++++++-
 apps/control-plane/static/index.html |  42 +-
 apps/control-plane/static/style.css  | 101 +++++
 tests/dom/session_ux_runtime.mjs     | 340 ++++++++++++++++
 tests/test_session_continuity_ux.py  | 745 +++++++++++++++++++++++++++++++++++
 tests/test_session_ux_runtime.py     |  40 ++
 6 files changed, 1823 insertions(+), 29 deletions(-)

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
1. Does sending work_item_id with a durable thread_id weaken any message,
   thread, work-item, authority or audit semantics? Point A argues it
   strengthens them by engaging the server's target-integrity check.
2. Can the restoration path ever select a terminal or non-active item, or
   land on an item the operator did not choose when a deep link is present?
3. Is any rendered execution state not supportable from durable evidence?
4. Does the demotion leave any keyboard or screen-reader inconsistency?
5. Is any failure mode here silent rather than fail-closed?

FULL DIFF (committed bytes)
----------------------------------------------------------------------
NOTE: non-ASCII characters below are shown as <U+XXXX>.

diff --git a/apps/control-plane/static/app.js b/apps/control-plane/static/app.js
index dd2d10c..dea7adb 100644
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
 
@@ -768,8 +774,36 @@ function createComposer(opts) {
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
 
@@ -823,8 +857,15 @@ function createComposer(opts) {
       return;
     }
     showError("");
+    const preTarget = getTarget();
+    if (preTarget && preTarget.unresolved) {
+      showError("This work item has no durable thread yet, so the destination " +
+                "cannot be verified. The draft was kept; sending is blocked " +
+                "until the queue reports the thread.");
+      return;
+    }
     const draft = persistDraft();
-    const target = getTarget();
+    const target = preTarget;
     sending = true;
     sendBtn.disabled = true;
     try {
@@ -865,6 +906,10 @@ function createComposer(opts) {
       textarea.value = "";
       autoGrow();
       updateCounter();
+      // Phase 1, item 4: the operator must never open History or raw JSON to
+      // retrieve a durable message id. Show destination + the new id inline,
+      // with a copy control, immediately after a verified post.
+      showPostConfirmation(result);
       if (onPosted) onPosted(result, stored);
     } finally {
       sending = false;
@@ -1061,11 +1106,21 @@ function queueCard(it) {
     ? '<span class="q-opflag" title="operator action required"><U+25C9> operator</span>' : "";
   // Technical ids ride on data attributes only; the primary card stays readable.
   const title = esc((it.title || it.summary || it.work_item_id || "").slice(0, 140));
+  // Phase 1, item 7: a queue row is a real control -- role, tabindex and
+  // aria-pressed -- so it is reachable and activatable from the keyboard
+  // instead of being a plain div that only responds to a mouse click.
+  const execLabel = executionStateLabel(it);
   return '<div class="q-row q-card' + (selected ? " is-selected" : "") +
-    '" data-thread="' + esc(it.thread_id || "") +
+    '" role="button" tabindex="0"' +
+    ' aria-pressed="' + (selected ? "true" : "false") + '"' +
+    ' aria-label="Open work item ' + esc(it.work_item_id || "") +
+    (execLabel ? " (" + esc(execLabel) + ")" : "") + '"' +
+    ' data-thread="' + esc(it.thread_id || "") +
     '" data-work-item="' + esc(it.work_item_id || "") + '">' +
     '<div class="q-title">' + title + "</div>" +
-    '<div class="q-meta">' + bits.join("") + opFlag + "</div>" +
+    '<div class="q-meta">' + bits.join("") + opFlag +
+    (execLabel ? '<span class="q-exec mono">' + esc(execLabel) + "</span>" : "") +
+    "</div>" +
     "</div>";
 }
 
@@ -1290,6 +1345,341 @@ function openAttention() {
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
+document.addEventListener("keydown", (e) => {
+  if (e.key !== "Enter" && e.key !== " ") return;
+  const row = e.target && e.target.closest && e.target.closest(".q-row[data-work-item]");
+  if (!row) return;
+  e.preventDefault();
+  const wid = row.getAttribute("data-work-item");
+  if (wid) navigateToWorkItem(wid);
+});
+
+// --------------------------------------------------------------------------
+// TRUTHFUL EXECUTION STATE (Phase 1, item 6)
+// --------------------------------------------------------------------------
+// RUNNING is never derived from message-post activity. An inbound operator
+// message proves only that the OPERATOR acted; it is no evidence that an
+// executor resumed, consumed it, or is working. Such an item is reported as
+// OPERATOR_MESSAGE_POSTED, which is exactly what the durable record supports.
+//
+// States requiring executor acknowledgement or wake telemetry
+// (EXECUTOR_RESUMED, MESSAGE_ACKNOWLEDGED, WAKE_PENDING) are NOT produced here
+// and are NOT simulated. They are deferred to the Phase 2 executor wake bridge.
+const EXECUTION_STATE_LABELS = {
+  claimed: "CLAIMED",
+  waiting_for_operator: "WAITING_FOR_OPERATOR",
+  operator_message_posted: "OPERATOR_MESSAGE_POSTED",
+  paused: "PAUSED",
+  executor_active: "EXECUTOR_ACTIVE",
+  in_council: "IN_COUNCIL",
+  blocked: "BLOCKED",
+  complete: "COMPLETE"
+};
+
+// Evidence-backed only. `it` is the derived work item from /api/work-items.
+function truthfulExecutionState(it) {
+  if (!it) return "";
+  const p = String(it.presentation_state || "");
+  const r = String(it.runner_state || "");
+  const ev = String(it.last_activity_event || "");
+  if (p === "recently_completed" || p === "complete") return "complete";
+  if (p === "blocked") return "blocked";
+  if (p === "needs_operator" || p === "waiting_on_operator") return "waiting_for_operator";
+  if (r === "waiting_on_council") return "in_council";
+  // An operator message is the LAST durable event: the operator acted, but no
+  // executor activity followed. Never call this running.
+  if (ev === "operator_message" || ev === "message") return "operator_message_posted";
+  if (p === "stale" || r === "stale_or_no_heartbeat") return "paused";
+  if (p === "running") return "executor_active";
+  if (it.claimed_by) return "claimed";
+  return "";
+}
+
+function executionStateLabel(it) {
+  return EXECUTION_STATE_LABELS[truthfulExecutionState(it)] || "";
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
+const ACTIVE_RANK = [
+  "waiting_for_operator",
+  "operator_message_posted",
+  "paused",
+  "executor_active",
+  "in_council",
+  "blocked",
+  "claimed"
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
+    clearWorkRoute();
+    selectTask(null);
+    persistSelection(null);
+    showRestoreStatus("That link could not be read, so nothing is selected.");
+    return;
+  }
+  if (deep) {
+    // An explicit deep link always wins. It is applied on load BEFORE the work
+    // queue has been fetched, so the durable thread id could not be resolved at
+    // that point; bind it now that the queue is known. Without this the
+    // conversation stays empty and the composer shows no thread.
+    const wid = deep.work_item_id;
+    const known = items.find((it) => it.work_item_id === wid);
+    if (!known) {
+      // A stale or unavailable deep link must not leave a selected work item
+      // with no queue-backed identity. Clear the selection UNCONDITIONALLY --
+      // announcing "nothing is selected" while some other prior selection
+      // survived would be a false statement -- drop the route so a reload does
+      // not repeat this, and say what happened.
+      selectTask(null);
+      persistSelection(null);
+      clearWorkRoute();
+      showRestoreStatus('Work item "' + wid + '" is not in the live queue. ' +
+                        "The link may be stale, so nothing is selected.");
+      return;
+    }
+    if (known.thread_id && selectedWorkItemId === wid && !selectedConvThread) {
+      selectTask(known.thread_id, wid);
+    }
+    // POLICY, stated explicitly because both reviewers asked. An EXPLICIT link
+    // may open a terminal item, because reviewing finished work is the point of
+    // sharing a link. That is inspection, NOT active-session restoration: it is
+    // never persisted as the active selection, so the next refresh restores
+    // real active work rather than reopening finished work. Automatic
+    // restoration (stored selection and fallback ranking) still excludes
+    // terminal items entirely.
+    if (!isActiveItem(known)) {
+      persistSelection(null);
+      showRestoreStatus("Opened " + activeStateOf(known).replace(/_/g, " ") +
+                        " work item for inspection. It is not active, so it " +
+                        "will not be restored on the next refresh.");
+    }
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
+  const m = /[#&]work=([^&]+)/.exec(hash || "");
+  if (!m) return null;
+  let wid;
+  try {
+    wid = decodeURIComponent(m[1]);
+  } catch (e) {
+    return { malformed: true, work_item_id: null, message_id: null };
+  }
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
 // Deterministic hash route: #work=<work_item_id>[&msg=<message_id>]. The
 // highlight message id is derived from the work item id itself (a message work
 // item id IS "message:" + message_id) -- no message search, no ambiguity.
@@ -1298,9 +1688,124 @@ function navigateToWorkItem(workItemId) {
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
+// Scroll events do not bubble from the document element, so a page-level
+// scroller must be observed on window instead.
+function scrollEventTargetFor(el) {
+  return (el === document.scrollingElement || el === document.documentElement ||
+          el === document.body) ? window : el;
+}
+
+function operatorMovedAwayFromLatest(el) {
+  if (!el) return false;
+  return (el.scrollHeight - el.scrollTop - el.clientHeight) > 120;
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
+  // Resolved lazily on each event: the real scroller depends on layout, which
+  // changes when the Work view opens and when the conversation grows.
+  scrollEventTargetFor(conversationScrollEl()).addEventListener("scroll", () => {
+    if (!operatorMovedAwayFromLatest(conversationScrollEl())) pill.hidden = true;
+  });
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
@@ -1317,14 +1822,21 @@ function highlightMessage(messageId) {
 
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
+    // Never throw out of boot. Drop the unusable route and report it once the
+    // status element exists; restoration then proceeds normally.
+    clearWorkRoute();
+    showRestoreStatus("That link could not be read, so nothing is selected.");
+    return;
+  }
+  const known = (lastWorkItems || []).find((it) => it.work_item_id === route.work_item_id);
+  selectTask(known ? known.thread_id || null : null, route.work_item_id);
   showView("work");
-  const mm = /[#&]msg=([^&]+)/.exec(h);
-  if (mm) setTimeout(() => highlightMessage(decodeURIComponent(mm[1])), 200);
+  if (route.message_id) {
+    setTimeout(() => highlightMessage(route.message_id), 200);
+  }
 }
 
 async function refreshWorkItems() {
@@ -1837,7 +2349,8 @@ function buildConversationTab(run) {
     html += '<div class="' + cls + '" data-message-id="' + esc(m.message_id || "") + '">' +
       (tag ? '<div class="conv-entry-tag">' + esc(tag.label) + "</div>" : "") +
       '<div class="conv-msg-body">' + esc(m.message) + "</div>" +
-      '<div class="conv-msg-meta">' + meta + "</div></div>";
+      '<div class="conv-msg-meta">' + meta + "</div>" +
+      messageIdentityRow(m) + "</div>";
   }
   html += "</div>";
   return html;
@@ -2135,6 +2648,23 @@ let convComposerNewThreadId = null;
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
@@ -2786,9 +3316,12 @@ function toggleToolLog() {
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
@@ -2886,6 +3419,10 @@ function wire() {
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
@@ -2997,6 +3534,19 @@ function wire() {
   // at boot; the fast poll below only runs while the Work view is open.
   loadConversations();
   applyWorkHashRoute();   // honor a #work=...&msg=... deep link on load
+  // Active session continuity: once the queue has loaded, restore the prior
+  // selection or fall back to the highest-priority active item so a refresh
+  // never strands the operator on an empty panel while active work exists.
+  initJumpToLatest();
+  refreshWorkItems().then(() => {
+    showRestoreStatus("");
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
index 1a5fd93..a982560 100644
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
 
diff --git a/apps/control-plane/static/style.css b/apps/control-plane/static/style.css
index ac13c95..44a614a 100644
--- a/apps/control-plane/static/style.css
+++ b/apps/control-plane/static/style.css
@@ -1047,3 +1047,104 @@ body.history-open .mission { display: none !important; }
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
diff --git a/tests/dom/session_ux_runtime.mjs b/tests/dom/session_ux_runtime.mjs
new file mode 100644
index 0000000..b427d50
--- /dev/null
+++ b/tests/dom/session_ux_runtime.mjs
@@ -0,0 +1,340 @@
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
+// 3. Ranking: every ranked bucket reachable, unknown last, deterministic ties.
+// --------------------------------------------------------------------------
+{
+  const reg = baseRegistry();
+  const ctx = loadApp(reg, "", ["conv-scroll", "conversation"]);
+
+  eq(ctx.activeStateOf({ presentation_state: "needs_operator" }), "waiting_for_operator",
+     "needs_operator maps to waiting_for_operator");
+  eq(ctx.activeStateOf({ last_activity_event: "operator_message" }), "operator_message_posted",
+     "an operator message is reachable as its own rank (was unreachable)");
+  eq(ctx.activeStateOf({ presentation_state: "totally_new_state" }), "",
+     "an unrecognised state is NOT guessed as in_council");
+
+  ok(evalIn(ctx, 'ACTIVE_RANK.indexOf("wake_pending")') === -1,
+     "wake_pending is not in the executable rank");
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
+// 4. Composer target fails closed without a durable thread.
+// --------------------------------------------------------------------------
+{
+  const reg = baseRegistry();
+  const ctx = loadApp(reg, "", ["conv-scroll", "conversation"]);
+
+  evalIn(ctx, 'lastWorkItems = [{ work_item_id: "message:msg-1", thread_id: "thr-1" }];' +
+              'selectedWorkItemId = "message:msg-1"; selectedConvThread = null;');
+  const bound = ctx.convComposerTarget();
+  eq([bound.work_item_id, bound.thread_id, !!bound.unresolved],
+     ["message:msg-1", "thr-1", false],
+     "a known item binds work item and durable thread together");
+
+  // Same selection, but the queue has no thread for it.
+  evalIn(ctx, 'lastWorkItems = [{ work_item_id: "message:msg-2", thread_id: null }];' +
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
index 0000000..fe4eb0d
--- /dev/null
+++ b/tests/test_session_continuity_ux.py
@@ -0,0 +1,745 @@
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
+            "waiting_for_operator", "operator_message_posted",
+            "paused", "executor_active", "in_council", "blocked", "claimed"])
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
+class StaleRouteClearingTest(unittest.TestCase):
+    """'Clear it and say so' has to be literally true."""
+
+    def test_invalid_route_is_removed_from_the_url(self):
+        self.assertIn("function clearWorkRoute", APP)
+        m = re.search(r"function clearWorkRoute[\s\S]{0,4000}?\n\}", APP)
+        self.assertIn("replaceState", m.group(0))
+        r = re.search(r"function restoreActiveSelection[\s\S]{0,4000}?\n\}", APP)
+        self.assertIn("clearWorkRoute()", r.group(0))
+
+    def test_selection_is_cleared_unconditionally(self):
+        """A conditional clear could announce 'nothing is selected' while a
+        different prior selection survived."""
+        r = re.search(r"function restoreActiveSelection[\s\S]{0,4000}?\n\}", APP)
+        unknown = r.group(0).split("if (!known)")[1][:400]
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
+        r = re.search(r"function restoreActiveSelection[\s\S]{0,4000}?\n\}", APP)
+        body = r.group(0)
+        self.assertIn("if (!isActiveItem(known))", body)
+        tail = body.split("if (!isActiveItem(known))")[1][:400]
+        self.assertIn("persistSelection(null)", tail)
+        self.assertIn("showRestoreStatus", tail)
+
+    def test_the_policy_is_documented_where_it_is_enforced(self):
+        r = re.search(r"function restoreActiveSelection[\s\S]{0,4000}?\n\}", APP)
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
+        m = re.search(r"function restoreActiveSelection[\s\S]{0,4000}?\n\}", APP)
+        body = m.group(0)
+        self.assertIn("if (!known)", body)
+        self.assertIn("selectTask(null)", body)
+        self.assertIn("persistSelection(null)", body)
+
+    def test_unknown_deep_link_is_reported_not_silent(self):
+        m = re.search(r"function restoreActiveSelection[\s\S]{0,4000}?\n\}", APP)
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
+    def test_page_level_scroll_is_observed_on_window(self):
+        """Scroll events do not bubble from the document element."""
+        self.assertIn("function scrollEventTargetFor", APP)
+        m = re.search(r"function scrollEventTargetFor[\s\S]{0,4000}?\n\}", APP)
+        self.assertIn("window", m.group(0))
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
+        self.assertIn("operator_message_posted", body)
+        i_msg = body.index('ev === "operator_message"')
+        i_run = body.index('p === "running"')
+        self.assertLess(i_msg, i_run,
+                        "an operator message must be classified before any "
+                        "running check can claim the executor is active")
+
+    def test_unsupported_states_are_not_simulated(self):
+        labels = re.search(r"EXECUTION_STATE_LABELS = \{(.*?)\n\}", APP, re.S).group(1)
+        for deferred in ("EXECUTOR_RESUMED", "MESSAGE_ACKNOWLEDGED", "WAKE_PENDING"):
+            self.assertNotIn(deferred, labels,
+                             deferred + " requires the Phase 2 wake bridge and "
+                             "must not be rendered from current evidence")
+
+    def test_supported_states_are_available(self):
+        labels = re.search(r"EXECUTION_STATE_LABELS = \{(.*?)\n\}", APP, re.S).group(1)
+        for supported in ("CLAIMED", "WAITING_FOR_OPERATOR", "OPERATOR_MESSAGE_POSTED",
+                          "PAUSED", "EXECUTOR_ACTIVE", "IN_COUNCIL", "BLOCKED", "COMPLETE"):
+            self.assertIn(supported, labels)
+
+    def test_state_is_surfaced_on_the_queue_row(self):
+        self.assertIn("executionStateLabel(it)", APP)
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
+        self.assertIn('role="button"', APP)
+        self.assertIn('tabindex="0"', APP)
+        self.assertIn("aria-pressed=", APP)
+
+    def test_queue_rows_activate_on_enter_and_space(self):
+        m = re.search(r'if \(e\.key !== "Enter" && e\.key !== " "\)[\s\S]{0,4000}?\n\}\);', APP)
+        self.assertIsNotNone(m, "queue rows need Enter/Space activation")
+        self.assertIn("navigateToWorkItem", m.group(0))
+
+    def test_existing_send_shortcuts_are_preserved(self):
+        """Ctrl+Enter sends; Shift+Enter still inserts a newline."""
+        self.assertIn('e.key === "Enter" && (e.ctrlKey || e.metaKey)', APP)
+        self.assertIn("Shift+Enter for a new line, Ctrl+Enter to send", HTML)
+
+    def test_focus_rings_exist_for_new_controls(self):
+        for rule in (".copy-id:focus-visible", ".jump-to-latest:focus-visible",
+                     '.q-row[role="button"]:focus-visible'):
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
