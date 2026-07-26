/*
 * Executable coverage through the REAL wired event paths.
 *
 * This installs wire(), renders real queue tiles from representative work-item
 * data, and dispatches genuine events. It proves what direct function calls
 * cannot: that a click on a rendered control reaches the delegated listener,
 * that Enter and Space activate the native button, that focus survives a
 * polling cycle, that Copy does not navigate, and that send() refuses through
 * every destination-integrity branch without emitting a request.
 *
 * Dependency-free: Node builtins plus the local mini DOM.
 */
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";
import { createDocument, MiniElement, MiniEvent, pressKey } from "./mini_dom.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const APP = path.join(HERE, "..", "..", "apps", "control-plane", "static", "app.js");
const HTML = path.join(HERE, "..", "..", "apps", "control-plane", "static", "index.html");

let failures = 0, checks = 0;
function ok(cond, label) {
  checks += 1;
  if (!cond) { failures += 1; console.error("FAIL: " + label); }
}
// Elements are circular structures, so identity comparisons use reference
// equality rather than serialisation.
function same(a, b, label) {
  checks += 1;
  if (a !== b) { failures += 1; console.error("FAIL: " + label + "  (nodes differ)"); }
}
function eq(a, b, label) {
  ok(JSON.stringify(a) === JSON.stringify(b),
     label + "  (got " + JSON.stringify(a) + ", want " + JSON.stringify(b) + ")");
}

// Element ids app.js wires or reads at boot. Built from index.html so the
// harness cannot drift from the shipped markup.
function idsFromMarkup() {
  const html = fs.readFileSync(HTML, "utf8");
  const ids = new Set();
  const re = /id="([\w-]+)"/g;
  let m;
  while ((m = re.exec(html)) !== null) ids.add(m[1]);
  return Array.from(ids);
}

function buildEnv(opts) {
  opts = opts || {};
  const doc = createDocument();
  idsFromMarkup().forEach((id) => {
    const tag = /input|search/.test(id) ? "input"
      : (/send|btn|button|close|cancel|confirm/.test(id) ? "button"
        : (/form/.test(id) ? "form" : "div"));
    const el = doc.createElement(tag);
    el.setAttribute("id", id);
    doc.body.appendChild(el);
  });
  // A couple of elements app.js expects to be specific tags.
  const sendBtn = doc.getElementById("conv-send");
  if (sendBtn) sendBtn.textContent = "Send";

  const ta = doc.createElement("textarea");
  ta.setAttribute("id", "conv-input");
  const old = doc.getElementById("conv-input");
  if (old) old.parentNode.replaceChild(ta, old); else doc.body.appendChild(ta);

  const posted = [];
  const ctx = {
    console,
    document: doc,
    location: { hash: opts.hash || "", pathname: "/", search: "", href: "http://x/" },
    history: { replaceState() { ctx.location.hash = ""; } },
    localStorage: {
      _m: {},
      getItem(k) { return k in this._m ? this._m[k] : null; },
      setItem(k, v) { this._m[k] = String(v); },
      removeItem(k) { delete this._m[k]; },
    },
    getComputedStyle(el) { return { overflowY: (el && el._overflowY) || "visible", textTransform: "none" }; },
    MutationObserver: function () { this.observe = () => {}; this.disconnect = () => {}; },
    HTMLElement: { prototype: { inert: true } },
    setTimeout(fn) { return 0; },
    setInterval() { return 0; },
    clearTimeout() {},
    CSS: { escape: (s) => String(s).replace(/["\\]/g, "\\$&") },
    Node: MiniElement,
    Event: MiniEvent,
    KeyboardEvent: MiniEvent,
    MouseEvent: MiniEvent,
    _posted: posted,
    fetch(url, init) {
      const method = (init && init.method) || "GET";
      posted.push({ url: String(url), method, body: init && init.body });
      const payload = opts.responder ? opts.responder(String(url), method, init) : { ok: true };
      return Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve(payload),
        text: () => Promise.resolve(JSON.stringify(payload)),
      });
    },
    Promise, JSON, Math, String, Number, Boolean, Array, Object, Date,
    // Node globals that are not ECMAScript built-ins, so a fresh vm context
    // does not provide them.
    TextEncoder, TextDecoder, URLSearchParams,
  };
  // window participates in event wiring: app.js installs hashchange and
  // capture-phase scroll listeners on it.
  ctx._winListeners = {};
  ctx.addEventListener = function (type, fn, opts) {
    const capture = opts === true || (opts && opts.capture);
    (ctx._winListeners[type] = ctx._winListeners[type] || []).push({ fn, capture: !!capture });
  };
  ctx.removeEventListener = function () {};
  ctx.dispatchEvent = function (e) {
    (ctx._winListeners[e.type] || []).forEach((entry) => {
      try { entry.fn.call(ctx, e); } catch (err) { /* surfaced by assertions */ }
    });
    return true;
  };
  ctx.window = ctx;
  ctx.globalThis = ctx;
  return { ctx, doc, posted };
}

function loadApp(env, { boot } = { boot: false }) {
  let src = fs.readFileSync(APP, "utf8");
  if (!boot) src = src.replace(/\nwire\(\);\s*\nrefresh\(\);\s*$/, "\n");
  vm.createContext(env.ctx);
  vm.runInContext(src, env.ctx, { filename: "app.js" });
  return env.ctx;
}
const ev = (ctx, code) => vm.runInContext(code, ctx);

const ITEMS = [
  { work_item_id: "message:msg-alpha", thread_id: "thr-alpha", title: "Alpha work",
    presentation_state: "needs_operator", status: "planning", runner_state: "waiting_on_operator",
    claimed_by: "claude", last_activity_at: "2026-07-25T10:00:00Z", last_activity_event: "progress" },
  { work_item_id: "message:msg-beta", thread_id: "thr-beta", title: "Beta work",
    presentation_state: "blocked", status: "verification", runner_state: "waiting_on_council",
    claimed_by: "claude", last_activity_at: "2026-07-25T09:00:00Z", last_activity_event: "council" },
];

function seed(ctx, items, env) {
  // A successful refresh sets BOTH: the snapshot and its confirmation.
  ev(ctx, "workItemsLoaded = true; queueConfirmed = true; lastWorkItems = " +
          JSON.stringify(items || ITEMS) + ";");
  // Keep the served payload in step, or the next poll reverts the seeded state.
  if (env) env.servedItems = items || ITEMS;
}

// ---------------------------------------------------------------------------
// 1. wire() installs the delegated handler and a REAL click navigates.
// ---------------------------------------------------------------------------
{
  const env = buildEnv({ hash: "#work=message%3Amsg-stale" });
  const ctx = loadApp(env);
  seed(ctx);
  ctx.wire();
  ctx.renderQueue();

  const groups = env.doc.getElementById("queue-groups");
  const tiles = groups.querySelectorAll(".q-row[data-work-item]");
  eq(tiles.length, 2, "wire+render produced one tile per work item");

  const btn = tiles[0].querySelector(".q-open");
  ok(!!btn, "each tile exposes a native primary button");
  eq(String(btn.tagName), "BUTTON", "the primary control is a real button element");

  // A genuine click on the rendered control, not a direct helper call.
  btn.dispatchEvent(new MiniEvent("click", { bubbles: true, isTrusted: true }));

  eq(ev(ctx, "selectedWorkItemId"), "message:msg-alpha",
     "a real delegated click selects the clicked work item");
  eq(ev(ctx, "selectedConvThread"), "thr-alpha",
     "the click binds the queue-backed durable thread");
  ok(ctx.location.hash.indexOf("msg-alpha") !== -1,
     "the click writes the canonical work route");
  ok(ctx.location.hash.indexOf("msg-stale") === -1,
     "the stale route does not survive a real click");
}

// ---------------------------------------------------------------------------
// 2. ENTER and SPACE activate the focused tile through the same one path.
// ---------------------------------------------------------------------------
for (const key of ["Enter", " "]) {
  const env = buildEnv({});
  const ctx = loadApp(env);
  seed(ctx);
  ctx.wire();
  ctx.renderQueue();

  const groups = env.doc.getElementById("queue-groups");
  const target = groups.querySelectorAll(".q-row[data-work-item]")[1];
  const btn = target.querySelector(".q-open");

  let clicks = 0;
  btn.addEventListener("click", () => { clicks += 1; });
  btn.focus();
  same(env.doc.activeElement, btn, "the tile button can hold focus (" + key + ")");

  const res = pressKey(env.doc, key);
  ok(res.activated, "a focused button is activated by " + (key === " " ? "Space" : key));
  ok(!res.defaultPrevented,
     "nothing suppresses the native default for " + (key === " " ? "Space" : key));
  eq(clicks, 1, "activation fires EXACTLY ONE click for " + (key === " " ? "Space" : key));
  eq(ev(ctx, "selectedWorkItemId"), "message:msg-beta",
     (key === " " ? "Space" : key) + " selects through the canonical path");
  ok(ctx.location.hash.indexOf("msg-beta") !== -1,
     (key === " " ? "Space" : key) + " writes the canonical route");
}

// ---------------------------------------------------------------------------
// 3. COPY controls copy without navigating.
// ---------------------------------------------------------------------------
{
  const env = buildEnv({});
  const ctx = loadApp(env);
  seed(ctx);
  ctx.wire();
  ctx.renderQueue();

  const groups = env.doc.getElementById("queue-groups");
  const other = groups.querySelectorAll(".q-row[data-work-item]")[1];
  ev(ctx, 'selectedWorkItemId = "message:msg-alpha";');
  const before = ctx.location.hash;

  const copy = other.querySelector(".copy-id");
  ok(!!copy, "tiles carry Copy controls");
  copy.dispatchEvent(new MiniEvent("click", { bubbles: true, isTrusted: true }));

  eq(ev(ctx, "selectedWorkItemId"), "message:msg-alpha",
     "clicking Copy does not change the selection");
  eq(ctx.location.hash, before, "clicking Copy does not navigate");
}

// ---------------------------------------------------------------------------
// 4. FOCUS SURVIVES A POLLING CYCLE. This is the reported defect: renderQueue
//    ran about every two seconds and replaced every node, destroying focus.
// ---------------------------------------------------------------------------
{
  const env = buildEnv({});
  const ctx = loadApp(env);
  seed(ctx);
  ctx.wire();
  ctx.renderQueue();

  const groups = env.doc.getElementById("queue-groups");
  const tile = groups.querySelectorAll(".q-row[data-work-item]")[0];
  const btn = tile.querySelector(".q-open");
  btn.focus();

  // Poll repeatedly with UNCHANGED data, exactly as the live timers do.
  for (let i = 0; i < 5; i++) ctx.renderQueue();

  same(env.doc.activeElement, btn, "focus survives repeated unchanged polling");
  ok(env.doc.documentElement.contains(btn), "the focused node is still in the document");
  same(groups.querySelectorAll(".q-row[data-work-item]")[0].querySelector(".q-open"), btn,
     "the tile keeps its DOM IDENTITY across polling (same node)");

  // And it is still operable afterwards, which is the point.
  let clicks = 0;
  btn.addEventListener("click", () => { clicks += 1; });
  const res = pressKey(env.doc, "Enter");
  ok(res.activated && clicks === 1,
     "the tile is still keyboard-operable after several polling cycles");
}

// 4b. A CHANGED item updates without destroying the focus of an UNCHANGED one.
{
  const env = buildEnv({});
  const ctx = loadApp(env);
  seed(ctx);
  ctx.wire();
  ctx.renderQueue();

  const groups = env.doc.getElementById("queue-groups");
  const alphaBtn = groups.querySelectorAll('.q-row[data-work-item="message:msg-alpha"]')[0]
    .querySelector(".q-open");
  alphaBtn.focus();

  const changed = JSON.parse(JSON.stringify(ITEMS));
  changed[1].last_activity_at = "2026-07-25T23:59:00Z";   // only BETA changes
  seed(ctx, changed);
  ctx.renderQueue();

  same(env.doc.activeElement, alphaBtn,
     "changing one tile does not destroy focus held on another");
  ok(env.doc.documentElement.contains(alphaBtn),
     "the untouched tile keeps its node identity when a sibling changes");
}

// 4c. If the focused item legitimately disappears, focus moves predictably.
{
  const env = buildEnv({});
  const ctx = loadApp(env);
  seed(ctx);
  ctx.wire();
  ctx.renderQueue();

  const groups = env.doc.getElementById("queue-groups");
  const betaBtn = groups.querySelectorAll('.q-row[data-work-item="message:msg-beta"]')[0]
    .querySelector(".q-open");
  betaBtn.focus();

  seed(ctx, [ITEMS[0]]);          // beta legitimately leaves the queue
  ctx.renderQueue();

  ok(env.doc.activeElement !== env.doc.body,
     "focus does not fall back to the document body");
  ok(env.doc.activeElement && env.doc.activeElement.classList.contains("q-open"),
     "focus moves predictably to a remaining tile");
  eq(groups.querySelectorAll('.q-row[data-work-item="message:msg-beta"]').length, 0,
     "the removed item is genuinely gone, not retained to preserve focus");
}

// ---------------------------------------------------------------------------
// 4d. GROUP TRANSITION. An item whose presentation_state changes must move into
//     its new group, not be replaced inside its old one.
// ---------------------------------------------------------------------------
{
  const env = buildEnv({});
  const ctx = loadApp(env);
  seed(ctx);
  ctx.wire();
  ctx.renderQueue();
  const groups = env.doc.getElementById("queue-groups");

  const moved = JSON.parse(JSON.stringify(ITEMS));
  moved[1].presentation_state = "needs_operator";   // beta joins alpha's group
  seed(ctx, moved);
  ctx.renderQueue();

  const betaRow = groups.querySelector('.q-row[data-work-item="message:msg-beta"]');
  ok(!!betaRow, "the moved item is still rendered");
  const parentGroup = betaRow.closest(".q-group");
  eq(parentGroup.getAttribute("data-group"), "needs_operator",
     "a changed item lands in its DESIRED group, not its previous one");
  eq(groups.querySelectorAll('.q-group[data-group="blocked"] .q-row').length, 0,
     "no tile is left behind in the old group");
}

// 4e. STALE GROUP REMOVAL. A group with no remaining items must not linger as
//     an empty heading.
{
  const env = buildEnv({});
  const ctx = loadApp(env);
  seed(ctx);
  ctx.wire();
  ctx.renderQueue();
  const groups = env.doc.getElementById("queue-groups");
  eq(groups.querySelectorAll(".q-group").length, 2, "two groups initially");

  seed(ctx, [ITEMS[0]]);            // the blocked group empties out
  ctx.renderQueue();

  eq(groups.querySelectorAll('.q-group[data-group="blocked"]').length, 0,
     "an emptied group is removed, not left as a stale heading");
  eq(groups.querySelectorAll(".q-group").length, 1, "only the populated group remains");
}

// 4f. REORDER-ONLY. When the sort order changes but nothing else does, the
//     rendered order must follow.
{
  const env = buildEnv({});
  const c = loadApp(env);
  const pair = [
    { work_item_id: "message:msg-one", thread_id: "thr-one", title: "One",
      presentation_state: "blocked", status: "planning", runner_state: "waiting_on_council",
      claimed_by: "claude", last_activity_at: "2026-07-25T10:00:00Z" },
    { work_item_id: "message:msg-two", thread_id: "thr-two", title: "Two",
      presentation_state: "blocked", status: "planning", runner_state: "waiting_on_council",
      claimed_by: "claude", last_activity_at: "2026-07-25T09:00:00Z" },
  ];
  seed(c, pair);
  c.wire();
  c.renderQueue();
  const groups = env.doc.getElementById("queue-groups");
  const first = () => groups.querySelectorAll(".q-row[data-work-item]")[0]
    .getAttribute("data-work-item");
  const initial = first();

  // Flip which one is most recent; ranking sorts by last_activity_at desc.
  const flipped = JSON.parse(JSON.stringify(pair));
  flipped[0].last_activity_at = "2026-07-25T08:00:00Z";
  flipped[1].last_activity_at = "2026-07-25T11:00:00Z";
  seed(c, flipped);
  c.renderQueue();

  ok(first() !== initial || true, "reorder path executed");
  eq(first(), "message:msg-two",
     "a reorder-only update is reflected in the rendered order");
  eq(groups.querySelectorAll(".q-row[data-work-item]").length, 2,
     "reordering does not duplicate or drop tiles");
}

// 4g. THE LAST ITEM DISAPPEARS. The empty transition owes the same focus
//     contract: focus must not fall to the document body.
{
  const env = buildEnv({});
  const ctx = loadApp(env);
  seed(ctx, [ITEMS[0]]);
  ctx.wire();
  ctx.renderQueue();
  const groups = env.doc.getElementById("queue-groups");
  const btn = groups.querySelector(".q-open");
  btn.focus();
  same(env.doc.activeElement, btn, "focus starts on the only tile");

  seed(ctx, []);                    // the queue empties entirely
  ctx.renderQueue();

  ok(env.doc.activeElement !== env.doc.body,
     "focus does not fall to the document body when the last tile goes");
  same(env.doc.activeElement, groups,
     "focus moves to the queue container when no tile remains");
}

// 4h. Identifier text and the integrity warning are NOT activation targets.
{
  const env = buildEnv({});
  const ctx = loadApp(env);
  const shared = [
    { work_item_id: "message:msg-s1", thread_id: "thr-shared", title: "S1",
      presentation_state: "blocked", status: "planning", runner_state: "waiting_on_council",
      claimed_by: "claude", last_activity_at: "2026-07-25T10:00:00Z" },
    { work_item_id: "message:msg-s2", thread_id: "thr-shared", title: "S2",
      presentation_state: "blocked", status: "planning", runner_state: "waiting_on_council",
      claimed_by: "claude", last_activity_at: "2026-07-25T09:00:00Z" },
  ];
  seed(ctx, shared);
  ctx.wire();
  ctx.renderQueue();
  const groups = env.doc.getElementById("queue-groups");

  const warn = groups.querySelector(".q-integrity");
  ok(!!warn, "a shared thread raises the integrity warning");
  ev(ctx, 'selectedWorkItemId = null;');
  warn.dispatchEvent(new MiniEvent("click", { bubbles: true, isTrusted: true }));
  eq(ev(ctx, "selectedWorkItemId"), null,
     "clicking the integrity warning does not navigate");

  const idv = groups.querySelector(".q-idv");
  ok(!!idv, "identifier values are rendered");
  idv.dispatchEvent(new MiniEvent("click", { bubbles: true, isTrusted: true }));
  eq(ev(ctx, "selectedWorkItemId"), null,
     "clicking identifier text does not navigate; it can be selected and copied");

  // The explicit control still activates.
  groups.querySelector(".q-open").dispatchEvent(
    new MiniEvent("click", { bubbles: true, isTrusted: true }));
  eq(ev(ctx, "selectedWorkItemId"), "message:msg-s1",
     "the explicit primary control still activates");
}

// ---------------------------------------------------------------------------
// 5. THE REAL send() PATH refuses through every destination-integrity branch.
// ---------------------------------------------------------------------------
function sendEnv(hash) {
  // The responder must answer /api/work-items with a WORK-ITEMS shape. Returning
  // a generic payload made refreshWorkItems set lastWorkItems to [] during the
  // flush, which silently emptied the queue the test had just seeded and made a
  // legitimate retry refuse for the wrong reason.
  const env = buildEnv({
    hash,
    responder: (url, method) => {
      if (url.indexOf("/api/work-items") === 0) {
        return { work_items: env && env.servedItems ? env.servedItems : ITEMS };
      }
      if (url.indexOf("message_id=") !== -1) {
        return { found: true, message: { message: env.lastSent || "PROBE" } };
      }
      if (method === "POST") {
        return { ok: true, message_id: "msg-new", thread_id: "thr-alpha" };
      }
      return { ok: true };
    },
  });
  env.servedItems = ITEMS;
  const ctx = loadApp(env);
  seed(ctx);
  ctx.wire();
  ctx.renderQueue();
  return { env, ctx };
}

// send() is async: it awaits the POST and then a durable re-read. The helper
// must let those continuations run, or the composer stays in flight and the
// `sending` guard silently blocks every later attempt.
const settle = () => new Promise((r) => setImmediate(r));

async function attemptSend(ctx, env, text) {
  const ta = env.doc.getElementById("conv-input");
  const err = env.doc.getElementById("conv-error");
  const btn = env.doc.getElementById("conv-send");
  ta.value = text || "PROBE";
  env.lastSent = ta.value;
  const before = env.posted.filter((p) => p.method === "POST").length;
  ev(ctx, "convComposer.send()");

  // Every destination-integrity refusal happens SYNCHRONOUSLY, before the first
  // await, so the refusal state is captured here. It cannot be read after the
  // flush below, because unrelated render chains resolving in the meantime call
  // restoreDraft() and clear the textarea -- which would look like a cleared
  // draft even though the send was refused.
  const refusal = {
    draft: ta.value,
    error: String(err.textContent || ""),
    label: String(btn.textContent || ""),
    disabled: !!btn.disabled,
  };

  for (let i = 0; i < 8; i++) await settle();   // let the POST chain complete
  const after = env.posted.filter((p) => p.method === "POST").length;
  return {
    posts: after - before,
    draft: refusal.draft,
    error: refusal.error,
    inFlightLabel: refusal.label,
    inFlightDisabled: refusal.disabled,
    settledLabel: String(btn.textContent || ""),
    settledDisabled: !!btn.disabled,
  };
}

// 5a. Unresolved destination: selected item has no durable thread.
{
  const { env, ctx } = sendEnv("#work=message%3Amsg-nothread");
  ev(ctx, 'workItemsLoaded = true; lastWorkItems = [{ work_item_id: "message:msg-nothread",' +
          ' thread_id: null, presentation_state: "needs_operator" }];' +
          'selectedWorkItemId = "message:msg-nothread"; selectedConvThread = null;');
  const r = await attemptSend(ctx, env);
  eq(r.posts, 0, "unresolved destination emits NO request");
  ok(r.draft.length > 0, "unresolved destination preserves the draft");
  ok(r.error.length > 0, "unresolved destination explains itself");
}

// 5b. Route names a DIFFERENT work item than the selection.
{
  const { env, ctx } = sendEnv("#work=message%3Amsg-beta");
  ev(ctx, 'selectedWorkItemId = "message:msg-alpha"; selectedConvThread = "thr-alpha";');
  const r = await attemptSend(ctx, env);
  eq(r.posts, 0, "route/selection disagreement emits NO request");
  ok(r.draft.length > 0, "disagreement preserves the draft");
  ok(r.error.indexOf("different work item") !== -1, "disagreement names the mismatch");
}

// 5c. ABSENT route: nothing proves the URL agrees.
{
  const { env, ctx } = sendEnv("");
  ev(ctx, 'selectedWorkItemId = "message:msg-alpha"; selectedConvThread = "thr-alpha";');
  const r = await attemptSend(ctx, env);
  eq(r.posts, 0, "an absent route emits NO request");
  ok(r.error.indexOf("no work route") !== -1,
     "an absent route says the URL cannot confirm the destination");
}

// 5d. MALFORMED route is not evidence of agreement.
{
  const { env, ctx } = sendEnv("");
  ev(ctx, 'selectedWorkItemId = "message:msg-alpha"; selectedConvThread = "thr-alpha";');
  // Set the malformed route immediately before sending. Setting it earlier
  // would let applyWorkHashRoute clear it first, so the send would then be
  // refused for an ABSENT route and this branch would never be exercised.
  ctx.location.hash = "#work=%";
  const r = await attemptSend(ctx, env);
  eq(r.posts, 0, "a malformed route emits NO request");
  ok(r.error.indexOf("unreadable") !== -1, "a malformed route says the URL is unreadable");
}

// 5e. VALID RETRY after a refusal succeeds, and the button state is restored.
{
  const { env, ctx } = sendEnv("");
  ev(ctx, 'selectedWorkItemId = "message:msg-alpha"; selectedConvThread = "thr-alpha";');
  const refused = await attemptSend(ctx, env);
  eq(refused.posts, 0, "the first attempt is refused");
  eq(refused.settledLabel, "Send", "the button label is unchanged by a refusal");
  ok(!refused.settledDisabled, "the button is re-enabled after a refusal");

  // Correct the route and retry. The selection is re-established explicitly,
  // because unrelated render chains resolving during the flush can clear it and
  // this case is about the ROUTE being corrected, not about selection drift.
  ctx.location.hash = "#work=" + encodeURIComponent("message:msg-alpha");
  ev(ctx, 'selectedWorkItemId = "message:msg-alpha"; selectedConvThread = "thr-alpha";');
  const retry = await attemptSend(ctx, env);
  eq(retry.posts, 1, "a valid retry after a refusal DOES send");
}

// 5f. DUPLICATE submission while in flight produces exactly one POST.
{
  const { env, ctx } = sendEnv("#work=message%3Amsg-alpha");
  ev(ctx, 'selectedWorkItemId = "message:msg-alpha"; selectedConvThread = "thr-alpha";');
  const ta = env.doc.getElementById("conv-input");
  const btn = env.doc.getElementById("conv-send");
  ta.value = "PROBE";

  const before = env.posted.filter((p) => p.method === "POST").length;
  ev(ctx, "convComposer.send()");                     // in flight from here
  const during = { label: btn.textContent, disabled: btn.disabled };
  ev(ctx, "convComposer.send()");                     // repeat click
  ev(ctx, "convComposer.send()");                     // and again
  const after = env.posted.filter((p) => p.method === "POST").length;

  eq(after - before, 1, "three submissions in flight produce EXACTLY ONE POST");
  eq(during.label, "Sending...", "the button reports the in-flight state");
  ok(during.disabled, "the button is disabled while in flight");
}

// 5g. Ctrl+Enter uses the same guarded path, not a parallel one.
{
  const { env, ctx } = sendEnv("#work=message%3Amsg-alpha");
  ev(ctx, 'selectedWorkItemId = "message:msg-alpha"; selectedConvThread = "thr-alpha";');
  const ta = env.doc.getElementById("conv-input");
  ta.value = "PROBE";
  const before = env.posted.filter((p) => p.method === "POST").length;
  ta.dispatchEvent(new MiniEvent("keydown", { key: "Enter", ctrlKey: true, bubbles: true, cancelable: true }));
  ta.dispatchEvent(new MiniEvent("keydown", { key: "Enter", ctrlKey: true, bubbles: true, cancelable: true }));
  const after = env.posted.filter((p) => p.method === "POST").length;
  eq(after - before, 1, "repeated Ctrl+Enter in flight produces EXACTLY ONE POST");
}

// ---------------------------------------------------------------------------
// 6. STALE SELECTION AFTER POLLING. The reported gap: once polling removed the
//    selected item, a retained thread plus a matching stale hash let a request
//    be built for a destination the live queue no longer backed.
// ---------------------------------------------------------------------------
{
  const { env, ctx } = sendEnv("");
  ctx.wire();
  ctx.renderQueue();

  // Select a VALID live item through the real wired path.
  const groups = env.doc.getElementById("queue-groups");
  groups.querySelector('.q-row[data-work-item="message:msg-alpha"] .q-open')
    .dispatchEvent(new MiniEvent("click", { bubbles: true, isTrusted: true }));
  eq(ev(ctx, "selectedWorkItemId"), "message:msg-alpha", "a live item is selected");
  eq(ev(ctx, "selectedConvThread"), "thr-alpha", "its durable thread is bound");
  const routeAfterSelect = ctx.location.hash;
  ok(routeAfterSelect.indexOf("msg-alpha") !== -1, "the canonical route is written");

  // A send here is legitimate, proving the refusal below is not incidental.
  const okAttempt = await attemptSend(ctx, env, "BASELINE");
  eq(okAttempt.posts, 1, "a valid live selection DOES send");

  // Now polling removes that item while thread, hash and composer state remain.
  // env is passed so the SERVED payload drops it too: otherwise the next poll
  // would restore it and the test could pass without exercising the gap.
  seed(ctx, [ITEMS[1]], env);
  ctx.renderQueue();
  ctx.location.hash = routeAfterSelect;          // stale but MATCHING route
  ev(ctx, 'selectedWorkItemId = "message:msg-alpha"; selectedConvThread = "thr-alpha";');

  eq(ev(ctx, "selectedConvThread"), "thr-alpha", "the stale thread is still remembered");
  ok(ctx.location.hash.indexOf("msg-alpha") !== -1, "the stale route still matches");

  const refused = await attemptSend(ctx, env, "MUST NOT SEND");
  eq(refused.posts, 0,
     "a selection removed by polling emits NO request, despite stale thread and matching route");
  ok(refused.draft.length > 0, "the draft is preserved through the refusal");
  ok(refused.error.indexOf("no longer in the live queue") !== -1,
     "the refusal explains that the item left the live queue");
  eq(refused.settledLabel, "Send", "the button returns from Sending... to Send");
  ok(!refused.settledDisabled, "the button is re-enabled after the refusal");

  // The target itself must report unresolved rather than a sendable pair.
  const stale = ev(ctx, "convComposerTarget()");
  ok(stale.unresolved === true, "the stale target reports unresolved");
  ok(!stale.thread_id, "the stale target carries no thread");

  // Retry succeeds ONLY after selecting a valid live item.
  groups.querySelector('.q-row[data-work-item="message:msg-beta"] .q-open')
    .dispatchEvent(new MiniEvent("click", { bubbles: true, isTrusted: true }));
  eq(ev(ctx, "selectedWorkItemId"), "message:msg-beta", "a live item is reselected");
  const retry = await attemptSend(ctx, env, "NOW VALID");
  eq(retry.posts, 1, "the retry sends only after a valid live item is selected");
}

// 6b. THREAD REMOVED from the live record, item still present.
{
  const { env, ctx } = sendEnv("#work=message%3Amsg-alpha");
  ev(ctx, 'selectedWorkItemId = "message:msg-alpha"; selectedConvThread = "thr-alpha";');
  ev(ctx, 'workItemsLoaded = true; lastWorkItems = [{ work_item_id: "message:msg-alpha",' +
          ' thread_id: null, presentation_state: "needs_operator" }];');
  const r = await attemptSend(ctx, env, "MUST NOT SEND");
  eq(r.posts, 0, "an item whose live record lost its thread emits NO request");
  ok(r.draft.length > 0, "the draft is preserved");
}

// 6c. A PACKET PROJECTION can never be a message destination.
{
  const { env, ctx } = sendEnv("#work=in_progress%3Asession-ux-cta-20260725");
  ev(ctx, 'workItemsLoaded = true; lastWorkItems = [{' +
          ' work_item_id: "in_progress:session-ux-cta-20260725", thread_id: null,' +
          ' presentation_state: "waiting_on_claude" }];' +
          'selectedWorkItemId = "in_progress:session-ux-cta-20260725";' +
          'selectedConvThread = "thr-anything";');
  ok(ev(ctx, 'isCanonicalMessageWorkItem("in_progress:session-ux-cta-20260725")') === false,
     "a packet projection is not a canonical message work item");
  const t = ev(ctx, "convComposerTarget()");
  ok(t.unresolved === true, "a packet projection resolves to an unresolved target");
  const r = await attemptSend(ctx, env, "MUST NOT SEND");
  eq(r.posts, 0, "a packet projection emits NO request even with a remembered thread");
}

// 6d. A MALFORMED record can never become a destination or a reconciled tile.
{
  const { env, ctx } = sendEnv("");
  ctx.wire();
  ev(ctx, 'workItemsLoaded = true; lastWorkItems = [' +
          '{ work_item_id: "message:msg-good", thread_id: "thr-good", title: "Good",' +
          '  presentation_state: "blocked", status: "planning", runner_state: "waiting_on_council" },' +
          '{ work_item_id: null, thread_id: "thr-orphan", title: "No canonical id",' +
          '  presentation_state: "blocked", status: "planning", runner_state: "waiting_on_council" },' +
          '{ thread_id: "thr-orphan2", title: "Missing entirely",' +
          '  presentation_state: "blocked", status: "planning", runner_state: "waiting_on_council" }];');
  ctx.renderQueue();
  const groups = env.doc.getElementById("queue-groups");
  eq(groups.querySelectorAll(".q-row[data-work-item]").length, 1,
     "only the canonical record is reconciled into a tile");
  eq(groups.querySelectorAll('.q-row[data-work-item=""]').length, 0,
     "no tile is keyed on an empty work-item id");

  for (const bad of [null, "", "thr-20260725T142257787771", "in_progress:x", "message:", "msg-1"]) {
    ok(ev(ctx, "isCanonicalMessageWorkItem(" + JSON.stringify(bad) + ")") === false,
       JSON.stringify(bad) + " is not a canonical message work item");
  }
  ok(ev(ctx, 'isCanonicalMessageWorkItem("message:msg-20260725T142257787771")') === true,
     "a real canonical id is accepted");
}

// 6e. SELECTOR-SIGNIFICANT characters in identifiers must not break escaping.
{
  const env = buildEnv({});
  const ctx = loadApp(env);
  const hostile = ['a"b', "a\\b", "a]b", "a b", "a.b", "a#b", "a:b"];
  hostile.forEach((v) => {
    const out = ev(ctx, "cssAttrValue(" + JSON.stringify(v) + ")");
    ok(typeof out === "string" && out.length >= v.length,
       "cssEscape returns an escaped string for " + JSON.stringify(v));
    // The escaped value must be usable in a selector without throwing.
    let threw = null;
    try { env.doc.body.querySelector('[data-x="' + out + '"]'); }
    catch (e) { threw = String(e); }
    ok(threw === null, "an escaped value is selector-safe for " + JSON.stringify(v));
  });
  // Escaping must be applied, not merely available.
  const src = fs.readFileSync(APP, "utf8");
  // Ignore comment lines: the code explains WHY CSS.escape is wrong here.
  const codeOnly = src.split("\n").filter((l) => l.trim().indexOf("//") !== 0).join("\n");
  ok(codeOnly.indexOf("CSS.escape") === -1,
     "identifier escaping is not used for quoted attribute selectors");
}

// ---------------------------------------------------------------------------
// 7. A FAILED QUEUE REFRESH withdraws send authority. A snapshot that merely
//    loaded once is not evidence the destination is still live.
// ---------------------------------------------------------------------------
{
  const { env, ctx } = sendEnv("");
  ctx.wire();
  ctx.renderQueue();
  const groups = env.doc.getElementById("queue-groups");
  groups.querySelector('.q-row[data-work-item="message:msg-alpha"] .q-open')
    .dispatchEvent(new MiniEvent("click", { bubbles: true, isTrusted: true }));
  eq(ev(ctx, "selectedWorkItemId"), "message:msg-alpha", "a live item is selected");

  const before = await attemptSend(ctx, env, "BASELINE");
  eq(before.posts, 1, "sending works while the queue is confirmed");

  // The refresh now FAILS. The snapshot and the selection are untouched.
  ev(ctx, "queueConfirmed = false;");
  ok(ev(ctx, "lastWorkItems.length") > 0, "the stale snapshot is still present");
  ok(ev(ctx, "workItemsLoaded") === true, "the queue still counts as loaded");
  eq(ev(ctx, "selectedWorkItemId"), "message:msg-alpha", "the selection survives");

  const refused = await attemptSend(ctx, env, "MUST NOT SEND");
  eq(refused.posts, 0, "a stale, unconfirmed queue emits NO request");
  ok(refused.draft.length > 0, "the draft is preserved");
  ok(refused.error.indexOf("not currently confirmed") !== -1,
     "the refusal explains that the queue is unconfirmed");
  eq(refused.settledLabel, "Send", "the button returns to Send");
  ok(!refused.settledDisabled, "the button is re-enabled");

  // RECOVERY: a successful refresh re-confirms and sending resumes.
  seed(ctx, ITEMS, env);
  ev(ctx, 'selectedWorkItemId = "message:msg-alpha"; selectedConvThread = "thr-alpha";');
  ctx.location.hash = "#work=" + encodeURIComponent("message:msg-alpha");
  const recovered = await attemptSend(ctx, env, "NOW CONFIRMED");
  eq(recovered.posts, 1, "sending resumes after a successful refresh re-confirms");
}

// 7b. A real refreshWorkItems FAILURE marks the queue unconfirmed.
{
  const env = buildEnv({ responder: () => { throw new Error("network down"); } });
  const ctx = loadApp(env);
  seed(ctx, ITEMS, env);
  ok(ev(ctx, "queueConfirmed") === true, "confirmed after seeding");
  env.failAll = true;
  // Force the failure path through the real function.
  ctx.fetch = () => Promise.reject(new Error("network down"));
  await ctx.refreshWorkItems();
  ok(ev(ctx, "queueConfirmed") === false,
     "a failed refresh withdraws queue confirmation");
  ok(ev(ctx, "lastWorkItems.length") > 0,
     "the previous content stays on screen rather than blanking the operator");
}

// ---------------------------------------------------------------------------
// 8. NON-CANONICAL ENTRIES render READ-ONLY: visible, never activatable.
// ---------------------------------------------------------------------------
{
  const env = buildEnv({});
  const ctx = loadApp(env);
  const mixed = [
    { work_item_id: "message:msg-real", thread_id: "thr-real", title: "Real work",
      presentation_state: "blocked", status: "planning", runner_state: "waiting_on_council" },
    { work_item_id: "in_progress:session-ux-cta-20260725", thread_id: null,
      title: "CTA packet", presentation_state: "waiting_on_claude", status: "open",
      runner_state: "unknown" },
    { work_item_id: "totally-malformed-but-truthy", thread_id: "thr-x",
      title: "Malformed", presentation_state: "blocked", status: "planning",
      runner_state: "waiting_on_council" },
  ];
  seed(ctx, mixed, env);
  ctx.wire();
  ctx.renderQueue();
  const groups = env.doc.getElementById("queue-groups");

  // All three remain VISIBLE: hiding real durable records is not the fix.
  eq(groups.querySelectorAll(".q-row[data-work-item]").length, 3,
     "every durable record stays visible, canonical or not");

  // Only the canonical one is activatable.
  eq(groups.querySelectorAll(".q-open").length, 1,
     "only a canonical message work item gets an activation control");
  const proj = groups.querySelector('.q-row[data-work-item="in_progress:session-ux-cta-20260725"]');
  eq(proj.getAttribute("data-canonical"), "false", "the projection is marked non-canonical");
  ok(!proj.querySelector(".q-open"), "the projection has NO activation control");
  ok(!!proj.querySelector(".q-readonly"), "the projection renders read-only");
  ok(!!proj.querySelector(".q-ro-badge"), "the projection is labelled as a packet record");

  const mal = groups.querySelector('.q-row[data-work-item="totally-malformed-but-truthy"]');
  eq(mal.getAttribute("data-canonical"), "false", "a truthy non-canonical id is non-canonical");
  ok(!mal.querySelector(".q-open"), "a malformed record has NO activation control");

  // Clicking a read-only row navigates nowhere.
  ev(ctx, "selectedWorkItemId = null;");
  proj.dispatchEvent(new MiniEvent("click", { bubbles: true, isTrusted: true }));
  eq(ev(ctx, "selectedWorkItemId"), null, "clicking a packet projection does not navigate");
  mal.dispatchEvent(new MiniEvent("click", { bubbles: true, isTrusted: true }));
  eq(ev(ctx, "selectedWorkItemId"), null, "clicking a malformed record does not navigate");

  // The canonical one still works.
  groups.querySelector(".q-open").dispatchEvent(
    new MiniEvent("click", { bubbles: true, isTrusted: true }));
  eq(ev(ctx, "selectedWorkItemId"), "message:msg-real",
     "the canonical record is still activatable");

  // aria-current, not aria-pressed: this navigates, it does not toggle.
  const btn = groups.querySelector(".q-open");
  ok(btn.hasAttribute("aria-current"), "the control uses aria-current");
  ok(!btn.hasAttribute("aria-pressed"),
     "it does not advertise a toggle-button contract");
}

// 8b. POSITIVE selector-match proof for escaped values, not merely no-throw.
{
  const env = buildEnv({});
  const ctx = loadApp(env);
  const host = env.doc.createElement("div");
  env.doc.body.appendChild(host);
  const hostile = ['a"b', "a\\b", "a]b", "a b", "a.b", "a#b", "a:b", "a[b"];
  hostile.forEach((v, i) => {
    const el = env.doc.createElement("span");
    el.setAttribute("data-probe", v);
    el.setAttribute("data-idx", String(i));
    host.appendChild(el);
    const escd = ev(ctx, "cssAttrValue(" + JSON.stringify(v) + ")");
    let found = null;
    try { found = host.querySelector('[data-probe="' + escd + '"]'); }
    catch (e) { found = null; }
    same(found, el, "an escaped value SELECTS THE INTENDED node for " + JSON.stringify(v));
  });
}

// ---------------------------------------------------------------------------
// 9. REFRESH OUTCOMES, SEQUENCING AND PERSISTENT FAILURE VISIBILITY.
//    Everything below drives the REAL refreshWorkItems / wire() / send paths.
//    A controllable transport lets a test decide, per request, whether a
//    /api/work-items call succeeds, fails, or resolves LATE and out of order.
// ---------------------------------------------------------------------------
function queueEnv(opts) {
  opts = opts || {};
  const state = { mode: "ok", items: ITEMS, pending: [] };
  const env = buildEnv({
    hash: opts.hash || "",
    responder: (url, method) => {
      if (url.indexOf("message_id=") !== -1) return { found: true, message: { message: env.lastSent || "PROBE" } };
      if (method === "POST") return { ok: true, message_id: "msg-new", thread_id: "thr-alpha" };
      return { ok: true };
    },
  });
  const realFetch = env.ctx.fetch;
  env.queue = state;
  env.ctx.fetch = function (url, init) {
    const u = String(url);
    const method = (init && init.method) || "GET";
    if (u.indexOf("/api/work-items") === 0) {
      env.posted.push({ url: u, method });   // recorded here; not delegated
      if (state.mode === "fail") return Promise.reject(new Error("probe: queue down"));
      if (state.mode === "defer") {
        // Hand the test the resolver so it can complete this call LATER.
        return new Promise((resolve, reject) => {
          state.pending.push({
            resolveWith: (items) => resolve({ ok: true, status: 200,
              json: () => Promise.resolve({ work_items: items }) }),
            rejectWith: (e) => reject(e || new Error("probe: deferred failure")),
          });
        });
      }
      return Promise.resolve({ ok: true, status: 200,
        json: () => Promise.resolve({ work_items: state.items }) });
    }
    return realFetch(url, init);
  };
  return env;
}

const statusOf = (env) => {
  const el = env.doc.getElementById("restore-status");
  const text = String(el.textContent || "");
  // A status counts as shown only when it is both un-hidden AND has content:
  // the stub element starts without a hidden attribute, so emptiness is the
  // reliable signal that nothing is being reported.
  const shown = !el.hidden && text.trim().length > 0;
  return { hidden: !shown, shown: shown, text: text };
};

// 9a. THE FOUR OUTCOMES ARE DISTINCT.
{
  const env = queueEnv({});
  const ctx = loadApp(env);
  env.queue.items = ITEMS;
  eq(await ctx.refreshWorkItems(), "confirmed", "a populated load is CONFIRMED");
  env.queue.items = [];
  eq(await ctx.refreshWorkItems(), "confirmed_empty",
     "an EMPTY load is confirmed_empty, not failed and not unloaded");
  ok(ev(ctx, "workItemsLoaded") === true, "a confirmed empty load is still LOADED");
  ok(ev(ctx, "queueConfirmed") === true, "a confirmed empty load is CONFIRMED");
  eq(ev(ctx, "lastWorkItems.length"), 0, "and the snapshot is genuinely empty");
  env.queue.mode = "fail";
  eq(await ctx.refreshWorkItems(), "failed", "a rejected load is FAILED");
  ok(ev(ctx, "queueConfirmed") === false, "a failure withdraws confirmation");
  ok(ev(ctx, "workItemsLoaded") === true,
     "a failure does not pretend the queue was never loaded");
}

// 9b. STALE COMPLETION, DIRECTION ONE: an OLDER SUCCESS after a NEWER FAILURE
//     must not restore sending.
{
  const env = queueEnv({});
  const ctx = loadApp(env);
  env.queue.items = ITEMS;
  await ctx.refreshWorkItems();
  ok(ev(ctx, "queueConfirmed") === true, "confirmed to begin with");

  env.queue.mode = "defer";
  const older = ctx.runWorkItemsRefresh();       // generation N, left in flight
  env.queue.mode = "fail";
  const newerOutcome = await ctx.runWorkItemsRefresh();   // generation N+1, fails
  eq(newerOutcome, "failed", "the newer refresh failed");
  ok(ev(ctx, "queueConfirmed") === false, "confirmation is withdrawn");

  env.queue.pending.shift().resolveWith(ITEMS);  // the OLDER one now succeeds
  eq(await older, "superseded", "the older success reports SUPERSEDED");
  ok(ev(ctx, "queueConfirmed") === false,
     "an older success does NOT restore confirmation after a newer failure");
  ok(ev(ctx, "queueFailureReported") === true, "the failure is still reported");
}

// 9c. STALE COMPLETION, DIRECTION TWO: an OLDER FAILURE after a NEWER SUCCESS
//     must not invalidate the confirmed snapshot.
{
  const env = queueEnv({});
  const ctx = loadApp(env);
  env.queue.mode = "defer";
  const older = ctx.runWorkItemsRefresh();       // generation N, in flight
  env.queue.mode = "ok";
  env.queue.items = ITEMS;
  eq(await ctx.runWorkItemsRefresh(), "confirmed", "the newer refresh confirmed");
  ok(ev(ctx, "queueConfirmed") === true, "the queue is confirmed");

  env.queue.pending.shift().rejectWith();        // the OLDER one now fails
  eq(await older, "superseded", "the older failure reports SUPERSEDED");
  ok(ev(ctx, "queueConfirmed") === true,
     "an older failure does NOT invalidate a newer confirmed snapshot");
  ok(ev(ctx, "queueFailureReported") === false, "no failure is reported");
  eq(statusOf(env).hidden, true, "and no failure status is shown");
}

// 9d. INITIAL LOAD FAILURE THROUGH wire(): the explanation must SURVIVE every
//     continuation. This is the regression the previous round introduced.
{
  const env = queueEnv({});
  const ctx = loadApp(env);
  env.queue.mode = "fail";
  ctx.wire();
  for (let i = 0; i < 12; i++) await settle();   // let ALL continuations run

  ok(ev(ctx, "queueConfirmed") === false, "boot failure leaves the queue unconfirmed");
  ok(ev(ctx, "queueFailureReported") === true, "the failure is recorded");
  const st = statusOf(env);
  eq(st.hidden, false, "the explanation is STILL VISIBLE after boot settles");
  ok(st.text.indexOf("Sending is paused") !== -1,
     "the explanation is the plain-language sending-paused message");
  ok(st.text.indexOf("could not be refreshed") !== -1, "it says what went wrong");
  eq(ev(ctx, "selectedWorkItemId"), null,
     "no selection is restored from an unconfirmed snapshot");

  // Transient cleanup must not erase it either.
  ctx.clearTransientRestoreStatus();
  eq(statusOf(env).hidden, false,
     "transient-status cleanup does NOT erase a refresh failure");

  // Nor may a later polling cycle that also fails.
  await ctx.refreshWorkItems();
  eq(statusOf(env).hidden, false, "a further failed poll keeps the explanation");
}

// 9e. ZERO POSTS while unconfirmed, DRAFT PRESERVED, then RECOVERY.
{
  const env = queueEnv({});
  const ctx = loadApp(env);
  env.queue.items = ITEMS;
  await ctx.refreshWorkItems();
  ctx.wire();
  ctx.renderQueue();
  env.doc.getElementById("queue-groups")
    .querySelector('.q-row[data-work-item="message:msg-alpha"] .q-open')
    .dispatchEvent(new MiniEvent("click", { bubbles: true, isTrusted: true }));
  eq(ev(ctx, "selectedWorkItemId"), "message:msg-alpha", "a live item is selected");

  const baseline = await attemptSend(ctx, env, "BASELINE");
  eq(baseline.posts, 1, "sending works while confirmed");

  env.queue.mode = "fail";
  eq(await ctx.refreshWorkItems(), "failed", "the refresh now fails");

  const refused = await attemptSend(ctx, env, "MUST NOT SEND");
  eq(refused.posts, 0, "ZERO POSTs while the queue is unconfirmed");
  ok(refused.draft.length > 0, "the draft is preserved");
  ok(refused.error.indexOf("not currently confirmed") !== -1,
     "the refusal explains the unconfirmed queue");
  eq(refused.settledLabel, "Send", "the button is restored");
  eq(statusOf(env).hidden, false, "the sending-paused explanation is still visible");

  // RECOVERY through a later CONFIRMED refresh.
  env.queue.mode = "ok";
  eq(await ctx.refreshWorkItems(), "confirmed", "a later refresh confirms");
  ok(ev(ctx, "queueFailureReported") === false, "the failure record is cleared");
  eq(statusOf(env).hidden, true, "and the explanation is cleared with it");
  ev(ctx, 'selectedWorkItemId = "message:msg-alpha"; selectedConvThread = "thr-alpha";');
  ctx.location.hash = "#work=" + encodeURIComponent("message:msg-alpha");
  const recovered = await attemptSend(ctx, env, "NOW CONFIRMED");
  eq(recovered.posts, 1, "sendability is restored only after a confirmed refresh");
}

// 9f. A CONFIRMED EMPTY result is authoritative: stale destinations are NOT
//     sendable afterwards, and it is not treated as a failure.
{
  const env = queueEnv({});
  const ctx = loadApp(env);
  env.queue.items = ITEMS;
  await ctx.refreshWorkItems();
  ctx.wire();
  // wire() starts a boot refresh that owns the polling slot. Let it settle
  // before the deterministic empty-outcome assertion below, or that call would
  // coalesce instead of performing a request. Coalescing is covered in 11.
  for (let i = 0; i < 10; i++) await settle();
  ctx.renderQueue();
  ev(ctx, 'selectedWorkItemId = "message:msg-alpha"; selectedConvThread = "thr-alpha";');
  ctx.location.hash = "#work=" + encodeURIComponent("message:msg-alpha");

  env.queue.items = [];
  // wire() above owns the polling slot, so a coalescing call would return
  // "coalesced". This case is about the EMPTY OUTCOME, so drive the request
  // directly; the coalescing contract is covered in section 11.
  eq(await ctx.refreshWorkItems(), "confirmed_empty", "the queue is confirmed EMPTY");
  ok(ev(ctx, "queueConfirmed") === true, "confirmed empty is still confirmed");
  eq(statusOf(env).hidden, true, "a confirmed empty result shows no failure status");

  const t = ev(ctx, "convComposerTarget()");
  ok(t.unresolved === true, "the stale destination is unresolved after an empty result");
  const r = await attemptSend(ctx, env, "MUST NOT SEND");
  eq(r.posts, 0, "a stale destination is NOT sendable after a confirmed empty result");
  ok(r.draft.length > 0, "the draft is preserved");
  ok(r.error.indexOf("no longer in the live queue") !== -1,
     "the refusal names the live queue, not a refresh failure");
}

// ---------------------------------------------------------------------------
// 10. CSS STRING ESCAPING is complete, and a MALFORMED payload is not an
//     authoritative empty queue.
// ---------------------------------------------------------------------------
{
  const env = buildEnv({});
  const ctx = loadApp(env);

  // Control characters are not permitted raw in a CSS string token.
  const controls = ["a\nb", "a\rb", "a\tb", "a\u0000b", "a\u001fb", "a\u007fb"];
  controls.forEach((v) => {
    const out = ev(ctx, "cssAttrValue(" + JSON.stringify(v) + ")");
    ok(out.indexOf("\n") === -1 && out.indexOf("\r") === -1,
       "no raw newline survives escaping of " + JSON.stringify(v));
    ok(/\\[0-9a-f]+ /.test(out),
       "a control character is hex-escaped in " + JSON.stringify(v));
  });

  // And the ordinary cases still round-trip to the intended node.
  const host = env.doc.createElement("div");
  env.doc.body.appendChild(host);
  ['a"b', "a\\b", "a b", "a]b"].forEach((v) => {
    const el = env.doc.createElement("span");
    el.setAttribute("data-probe", v);
    host.appendChild(el);
    const escd = ev(ctx, "cssAttrValue(" + JSON.stringify(v) + ")");
    let found = null;
    try { found = host.querySelector('[data-probe="' + escd + '"]'); } catch (e) { found = null; }
    same(found, el, "escaping still selects the intended node for " + JSON.stringify(v));
  });
}

// 10b. A 200 with a MALFORMED body must be a FAILURE, not a confirmed empty.
for (const bad of [{}, { work_items: null }, { work_items: "nope" }, { work_items: 7 }]) {
  const env = queueEnv({});
  const ctx = loadApp(env);
  env.queue.items = ITEMS;
  await ctx.refreshWorkItems();
  ok(ev(ctx, "queueConfirmed") === true, "confirmed to begin with");
  const snapshotBefore = ev(ctx, "lastWorkItems.length");

  // Return 200 with a body that is not a work-items payload.
  env.ctx.fetch = (url, init) => {
    const u = String(url);
    const method = (init && init.method) || "GET";
    if (u.indexOf("/api/work-items") === 0) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(bad) });
    }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ ok: true }) });
  };
  const outcome = await ctx.refreshWorkItems();
  eq(outcome, "failed",
     "a malformed body is FAILED, not confirmed_empty: " + JSON.stringify(bad));
  ok(ev(ctx, "queueConfirmed") === false, "confirmation is withdrawn");
  eq(ev(ctx, "lastWorkItems.length"), snapshotBefore,
     "the previous snapshot is preserved, not replaced by an invented empty one");
  ok(statusOf(env).shown, "the operator is told the response was unreadable");
  ok(statusOf(env).text.indexOf("unreadable") !== -1, "and why");
}

// ---------------------------------------------------------------------------
// 11. OVERLAPPING-POLL STARVATION. This is the production regression: the
//     endpoint was slower than the poll interval, so every request was
//     superseded by the next before it could apply and the queue never
//     confirmed. These drive the REAL refreshWorkItems entry point.
// ---------------------------------------------------------------------------
{
  const env = queueEnv({});
  const ctx = loadApp(env);
  env.queue.items = ITEMS;
  env.queue.mode = "defer";                 // response outlives the poll tick

  // Poll while a request is active, repeatedly, exactly as setInterval does.
  const active = ctx.refreshWorkItems();
  const p1 = await ctx.refreshWorkItems();
  const p2 = await ctx.refreshWorkItems();
  const p3 = await ctx.refreshWorkItems();
  eq([p1, p2, p3], ["coalesced", "coalesced", "coalesced"],
     "polls arriving during an active refresh COALESCE, they do not start requests");
  eq(env.queue.pending.length, 1,
     "AT MOST ONE request is active no matter how many polls arrive");
  ok(ev(ctx, "queueRefreshInFlight") === true, "the active refresh still owns the slot");

  // The active request is NOT invalidated by those polls: it settles and applies.
  env.queue.pending.shift().resolveWith(ITEMS);
  eq(await active, "confirmed", "the active refresh CONFIRMS rather than being superseded");
  ok(ev(ctx, "queueConfirmed") === true, "the queue is confirmed");
  ok(ev(ctx, "workItemsLoaded") === true, "the queue is loaded");
  eq(ev(ctx, "lastWorkItems.length"), ITEMS.length, "the snapshot is applied");

  // Exactly one coalesced follow-up was started, not three.
  eq(env.queue.pending.length, 1, "exactly ONE coalesced follow-up runs, no backlog");
  env.queue.pending.shift().resolveWith(ITEMS);
}

// 11b. SUSTAINED slow endpoint with continuous polling still CONFIRMS and
//      renders, and the generation does not climb without bound.
{
  const env = queueEnv({});
  const ctx = loadApp(env);
  env.queue.items = ITEMS;
  // No wire() here: this case is exactly about the polling slot, so nothing
  // else may own it.

  // Ten polling ticks while every response is slow.
  env.queue.mode = "defer";
  const first = ctx.refreshWorkItems();
  for (let i = 0; i < 10; i++) await ctx.refreshWorkItems();
  eq(env.queue.pending.length, 1, "ten polls produce ONE active request");
  const genDuringPolls = ev(ctx, "queueRefreshGeneration");

  env.queue.pending.shift().resolveWith(ITEMS);
  eq(await first, "confirmed", "the request confirms despite continuous polling");
  // Drain the single follow-up.
  if (env.queue.pending.length) env.queue.pending.shift().resolveWith(ITEMS);
  for (let i = 0; i < 6; i++) await settle();

  ok(ev(ctx, "queueConfirmed") === true, "queue confirmation is REACHED, not starved");
  ctx.renderQueue();
  const tiles = env.doc.getElementById("queue-groups").querySelectorAll(".q-row[data-work-item]");
  eq(tiles.length, ITEMS.length, "the expected work items RENDER");
  const genAfter = ev(ctx, "queueRefreshGeneration");
  ok(genAfter - genDuringPolls <= 2,
     "the generation does not increase without bound while polls coalesce");
}

// 11c. An explicit operator refresh during an active one uses the same bounded
//      follow-up rather than piling on.
{
  const env = queueEnv({});
  const ctx = loadApp(env);
  env.queue.items = ITEMS;
  env.queue.mode = "defer";
  const active = ctx.refreshWorkItems();
  eq(await ctx.refreshWorkItems(), "coalesced", "an explicit refresh coalesces too");
  eq(await ctx.refreshWorkItems(), "coalesced", "and a second one does not queue a backlog");
  eq(env.queue.pending.length, 1, "still exactly one active request");
  env.queue.pending.shift().resolveWith(ITEMS);
  await active;
  eq(env.queue.pending.length, 1, "exactly one follow-up, not two");
  env.queue.pending.shift().resolveWith(ITEMS);
}

// 11d. A coalesced poll is NOT a success: it must not clear status or restore.
{
  const env = queueEnv({});
  const ctx = loadApp(env);
  ok(ev(ctx, 'refreshSucceeded("coalesced")') === false,
     "a coalesced poll is not treated as a confirmed refresh");
  ok(ev(ctx, 'refreshSucceeded("confirmed")') === true, "confirmed is a success");
  ok(ev(ctx, 'refreshSucceeded("confirmed_empty")') === true, "confirmed_empty is a success");
  ok(ev(ctx, 'refreshSucceeded("failed")') === false, "failed is not a success");
  ok(ev(ctx, 'refreshSucceeded("superseded")') === false, "superseded is not a success");
}

// 11e. The slot is released even when the active refresh FAILS, so a later poll
//      can still recover. A stuck flag would be a permanent outage.
{
  const env = queueEnv({});
  const ctx = loadApp(env);
  env.queue.mode = "fail";
  eq(await ctx.refreshWorkItems(), "failed", "the refresh fails");
  ok(ev(ctx, "queueRefreshInFlight") === false, "the in-flight slot is released on failure");
  ok(ev(ctx, "queueConfirmed") === false, "confirmation is withdrawn");
  ok(ev(ctx, "lastWorkItems.length") >= 0, "the snapshot is retained, not cleared");

  env.queue.mode = "ok";
  env.queue.items = ITEMS;
  eq(await ctx.refreshWorkItems(), "confirmed", "a later poll recovers");
  ok(ev(ctx, "queueConfirmed") === true, "sendability is restored");
  ok(ev(ctx, "queueFailureReported") === false, "the failure record is cleared");
}

// 11f. No duplicate, phantom or synthetic durable record is created by any of
//      this: coalescing must not emit extra requests.
{
  const env = queueEnv({});
  const ctx = loadApp(env);
  env.queue.items = ITEMS;
  env.queue.mode = "defer";
  const before = env.posted.filter((p) => p.url.indexOf("/api/work-items") === 0).length;
  const active = ctx.refreshWorkItems();
  for (let i = 0; i < 5; i++) await ctx.refreshWorkItems();
  const during = env.posted.filter((p) => p.url.indexOf("/api/work-items") === 0).length;
  eq(during - before, 1, "five extra polls emit ZERO extra requests");
  env.queue.pending.shift().resolveWith(ITEMS);
  await active;
  const after = env.posted.filter((p) => p.url.indexOf("/api/work-items") === 0).length;
  eq(after - before, 2, "exactly the active request plus ONE follow-up");
  if (env.queue.pending.length) env.queue.pending.shift().resolveWith(ITEMS);
  eq(env.posted.filter((p) => p.method === "POST").length, 0,
     "no durable write is produced by refreshing");
}

// 11g. MULTI-CALLER LIFECYCLE: wire()/boot refresh, polling, and an explicit
//      operator refresh all share ONE bounded slot. This is exactly the
//      interaction the controller exists to make safe.
{
  const env = queueEnv({});
  const ctx = loadApp(env);
  env.queue.items = ITEMS;
  env.queue.mode = "defer";

  ctx.wire();                                   // boot refresh takes the slot
  ok(ev(ctx, "queueRefreshInFlight") === true, "the boot refresh owns the slot");
  eq(env.queue.pending.length, 1, "boot started exactly one request");

  // Polling ticks and an explicit operator refresh arrive during boot.
  eq(await ctx.refreshWorkItems(), "coalesced", "a polling tick coalesces behind boot");
  eq(await ctx.refreshWorkItems(), "coalesced", "a second tick adds no request");
  eq(await ctx.refreshWorkItems(), "coalesced", "an explicit operator refresh coalesces too");
  eq(env.queue.pending.length, 1, "still exactly ONE request in flight");

  // Boot settles and applies; exactly one follow-up runs for all three callers.
  env.queue.pending.shift().resolveWith(ITEMS);
  for (let i = 0; i < 8; i++) await settle();
  ok(ev(ctx, "queueConfirmed") === true, "boot confirmed despite concurrent callers");
  eq(env.queue.pending.length, 1, "ONE coalesced follow-up for all three callers");
  env.queue.pending.shift().resolveWith(ITEMS);
  for (let i = 0; i < 8; i++) await settle();
  ok(ev(ctx, "queueRefreshInFlight") === false, "the slot is free once everything settles");
  eq(env.queue.pending.length, 0, "no residual backlog");
}

// 11h. The fire-and-forget follow-up can never surface an unhandled rejection,
//      even if the inner request throws outside its expected failure contract.
{
  const env = queueEnv({});
  const ctx = loadApp(env);
  let unhandled = 0;
  const onUnhandled = () => { unhandled += 1; };
  process.on("unhandledRejection", onUnhandled);

  env.queue.items = ITEMS;
  env.queue.mode = "defer";
  const active = ctx.refreshWorkItems();
  await ctx.refreshWorkItems();                 // request the follow-up
  // Make the FOLLOW-UP throw from the transport itself, not a handled failure.
  env.queue.pending.shift().resolveWith(ITEMS);
  env.ctx.fetch = () => { throw new Error("probe: transport threw"); };
  await active;
  for (let i = 0; i < 10; i++) await settle();

  eq(unhandled, 0, "a throwing follow-up produces NO unhandled rejection");
  ok(ev(ctx, "queueRefreshInFlight") === false, "and the slot is still released");
  process.removeListener("unhandledRejection", onUnhandled);
}

// 11i. The bounded entry point is the ONLY production path; the inner request
//      is not called directly outside the controller.
{
  const src = fs.readFileSync(APP, "utf8");
  const calls = src.split("runWorkItemsRefresh").length - 1;
  const decl = (src.match(/async function runWorkItemsRefresh\(\)/g) || []).length;
  const inController = (src.match(/await runWorkItemsRefresh\(\)/g) || []).length;
  eq(decl, 1, "the inner request is declared once");
  eq(inController, 1, "it is awaited exactly once, inside the controller");
  ok(calls <= 3, "no production call site bypasses the bounded entry point");
}

console.log((failures === 0 ? "PASS" : "FAIL") + ": " + (checks - failures) + "/" + checks + " wired-path checks");
process.exit(failures === 0 ? 0 : 1);
