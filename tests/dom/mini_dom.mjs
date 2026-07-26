/*
 * A small but REAL DOM: parsing, tree, selectors, focus and event propagation.
 *
 * The previous harness called app.js functions directly. Both reviewers
 * correctly said that proves the helpers, not the wired path -- a click on a
 * rendered control reaching the delegated listener, Enter and Space activating
 * a native button, and focus surviving a polling cycle are exactly the things
 * direct calls cannot demonstrate. This module supplies enough real DOM
 * behaviour to install wire() and dispatch genuine events.
 *
 * Dependency-free: Node builtins only, no package manifest, no browser driver.
 *
 * STATED LIMITATION: this is not a browser. It implements markup parsing, the
 * element tree, a useful subset of CSS selectors, focus tracking, capture/target/
 * bubble event propagation, and native <button> Enter/Space activation. It does
 * NOT implement layout, painting, or real scrolling, so geometry-dependent
 * behaviour is still proven only against supplied values.
 */

const VOID_TAGS = new Set(["br", "hr", "img", "input", "meta", "link"]);

class ClassList {
  constructor(el) { this.el = el; }
  _set() {
    const v = this.el.getAttribute("class") || "";
    return new Set(v.split(/\s+/).filter(Boolean));
  }
  _write(s) { this.el.setAttribute("class", Array.from(s).join(" ")); }
  add(c) { const s = this._set(); s.add(c); this._write(s); }
  remove(c) { const s = this._set(); s.delete(c); this._write(s); }
  toggle(c, on) { if (on === undefined ? !this.contains(c) : on) this.add(c); else this.remove(c); }
  contains(c) { return this._set().has(c); }
  get value() { return this.el.getAttribute("class") || ""; }
}

export class MiniEvent {
  constructor(type, init) {
    init = init || {};
    this.type = type;
    this.bubbles = init.bubbles !== false;
    this.cancelable = init.cancelable !== false;
    this.key = init.key;
    this.code = init.code;
    this.ctrlKey = !!init.ctrlKey;
    this.metaKey = !!init.metaKey;
    this.shiftKey = !!init.shiftKey;
    this.defaultPrevented = false;
    this.isTrusted = !!init.isTrusted;
    this.target = null;
    this.currentTarget = null;
    this._stopped = false;
  }
  preventDefault() { if (this.cancelable) this.defaultPrevented = true; }
  stopPropagation() { this._stopped = true; }
}

export class MiniElement {
  constructor(tag, doc) {
    this.tagName = String(tag).toUpperCase();
    this.ownerDocument = doc;
    this.childNodes = [];
    this.parentNode = null;
    this._attrs = {};
    this._listeners = {};
    this._text = "";
    this.scrollTop = 0;
    this.scrollHeight = 0;
    this.clientHeight = 0;
    this.style = {};
    this.value = "";
    this.disabled = false;
    this.checked = false;
    this._overflowY = "visible";
  }

  // --- attributes ---------------------------------------------------------
  setAttribute(k, v) { this._attrs[k] = String(v); }
  getAttribute(k) { return Object.prototype.hasOwnProperty.call(this._attrs, k) ? this._attrs[k] : null; }
  hasAttribute(k) { return Object.prototype.hasOwnProperty.call(this._attrs, k); }
  removeAttribute(k) { delete this._attrs[k]; }
  get classList() { return new ClassList(this); }
  get className() { return this.getAttribute("class") || ""; }
  set className(v) { this.setAttribute("class", v); }
  get id() { return this.getAttribute("id") || ""; }
  set id(v) { this.setAttribute("id", v); }
  get hidden() { return this.hasAttribute("hidden"); }
  set hidden(v) { if (v) this.setAttribute("hidden", ""); else this.removeAttribute("hidden"); }
  get tabIndex() {
    if (this.hasAttribute("tabindex")) return parseInt(this.getAttribute("tabindex"), 10);
    return (this.tagName === "BUTTON" || this.tagName === "A" ||
            this.tagName === "INPUT" || this.tagName === "TEXTAREA" ||
            this.tagName === "SELECT") ? 0 : -1;
  }
  set tabIndex(v) { this.setAttribute("tabindex", String(v)); }
  get inert() { return this.hasAttribute("inert"); }
  set inert(v) { if (v) this.setAttribute("inert", ""); else this.removeAttribute("inert"); }
  get dataset() {
    const out = {};
    Object.keys(this._attrs).forEach((k) => {
      if (k.indexOf("data-") === 0) out[k.slice(5).replace(/-([a-z])/g, (m, c) => c.toUpperCase())] = this._attrs[k];
    });
    return out;
  }

  // --- tree ---------------------------------------------------------------
  get children() { return this.childNodes.filter((c) => c instanceof MiniElement); }
  get firstElementChild() { return this.children[0] || null; }
  get parentElement() { return this.parentNode instanceof MiniElement ? this.parentNode : null; }
  appendChild(c) {
    if (c.parentNode) c.parentNode.removeChild(c);
    c.parentNode = this;
    this.childNodes.push(c);
    return c;
  }
  insertBefore(c, ref) {
    if (c.parentNode) c.parentNode.removeChild(c);
    c.parentNode = this;
    const i = ref ? this.childNodes.indexOf(ref) : -1;
    if (i < 0) this.childNodes.push(c); else this.childNodes.splice(i, 0, c);
    return c;
  }
  removeChild(c) {
    const i = this.childNodes.indexOf(c);
    if (i >= 0) this.childNodes.splice(i, 1);
    c.parentNode = null;
    return c;
  }
  replaceChild(next, prev) {
    const i = this.childNodes.indexOf(prev);
    if (i < 0) return prev;
    if (next.parentNode) next.parentNode.removeChild(next);
    this.childNodes[i] = next;
    next.parentNode = this;
    prev.parentNode = null;
    return prev;
  }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  contains(n) {
    while (n) { if (n === this) return true; n = n.parentNode; }
    return false;
  }

  // --- content ------------------------------------------------------------
  set innerHTML(html) {
    this.childNodes = [];
    parseInto(this, String(html), this.ownerDocument);
  }
  get innerHTML() { return this.childNodes.map(serialize).join(""); }
  get outerHTML() { return serialize(this); }
  set textContent(v) { this.childNodes = [{ nodeType: 3, data: String(v) }]; }
  get textContent() { return collectText(this); }
  get innerText() { return this.textContent; }

  // --- selectors ----------------------------------------------------------
  matches(sel) { return selectorMatches(this, sel); }
  closest(sel) {
    let n = this;
    while (n) { if (n instanceof MiniElement && selectorMatches(n, sel)) return n; n = n.parentNode; }
    return null;
  }
  querySelectorAll(sel) {
    const out = [];
    walk(this, (n) => { if (n !== this && selectorMatches(n, sel)) out.push(n); });
    return out;
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }

  // --- events -------------------------------------------------------------
  addEventListener(type, fn, opts) {
    const capture = opts === true || (opts && opts.capture);
    (this._listeners[type] = this._listeners[type] || []).push({ fn, capture: !!capture });
  }
  removeEventListener(type, fn) {
    const l = this._listeners[type];
    if (l) this._listeners[type] = l.filter((e) => e.fn !== fn);
  }
  dispatchEvent(ev) {
    ev.target = ev.target || this;
    const path = [];
    let n = this;
    while (n) { path.push(n); n = n.parentNode; }
    // capture (root -> target)
    for (let i = path.length - 1; i >= 0 && !ev._stopped; i--) fire(path[i], ev, true);
    // bubble (target -> root)
    if (ev.bubbles) {
      for (let i = 0; i < path.length && !ev._stopped; i++) fire(path[i], ev, false);
    } else if (!ev._stopped) {
      fire(this, ev, false);
    }
    return !ev.defaultPrevented;
  }

  focus() {
    const d = this.ownerDocument;
    if (d) d.activeElement = this;
  }
  blur() {
    const d = this.ownerDocument;
    if (d && d.activeElement === this) d.activeElement = d.body;
  }
  click() {
    this.dispatchEvent(new MiniEvent("click", { bubbles: true, isTrusted: true }));
  }
  scrollIntoView() {}
  getBoundingClientRect() { return { width: 100, height: 20, top: 0, left: 0 }; }
}

function fire(node, ev, capture) {
  const l = node._listeners && node._listeners[ev.type];
  if (!l) return;
  ev.currentTarget = node;
  l.slice().forEach((entry) => {
    if (!!entry.capture === !!capture) {
      try { entry.fn.call(node, ev); } catch (e) { /* surfaced by assertions */ }
    }
  });
}

function walk(node, fn) {
  (node.childNodes || []).forEach((c) => {
    if (c instanceof MiniElement) { fn(c); walk(c, fn); }
  });
}

function collectText(node) {
  if (!(node instanceof MiniElement)) return node && node.nodeType === 3 ? node.data : "";
  return (node.childNodes || []).map(collectText).join("");
}

function serialize(node) {
  if (!(node instanceof MiniElement)) return node && node.nodeType === 3 ? node.data : "";
  const attrs = Object.keys(node._attrs)
    .map((k) => " " + k + '="' + node._attrs[k] + '"').join("");
  const tag = node.tagName.toLowerCase();
  if (VOID_TAGS.has(tag)) return "<" + tag + attrs + ">";
  return "<" + tag + attrs + ">" + node.childNodes.map(serialize).join("") + "</" + tag + ">";
}

// --- markup parsing ---------------------------------------------------------
const TAG_RE = /<(\/?)([a-zA-Z][\w-]*)((?:\s+[\w:-]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+))?)*)\s*(\/?)>/g;
const ATTR_RE = /([\w:-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g;

function parseInto(root, html, doc) {
  const stack = [root];
  let last = 0;
  TAG_RE.lastIndex = 0;
  let m;
  while ((m = TAG_RE.exec(html)) !== null) {
    if (m.index > last) {
      const text = html.slice(last, m.index);
      if (text) stack[stack.length - 1].childNodes.push({ nodeType: 3, data: text });
    }
    last = TAG_RE.lastIndex;
    const closing = m[1] === "/";
    const tag = m[2].toLowerCase();
    if (closing) {
      for (let i = stack.length - 1; i > 0; i--) {
        if (stack[i].tagName === tag.toUpperCase()) { stack.length = i; break; }
      }
      continue;
    }
    const el = new MiniElement(tag, doc);
    ATTR_RE.lastIndex = 0;
    let a;
    while ((a = ATTR_RE.exec(m[3] || "")) !== null) {
      if (!a[1]) continue;
      el.setAttribute(a[1], a[2] !== undefined ? a[2] : (a[3] !== undefined ? a[3] : (a[4] !== undefined ? a[4] : "")));
    }
    const parent = stack[stack.length - 1];
    el.parentNode = parent;
    parent.childNodes.push(el);
    if (!VOID_TAGS.has(tag) && m[4] !== "/") stack.push(el);
  }
  if (last < html.length) {
    const text = html.slice(last);
    if (text) stack[stack.length - 1].childNodes.push({ nodeType: 3, data: text });
  }
}

// --- selector matching ------------------------------------------------------
// Supports: tag, #id, .class, [attr], [attr="v"], and comma groups, plus a
// single descendant combinator. Enough for every selector app.js uses.
function selectorMatches(el, sel) {
  if (!(el instanceof MiniElement)) return false;
  return String(sel).split(",").some((part) => matchCompoundChain(el, part.trim()));
}

// Whitespace-aware compound splitter that respects quoted attribute values.
function splitCompounds(sel) {
  const out = [];
  let buf = "", quote = null, esc = false;
  for (const ch of String(sel)) {
    if (esc) { buf += ch; esc = false; continue; }
    if (ch === "\\") { buf += ch; esc = true; continue; }
    if (quote) { buf += ch; if (ch === quote) quote = null; continue; }
    if (ch === '"' || ch === "'") { quote = ch; buf += ch; continue; }
    if (/\s/.test(ch)) { if (buf) { out.push(buf); buf = ""; } continue; }
    buf += ch;
  }
  if (buf) out.push(buf);
  return out;
}

function matchCompoundChain(el, sel) {
  // Split on descendant combinators, but NOT on whitespace inside a quoted
  // attribute value: [data-x="a b"] is one compound, not two.
  const parts = splitCompounds(sel);
  if (!parts.length) return false;
  if (!matchCompound(el, parts[parts.length - 1])) return false;
  let n = el.parentNode;
  for (let i = parts.length - 2; i >= 0; i--) {
    let found = false;
    while (n) {
      if (n instanceof MiniElement && matchCompound(n, parts[i])) { found = true; n = n.parentNode; break; }
      n = n.parentNode;
    }
    if (!found) return false;
  }
  return true;
}

function matchCompound(el, comp) {
  // Attribute values may contain BACKSLASH-ESCAPED characters, which is how
  // a quote or backslash is carried inside a CSS string literal. Matching
  // must unescape them, or a correctly escaped selector would fail here
  // and the harness would report a false negative.
  const re = /(^|\.|#)([\w-]+)|\[([\w-]+)(?:\s*=\s*"((?:[^"\\]|\\.)*)")?\]/g;
  let m, ok = true, any = false;
  while ((m = re.exec(comp)) !== null) {
    any = true;
    if (m[3] !== undefined) {
      if (m[4] !== undefined) {
        const want = m[4].replace(/\\(.)/g, "$1");   // unescape the literal
        if (el.getAttribute(m[3]) !== want) ok = false;
      }
      else if (!el.hasAttribute(m[3])) ok = false;
    } else if (m[1] === ".") {
      if (!el.classList.contains(m[2])) ok = false;
    } else if (m[1] === "#") {
      if (el.getAttribute("id") !== m[2]) ok = false;
    } else {
      if (m[2] !== "*" && el.tagName !== m[2].toUpperCase()) ok = false;
    }
  }
  return any && ok;
}

// --- document ---------------------------------------------------------------
export function createDocument() {
  const doc = {
    createElement(tag) { return new MiniElement(tag, doc); },
    createTextNode(t) { return { nodeType: 3, data: String(t) }; },
    _listeners: {},
    addEventListener(type, fn, opts) {
      const capture = opts === true || (opts && opts.capture);
      (doc._listeners[type] = doc._listeners[type] || []).push({ fn, capture: !!capture });
    },
    removeEventListener() {},
    getElementById(id) { return doc.documentElement.querySelector('[id="' + id + '"]'); },
    querySelector(sel) { return doc.documentElement.querySelector(sel); },
    querySelectorAll(sel) { return doc.documentElement.querySelectorAll(sel); },
  };
  doc.documentElement = new MiniElement("html", doc);
  doc.body = new MiniElement("body", doc);
  doc.documentElement.appendChild(doc.body);
  doc.activeElement = doc.body;
  doc.scrollingElement = doc.documentElement;
  // The document participates in propagation, so delegated listeners installed
  // on `document` (which wire() uses) actually receive dispatched events.
  doc.documentElement.parentNode = {
    _listeners: doc._listeners,
    parentNode: null,
  };
  return doc;
}

/*
 * NATIVE BUTTON KEYBOARD ACTIVATION.
 *
 * A real browser activates a focused <button> on Enter and on Space and fires
 * exactly one click. That default is what an over-eager preventDefault() can
 * suppress, so the harness must model the default rather than assume it, or it
 * could not detect the regression it exists to catch.
 */
export function pressKey(doc, key) {
  const target = doc.activeElement;
  if (!target) return { activated: false, defaultPrevented: false };
  const code = key === " " ? "Space" : (key === "Enter" ? "Enter" : key);
  const down = new MiniEvent("keydown", { key, code, bubbles: true, cancelable: true, isTrusted: true });
  target.dispatchEvent(down);
  const isButton = target.tagName === "BUTTON";
  const activating = isButton && (key === "Enter" || key === " ");
  // The default action runs ONLY if nothing called preventDefault on keydown.
  if (activating && !down.defaultPrevented) {
    target.dispatchEvent(new MiniEvent("click", { bubbles: true, isTrusted: true }));
    return { activated: true, defaultPrevented: false };
  }
  return { activated: false, defaultPrevented: down.defaultPrevented };
}
