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

function seed(ctx, items) {
  ev(ctx, "workItemsLoaded = true; lastWorkItems = " + JSON.stringify(items || ITEMS) + ";");
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
// 5. THE REAL send() PATH refuses through every destination-integrity branch.
// ---------------------------------------------------------------------------
function sendEnv(hash) {
  const env = buildEnv({
    hash,
    responder: (url) => {
      if (url.indexOf("/api/messages?") === 0 || url.indexOf("message_id=") !== -1) {
        return { found: true, message: { message: "PROBE" } };
      }
      return { ok: true, message_id: "msg-new", thread_id: "thr-alpha" };
    },
  });
  const ctx = loadApp(env);
  seed(ctx);
  ctx.wire();
  ctx.renderQueue();
  return { env, ctx };
}

function attemptSend(ctx, env, text) {
  const ta = env.doc.getElementById("conv-input");
  ta.value = text || "PROBE";
  const before = env.posted.filter((p) => p.method === "POST").length;
  ev(ctx, "convComposer.send()");
  const after = env.posted.filter((p) => p.method === "POST").length;
  return { posts: after - before, draft: ta.value };
}

// 5a. Unresolved destination: selected item has no durable thread.
{
  const { env, ctx } = sendEnv("#work=message%3Amsg-nothread");
  ev(ctx, 'workItemsLoaded = true; lastWorkItems = [{ work_item_id: "message:msg-nothread",' +
          ' thread_id: null, presentation_state: "needs_operator" }];' +
          'selectedWorkItemId = "message:msg-nothread"; selectedConvThread = null;');
  const r = attemptSend(ctx, env);
  eq(r.posts, 0, "unresolved destination emits NO request");
  ok(r.draft.length > 0, "unresolved destination preserves the draft");
  const err = env.doc.getElementById("conv-error");
  ok(String(err.textContent).length > 0, "unresolved destination explains itself");
}

// 5b. Route names a DIFFERENT work item than the selection.
{
  const { env, ctx } = sendEnv("#work=message%3Amsg-beta");
  ev(ctx, 'selectedWorkItemId = "message:msg-alpha"; selectedConvThread = "thr-alpha";');
  const r = attemptSend(ctx, env);
  eq(r.posts, 0, "route/selection disagreement emits NO request");
  ok(r.draft.length > 0, "disagreement preserves the draft");
  ok(String(env.doc.getElementById("conv-error").textContent).indexOf("different work item") !== -1,
     "disagreement names the mismatch");
}

// 5c. ABSENT route: nothing proves the URL agrees.
{
  const { env, ctx } = sendEnv("");
  ev(ctx, 'selectedWorkItemId = "message:msg-alpha"; selectedConvThread = "thr-alpha";');
  const r = attemptSend(ctx, env);
  eq(r.posts, 0, "an absent route emits NO request");
  ok(String(env.doc.getElementById("conv-error").textContent).indexOf("no work route") !== -1,
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
  const r = attemptSend(ctx, env);
  eq(r.posts, 0, "a malformed route emits NO request");
  ok(String(env.doc.getElementById("conv-error").textContent).indexOf("unreadable") !== -1,
     "a malformed route says the URL is unreadable");
}

// 5e. VALID RETRY after a refusal succeeds, and the button state is restored.
{
  const { env, ctx } = sendEnv("");
  ev(ctx, 'selectedWorkItemId = "message:msg-alpha"; selectedConvThread = "thr-alpha";');
  const btn = env.doc.getElementById("conv-send");
  const idle = btn.textContent;

  const refused = attemptSend(ctx, env);
  eq(refused.posts, 0, "the first attempt is refused");
  eq(btn.textContent, idle, "the button label is unchanged by a refusal");
  ok(!btn.disabled, "the button is re-enabled after a refusal");

  // Correct the route and retry: the same draft must now be sendable.
  ctx.location.hash = "#work=" + encodeURIComponent("message:msg-alpha");
  const retry = attemptSend(ctx, env);
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

console.log((failures === 0 ? "PASS" : "FAIL") + ": " + (checks - failures) + "/" + checks + " wired-path checks");
process.exit(failures === 0 ? 0 : 1);
