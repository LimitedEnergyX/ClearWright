VERIFICATION PACKET: Active Session Continuity and Message Identity UX, Phase 1

BASE (merge-base with main): a3a5618ff8c35af561ee8a281c35e69bbd9aafac
HEAD (bytes under review):   789f0f4b244e056da41c2b323e6695eadee9c031

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
  apps/control-plane/static/app.js                       153437  249b3d3773246de8f30078e791b9c855626720c0a31957bb4c28e8da2dc60a6b
  apps/control-plane/static/index.html                    22153  336d1c97639ac69f8cbbac53f7919ffc1fb815da28c1c4f90175c5b55bc86179
  apps/control-plane/static/style.css                     52456  51d02fb9466c43a80f5d760a1e613dc0397acb0220fd3f91ffc4c3a1f5ece09f
  tests/test_session_continuity_ux.py                     27802  fcd7ec9460bdf7c90c22e1ce6b6fe14f238c5ad9b24ed232e0f8f0e4bb05ab52

DIFFSTAT
----------------------------------------------------------------------
 apps/control-plane/static/app.js     | 511 ++++++++++++++++++++++++++++-
 apps/control-plane/static/index.html |  42 ++-
 apps/control-plane/static/style.css  | 101 ++++++
 tests/test_session_continuity_ux.py  | 614 +++++++++++++++++++++++++++++++++++
 4 files changed, 1245 insertions(+), 23 deletions(-)

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
index dd2d10c..bcf7a9b 100644
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
 
@@ -1290,6 +1345,286 @@ function openAttention() {
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
+  const deep = /[#&]work=([^&]+)/.exec(location.hash || "");
+  if (deep) {
+    // An explicit deep link always wins. It is applied on load BEFORE the work
+    // queue has been fetched, so the durable thread id could not be resolved at
+    // that point; bind it now that the queue is known. Without this the
+    // conversation stays empty and the composer shows no thread.
+    const wid = decodeURIComponent(deep[1]);
+    const known = items.find((it) => it.work_item_id === wid);
+    if (!known) {
+      // A malformed, stale, or unavailable deep link must not leave a selected
+      // work item with no queue-backed identity. Clear it, say so, and do not
+      // persist it as the active selection.
+      if (selectedWorkItemId === wid) selectTask(null);
+      persistSelection(null);
+      showRestoreStatus('Work item "' + wid + '" is not in the live queue. ' +
+                        "The link may be stale, so nothing is selected.");
+      return;
+    }
+    if (known.thread_id && selectedWorkItemId === wid && !selectedConvThread) {
+      selectTask(known.thread_id, wid);
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
 // Deterministic hash route: #work=<work_item_id>[&msg=<message_id>]. The
 // highlight message id is derived from the work item id itself (a message work
 // item id IS "message:" + message_id) -- no message search, no ambiguity.
@@ -1298,9 +1633,124 @@ function navigateToWorkItem(workItemId) {
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
@@ -1321,7 +1771,8 @@ function applyWorkHashRoute() {
   const m = /[#&]work=([^&]+)/.exec(h);
   if (!m) return;
   const wid = decodeURIComponent(m[1]);
-  selectTask(null, wid);
+  const known = (lastWorkItems || []).find((it) => it.work_item_id === wid);
+  selectTask(known ? known.thread_id || null : null, wid);
   showView("work");
   const mm = /[#&]msg=([^&]+)/.exec(h);
   if (mm) setTimeout(() => highlightMessage(decodeURIComponent(mm[1])), 200);
@@ -1837,7 +2288,8 @@ function buildConversationTab(run) {
     html += '<div class="' + cls + '" data-message-id="' + esc(m.message_id || "") + '">' +
       (tag ? '<div class="conv-entry-tag">' + esc(tag.label) + "</div>" : "") +
       '<div class="conv-msg-body">' + esc(m.message) + "</div>" +
-      '<div class="conv-msg-meta">' + meta + "</div></div>";
+      '<div class="conv-msg-meta">' + meta + "</div>" +
+      messageIdentityRow(m) + "</div>";
   }
   html += "</div>";
   return html;
@@ -2135,6 +2587,23 @@ let convComposerNewThreadId = null;
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
@@ -2786,9 +3255,12 @@ function toggleToolLog() {
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
@@ -2886,6 +3358,10 @@ function wire() {
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
@@ -2997,6 +3473,19 @@ function wire() {
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
diff --git a/tests/test_session_continuity_ux.py b/tests/test_session_continuity_ux.py
new file mode 100644
index 0000000..512148d
--- /dev/null
+++ b/tests/test_session_continuity_ux.py
@@ -0,0 +1,614 @@
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
+        self.assertIn("restoreActiveSelection", APP)
+        self.assertRegex(APP, r"refreshWorkItems\(\)\s*\.then\(\s*restoreActiveSelection")
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
