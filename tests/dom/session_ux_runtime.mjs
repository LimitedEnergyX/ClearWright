/*
 * Runtime coverage for the session-continuity UX logic.
 *
 * Both reviewers correctly observed that static assertions over app.js cannot
 * catch the defects that actually occurred in this slice: a scroll listener
 * bound to an element that never scrolls, a rank bucket nothing can produce,
 * and a target shape the server cannot validate. This harness EXECUTES the real
 * app.js against a controllable DOM stub and asserts behaviour.
 *
 * Deliberately dependency-free: no package.json, no npm install, no browser. It
 * runs on the Node already present in CI.
 *
 * STATED LIMITATION, so this is not read as more than it is: the stub supplies
 * scroll geometry rather than computing layout, so it proves the DECISION LOGIC
 * given a geometry, not that a real browser produces that geometry. The
 * geometry used below is the one observed in the running console (a
 * non-scrolling #conv-detail inside a scrolling page), which is exactly the
 * case that made the feature inert.
 */
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const APP = path.join(HERE, "..", "..", "apps", "control-plane", "static", "app.js");

let failures = 0;
let checks = 0;

function ok(cond, label) {
  checks += 1;
  if (!cond) {
    failures += 1;
    console.error("FAIL: " + label);
  }
}

function eq(actual, expected, label) {
  ok(JSON.stringify(actual) === JSON.stringify(expected),
     label + "  (got " + JSON.stringify(actual) + ", want " + JSON.stringify(expected) + ")");
}

// --------------------------------------------------------------------------
// Minimal DOM stub. Only what app.js touches at load plus the elements the
// functions under test read. Elements report the geometry we give them.
// --------------------------------------------------------------------------
function makeEl(id, opts) {
  const o = opts || {};
  const el = {
    id: id,
    hidden: o.hidden === undefined ? false : o.hidden,
    scrollTop: o.scrollTop || 0,
    scrollHeight: o.scrollHeight || 0,
    clientHeight: o.clientHeight || 0,
    style: {},
    _overflowY: o.overflowY || "visible",
    parentElement: null,
    children: [],
    classList: {
      _s: new Set(),
      add(c) { this._s.add(c); },
      remove(c) { this._s.delete(c); },
      toggle(c, on) { if (on) this._s.add(c); else this._s.delete(c); },
      contains(c) { return this._s.has(c); },
    },
    _attrs: {},
    setAttribute(k, v) { this._attrs[k] = String(v); },
    getAttribute(k) { return k in this._attrs ? this._attrs[k] : null; },
    hasAttribute(k) { return k in this._attrs; },
    removeAttribute(k) { delete this._attrs[k]; },
    addEventListener(type, fn) { (this._ev = this._ev || {})[type] = fn; },
    removeEventListener() {},
    appendChild(c) { this.children.push(c); c.parentElement = this; return c; },
    removeChild(c) { this.children = this.children.filter((x) => x !== c); return c; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    contains() { return false; },
    focus() {},
    insertBefore(c) { this.children.push(c); return c; },
    remove() {},
    closest() { return null; },
    matches() { return false; },
    dataset: {},
    value: "",
    disabled: false,
    tabIndex: 0,
    inert: false,
    checked: false,
    options: [],
    reportValidity() { return true; },
    setCustomValidity() {},
    click() { if (this._ev && this._ev.click) this._ev.click({}); },
    scrollIntoView() {},
    get textContent() { return this._text || ""; },
    set textContent(v) { this._text = v; this.children = []; },
    get innerHTML() { return this._html || ""; },
    set innerHTML(v) { this._html = v; },
  };
  return el;
}

function buildContext(registry, hash, absent) {
  const missing = new Set(absent || []);
  const doc = {
    _els: registry,
    // Auto-vivify unknown ids so unrelated render paths cannot null-deref and
    // mask the behaviour under test. Ids in `absent` stay genuinely missing so
    // fallback chains (e.g. #conv-scroll -> #conv-detail) are exercised.
    getElementById(id) {
      if (missing.has(id)) return null;
      if (!registry[id]) registry[id] = makeEl(id);
      return registry[id];
    },
    createElement(tag) { return makeEl("<" + tag + ">"); },
    createTextNode(t) { return { nodeValue: t }; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    addEventListener() {},
    body: makeEl("body"),
    documentElement: makeEl("html"),
    scrollingElement: registry.__page,
  };
  const ctx = {
    console,
    document: doc,
    location: { hash: hash || "", pathname: "/", search: "", href: "http://x/" },
    history: { replaceState(_a, _b, _c) { ctx.location.hash = ""; } },
    localStorage: {
      _m: {},
      getItem(k) { return k in this._m ? this._m[k] : null; },
      setItem(k, v) { this._m[k] = String(v); },
      removeItem(k) { delete this._m[k]; },
    },
    getComputedStyle(el) { return { overflowY: el._overflowY || "visible", textTransform: "none" }; },
    MutationObserver: function (fn) { this._fn = fn; this.observe = () => {}; this.disconnect = () => {}; },
    HTMLElement: { prototype: { inert: true } },
    setTimeout() { return 0; },
    setInterval() { return 0; },
    clearTimeout() {},
    fetch() { return Promise.resolve({ ok: true, json: () => Promise.resolve({}) }); },
    CSS: { escape: (s) => s },
    Node: function () {},
  };
  ctx.window = ctx;
  ctx.globalThis = ctx;
  return ctx;
}

// app.js keeps its state in top-level let/const, which in a vm context lives in
// the script's lexical scope rather than on the context object. Reading or
// assigning ctx.<name> would silently address a DIFFERENT binding, so all state
// access goes through the context's own scope.
function evalIn(ctx, code) {
  return vm.runInContext(code, ctx);
}

function loadApp(registry, hash, absent) {
  let src = fs.readFileSync(APP, "utf8");
  // Drop the boot invocation: this harness exercises the module's functions
  // directly rather than starting the whole console.
  src = src.replace(/\nwire\(\);\s*\nrefresh\(\);\s*$/, "\n");
  if (/\nwire\(\);/.test(src)) {
    throw new Error("boot invocation still present after stripping");
  }
  const ctx = buildContext(registry, hash, absent);
  vm.createContext(ctx);
  vm.runInContext(src, ctx, { filename: "app.js" });
  return ctx;
}

function baseRegistry() {
  // The geometry observed in the running console: #conv-detail does NOT scroll
  // (overflow visible, scrollHeight === clientHeight); the page does.
  const page = makeEl("__page", { scrollHeight: 2000, clientHeight: 800, scrollTop: 0 });
  const conv = makeEl("conv-detail", { scrollHeight: 634, clientHeight: 634, overflowY: "visible" });
  const reg = {
    __page: page,
    "conv-detail": conv,
    "jump-to-latest": makeEl("jump-to-latest", { hidden: true }),
    "restore-status": makeEl("restore-status", { hidden: true }),
    "session-rail": makeEl("session-rail", { hidden: true }),
    "composer-card": makeEl("composer-card"),
    "conv-banner": makeEl("conv-banner"),
    "operator-chat-input": makeEl("operator-chat-input"),
  };
  return reg;
}

// --------------------------------------------------------------------------
// 1. The scroll target must be the element that ACTUALLY scrolls.
//    This is the defect that made Jump to latest inert twice.
// --------------------------------------------------------------------------
{
  const reg = baseRegistry();
  const ctx = loadApp(reg, "", ["conv-scroll", "conversation"]);
  const scroller = ctx.conversationScrollEl();
  ok(scroller !== reg["conv-detail"],
     "conversationScrollEl must not return the non-scrolling conversation container");
  ok(scroller === reg.__page,
     "conversationScrollEl falls back to the page when no ancestor scrolls");

  // With the page scrolled to the top, the operator IS away from the newest
  // message; at the bottom they are not.
  reg.__page.scrollTop = 0;
  ok(ctx.operatorMovedAwayFromLatest(scroller) === true,
     "scrolled to top counts as deliberately away from latest");
  reg.__page.scrollTop = reg.__page.scrollHeight - reg.__page.clientHeight;
  ok(ctx.operatorMovedAwayFromLatest(scroller) === false,
     "at the bottom the operator is not away from latest");

  // Had the old code been kept, this is what it would have reported.
  ok(ctx.operatorMovedAwayFromLatest(reg["conv-detail"]) === false,
     "the non-scrolling container can never report a scroll position");

  // jumpToLatestMessage must move the real scroller and hide the control.
  reg.__page.scrollTop = 0;
  reg["jump-to-latest"].hidden = false;
  ctx.jumpToLatestMessage();
  ok(reg.__page.scrollTop === reg.__page.scrollHeight,
     "jumpToLatestMessage scrolls the real scroller to the end");
  ok(reg["jump-to-latest"].hidden === true,
     "jumpToLatestMessage hides the control");
}

// --------------------------------------------------------------------------
// 2. A malformed route must never throw. An exception here aborted boot.
// --------------------------------------------------------------------------
for (const bad of ["#work=%", "#work=%E0%A4%A", "#work=abc&msg=%", "#work=%%%"]) {
  const reg = baseRegistry();
  const ctx = loadApp(reg, bad, ["conv-scroll", "conversation"]);
  let threw = null;
  try {
    ctx.applyWorkHashRoute();
  } catch (e) {
    threw = String(e);
  }
  ok(threw === null, "applyWorkHashRoute must not throw on " + bad + " (threw " + threw + ")");

  const parsed = ctx.parseWorkRoute(bad);
  ok(parsed !== null, "parseWorkRoute returns a result for " + bad);
  if (bad !== "#work=abc&msg=%") {
    ok(parsed.malformed === true, "parseWorkRoute flags " + bad + " as malformed");
  } else {
    // A malformed msg fragment must not discard an otherwise valid work id.
    ok(parsed.malformed === false && parsed.work_item_id === "abc" && parsed.message_id === null,
       "a bad msg fragment degrades to no highlight, keeping the valid work id");
  }
}

// A well-formed route still parses correctly.
{
  const reg = baseRegistry();
  const ctx = loadApp(reg, "#work=message%3Amsg-1&msg=msg-1", ["conv-scroll", "conversation"]);
  const p = ctx.parseWorkRoute("#work=message%3Amsg-1&msg=msg-1");
  eq([p.malformed, p.work_item_id, p.message_id], [false, "message:msg-1", "msg-1"],
     "a valid route decodes both ids");
}

// --------------------------------------------------------------------------
// 2b. BOOT ORDERING. applyWorkHashRoute() runs before the queue loads and
//     clears the bad hash, so restoreActiveSelection() can no longer see it.
//     The reported explanation must therefore survive, and the malformed route
//     must not leave a selection bound. This is the exact interaction the
//     previous harness missed by calling applyWorkHashRoute() in isolation.
// --------------------------------------------------------------------------
{
  const reg = baseRegistry();
  const ctx = loadApp(reg, "#work=%", ["conv-scroll", "conversation"]);

  // Pretend a previous session stored a selection, as a real reload would.
  evalIn(ctx, 'localStorage.setItem("cw_selected_work_item_v1", "message:msg-prior");' +
              'lastWorkItems = [{ work_item_id: "message:msg-prior", thread_id: "thr-prior",' +
              ' presentation_state: "needs_operator" }];');

  ctx.applyWorkHashRoute();
  ok(evalIn(ctx, "selectedWorkItemId") === null,
     "a malformed route clears the active selection at boot");
  ok(evalIn(ctx, 'localStorage.getItem("cw_selected_work_item_v1")') === null,
     "a malformed route clears the persisted selection at boot");
  ok(ctx.location.hash === "", "the malformed route is removed from the URL");
  const reported = reg["restore-status"].textContent;
  ok(reported.indexOf("could not be read") !== -1,
     "the malformed route is explained to the operator");
  ok(reg["restore-status"].hidden === false, "the explanation is visible");

  // Now the boot success path runs, exactly as wire() does.
  ctx.clearTransientRestoreStatus();
  ok(reg["restore-status"].hidden === false,
     "the route explanation SURVIVES the boot success path (was erased before)");
  ok(reg["restore-status"].textContent.indexOf("could not be read") !== -1,
     "the surviving message is still the route explanation");

  // And the message must not contradict what restoration then does.
  ctx.restoreActiveSelection();
  const restored = evalIn(ctx, "selectedWorkItemId");
  ok(reported.indexOf("nothing is selected") === -1 || restored === null,
     "the boot message must not claim nothing is selected while restoration binds one");
}

// 2c. An EMPTY work id is an invalid route, not the absence of one.
{
  const reg = baseRegistry();
  const ctx = loadApp(reg, "#work=", ["conv-scroll", "conversation"]);
  const p = ctx.parseWorkRoute("#work=");
  ok(p !== null, "an empty work id is recognised as a route");
  ok(p.malformed === true, "an empty work id is classified invalid, not absent");
}

// 2d. A transient status is still clearable when no route error occurred.
{
  const reg = baseRegistry();
  const ctx = loadApp(reg, "", ["conv-scroll", "conversation"]);
  ctx.showRestoreStatus("transient");
  ok(reg["restore-status"].hidden === false, "a transient status shows");
  ctx.clearTransientRestoreStatus();
  ok(reg["restore-status"].hidden === true,
     "a transient status clears when no route error was reported");
}

// --------------------------------------------------------------------------
// 2e. HASHCHANGE, not just boot. applyWorkHashRoute() is also the hashchange
//     path, so route validation and the terminal policy must hold there too.
//     Previously both lived only in restoreActiveSelection(), so a post-boot
//     link could bind and PERSIST an unknown or terminal item with no
//     restoration pass following to correct it.
// --------------------------------------------------------------------------
{
  // Unknown item, queue already loaded (the hashchange case).
  const reg = baseRegistry();
  const ctx = loadApp(reg, "#work=message%3Amsg-ghost", ["conv-scroll", "conversation"]);
  evalIn(ctx, 'lastWorkItems = [{ work_item_id: "message:msg-real", thread_id: "thr-real",' +
              ' presentation_state: "needs_operator" }];');
  ctx.applyWorkHashRoute();
  ok(evalIn(ctx, "selectedWorkItemId") === null,
     "a hashchange to an unknown item leaves no queue-unbacked selection");
  ok(evalIn(ctx, 'localStorage.getItem("cw_selected_work_item_v1")') === null,
     "a hashchange to an unknown item persists nothing");
  ok(reg["restore-status"].textContent.indexOf("not in the live queue") !== -1,
     "the unknown hashchange route is explained");
}

{
  // Terminal item, queue already loaded: openable, but never persisted.
  const reg = baseRegistry();
  const ctx = loadApp(reg, "#work=message%3Amsg-done", ["conv-scroll", "conversation"]);
  evalIn(ctx, 'lastWorkItems = [{ work_item_id: "message:msg-done", thread_id: "thr-done",' +
              ' presentation_state: "recently_completed" }];');
  ctx.applyWorkHashRoute();
  ok(evalIn(ctx, "selectedWorkItemId") === "message:msg-done",
     "an explicit link may OPEN a terminal item for inspection");
  ok(evalIn(ctx, 'localStorage.getItem("cw_selected_work_item_v1")') === null,
     "a terminal item is never persisted as the active selection");
  ok(reg["restore-status"].textContent.indexOf("inspection") !== -1,
     "the inspection-only status is explained");
}

{
  // Active item, queue already loaded: bound and persisted normally.
  const reg = baseRegistry();
  const ctx = loadApp(reg, "#work=message%3Amsg-live", ["conv-scroll", "conversation"]);
  evalIn(ctx, 'lastWorkItems = [{ work_item_id: "message:msg-live", thread_id: "thr-live",' +
              ' presentation_state: "needs_operator" }];');
  ctx.applyWorkHashRoute();
  eq([evalIn(ctx, "selectedWorkItemId"), evalIn(ctx, "selectedConvThread")],
     ["message:msg-live", "thr-live"],
     "an active route binds the item and its durable thread");
  ok(evalIn(ctx, 'localStorage.getItem("cw_selected_work_item_v1")') === "message:msg-live",
     "an active route IS persisted");
}

// 2f. The route-error latch must not outlive the error.
{
  const reg = baseRegistry();
  const ctx = loadApp(reg, "#work=%", ["conv-scroll", "conversation"]);
  evalIn(ctx, 'lastWorkItems = [{ work_item_id: "message:msg-live", thread_id: "thr-live",' +
              ' presentation_state: "needs_operator" }];');
  ctx.applyWorkHashRoute();                       // reports a malformed route
  ok(evalIn(ctx, "routeErrorReported") === true, "a malformed route latches the explanation");
  ctx.clearTransientRestoreStatus();
  ok(reg["restore-status"].hidden === false, "and it survives the boot success path");

  // A later VALID route is a successful navigation: the stale explanation goes.
  ctx.location.hash = "#work=message%3Amsg-live";
  ctx.applyWorkHashRoute();
  ok(evalIn(ctx, "routeErrorReported") === false,
     "a successful route resets the latch (was one-way before)");
  ctx.showRestoreStatus("transient");
  ctx.clearTransientRestoreStatus();
  ok(reg["restore-status"].hidden === true,
     "transient statuses are clearable again after recovery");
}

{
  // Deliberate operator navigation also supersedes a stale explanation.
  const reg = baseRegistry();
  const ctx = loadApp(reg, "#work=%", ["conv-scroll", "conversation"]);
  evalIn(ctx, 'lastWorkItems = [{ work_item_id: "message:msg-live", thread_id: "thr-live",' +
              ' presentation_state: "needs_operator" }];');
  ctx.applyWorkHashRoute();
  ok(evalIn(ctx, "routeErrorReported") === true, "latched after a malformed route");
  ctx.navigateToWorkItem("message:msg-live");
  ok(evalIn(ctx, "routeErrorReported") === false,
     "explicit navigation clears the stale route explanation");
}

// --------------------------------------------------------------------------
// 3. Ranking: every ranked bucket reachable, unknown last, deterministic ties.
// --------------------------------------------------------------------------
{
  const reg = baseRegistry();
  const ctx = loadApp(reg, "", ["conv-scroll", "conversation"]);

  eq(ctx.activeStateOf({ presentation_state: "needs_operator" }), "waiting_for_operator",
     "needs_operator maps to waiting_for_operator");
  eq(ctx.activeStateOf({ last_activity_event: "operator_message" }), "operator_message_posted",
     "an operator message is reachable as its own rank (was unreachable)");
  eq(ctx.activeStateOf({ presentation_state: "totally_new_state" }), "",
     "an unrecognised state is NOT guessed as in_council");

  ok(evalIn(ctx, 'ACTIVE_RANK.indexOf("wake_pending")') === -1,
     "wake_pending is not in the executable rank");
  ok(evalIn(ctx, "ACTIVE_RANK.length") > 0, "ACTIVE_RANK is readable and non-empty");

  // Unknown states must sort AFTER every known state.
  const items = [
    { work_item_id: "w-unknown", presentation_state: "brand_new" },
    { work_item_id: "w-blocked", presentation_state: "blocked" },
    { work_item_id: "w-operator", presentation_state: "needs_operator" },
  ];
  const ranked = ctx.rankActiveWorkItems(items).map((i) => i.work_item_id);
  eq(ranked, ["w-operator", "w-blocked", "w-unknown"],
     "unknown states sort last, operator-waiting first");

  // Deterministic tie-break when rank and timestamp are equal.
  const tied = [
    { work_item_id: "w-b", presentation_state: "blocked", last_activity_at: "" },
    { work_item_id: "w-a", presentation_state: "blocked", last_activity_at: "" },
  ];
  eq(ctx.rankActiveWorkItems(tied).map((i) => i.work_item_id), ["w-a", "w-b"],
     "equal rank and timestamp resolve deterministically by work_item_id");
  eq(ctx.rankActiveWorkItems(tied.slice().reverse()).map((i) => i.work_item_id), ["w-a", "w-b"],
     "the tie-break does not depend on input order");

  // Terminal items are never auto-ranked.
  const terminal = [{ work_item_id: "w-done", presentation_state: "complete" }];
  eq(ctx.rankActiveWorkItems(terminal).length, 0,
     "terminal items are excluded from automatic restoration");
}

// --------------------------------------------------------------------------
// 4. Composer target fails closed without a durable thread.
// --------------------------------------------------------------------------
{
  const reg = baseRegistry();
  const ctx = loadApp(reg, "", ["conv-scroll", "conversation"]);

  evalIn(ctx, 'lastWorkItems = [{ work_item_id: "message:msg-1", thread_id: "thr-1" }];' +
              'selectedWorkItemId = "message:msg-1"; selectedConvThread = null;');
  const bound = ctx.convComposerTarget();
  eq([bound.work_item_id, bound.thread_id, !!bound.unresolved],
     ["message:msg-1", "thr-1", false],
     "a known item binds work item and durable thread together");

  // Same selection, but the queue has no thread for it.
  evalIn(ctx, 'lastWorkItems = [{ work_item_id: "message:msg-2", thread_id: null }];' +
              'selectedWorkItemId = "message:msg-2"; selectedConvThread = null;');
  const unresolved = ctx.convComposerTarget();
  ok(unresolved.unresolved === true,
     "a selection with no durable thread reports unresolved");
  ok(unresolved.thread_id === null,
     "the unresolved target carries no thread");
  ok(!(unresolved.work_item_id && unresolved.thread_id),
     "a work_item_id is never paired with a fabricated thread");

  // An item absent from the queue entirely is also unresolved, never sendable.
  evalIn(ctx, 'lastWorkItems = []; selectedWorkItemId = "message:msg-missing";' +
              "selectedConvThread = null;");
  ok(ctx.convComposerTarget().unresolved === true,
     "an item missing from the queue is unresolved, not sendable");
}

// --------------------------------------------------------------------------
// Result
// --------------------------------------------------------------------------
console.log((failures === 0 ? "PASS" : "FAIL") + ": " + (checks - failures) + "/" + checks + " runtime checks");
process.exit(failures === 0 ? 0 : 1);
