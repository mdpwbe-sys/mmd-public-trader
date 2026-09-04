"use strict";
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const html = fs.readFileSync("gui/index.html", "utf8");
const script = html.match(/<script[^>]*>([\s\S]*?)<\/script>/i);
assert(script, "script GUI introuvable");

function element() {
  const classNames = new Set();
  const base = {
    style: { display: "none" }, dataset: {}, innerHTML: "", textContent: "",
    classList: {
      add: function (name) { classNames.add(name); },
      remove: function (name) { classNames.delete(name); },
      contains: function (name) { return classNames.has(name); },
      toggle: function (name) { return classNames.has(name) ? classNames.delete(name) : classNames.add(name); }
    },
    listeners: {},
    addEventListener: function (type, callback) {
      (this.listeners[type] = this.listeners[type] || []).push(callback);
    },
    removeEventListener: function () {}, appendChild: function (child) { (this.children = this.children || []).push(child); },
    setAttribute: function () {}, querySelector: function () { return null; },
    querySelectorAll: function () { return []; }, contains: function () { return false; },
    getBoundingClientRect: function () {
      return { left: 0, top: 0, width: 100, height: 100 };
    }
  };
  return new Proxy(base, {
    get: function (target, key) {
      return key in target ? target[key] : "";
    }
  });
}

const titlebar = element();
const controls = element();
titlebar.querySelector = function (selector) {
  return selector === ".win-controls" ? controls : null;
};
const marginOverlay = element();
const settingsOverlay = element();
const localOverlay = element();
const elements = {
  "margin-overlay": marginOverlay,
  "margin-body": element(),
  "margin-title": element(),
  "settings-overlay": settingsOverlay,
  "settings-body": element(),
  "local-intel-overlay": localOverlay,
  "local-intel-body": element(),
  "local-intel-title": element(),
  "logstream": element(),
  "win-pin-btn": element()
};
const documentListeners = {};
const document = {
  readyState: "loading",
  body: element(),
  addEventListener: function (type, callback) {
    (documentListeners[type] = documentListeners[type] || []).push(callback);
  },
  removeEventListener: function () {},
  getElementById: function (id) { return elements[id] || element(); },
  createElement: function () { return element(); },
  querySelector: function (selector) {
    if (selector === ".titlebar") return titlebar;
    if (selector === ".modal-overlay") return marginOverlay;
    return null;
  },
  querySelectorAll: function (selector) {
    return selector === ".modal-overlay" ? [marginOverlay, settingsOverlay] : [];
  }
};
const windowListeners = {};
const calls = [];
const api = {
  start_native_drag: function () { calls.push(["start"]); },
  stop_native_drag: function () { calls.push(["stop"]); },
  set_topmost: function (value) { calls.push(["topmost", value]); },
  set_window_topmost: function (value) { calls.push(["restore", value]); },
  move_window: function () { calls.push(["move"]); },
  move_window_physical: function () { calls.push(["move-physical"]); },
  disconnect_eve: function () {}
};
const window = {
  document, pywebview: { api },
  addEventListener: function (type, callback) {
    (windowListeners[type] = windowListeners[type] || []).push(callback);
  }
};
window.window = window;
const context = {
  window, document, console,
  getComputedStyle: function (node) { return node.style; },
  localStorage: { getItem: function () { return null; }, setItem: function () {} },
  navigator: {}, performance: { now: function () { return 0; } },
  setInterval: function () { return 1; }, clearInterval: function () {},
  setTimeout: function () { return 1; }, requestAnimationFrame: function () {},
  alert: function () {}, confirm: function () { return true; }
};
vm.createContext(context);
vm.runInContext(script[1], context);

context.makeAppTitlebarDraggable();
context.makeAppTitlebarDraggable();
assert.strictEqual(titlebar.listeners.mousedown.length, 1);
assert.strictEqual(/\b(?:api|a)\.move_window(?:_physical)?\s*\(/.test(script[1]), false);

const outsideEvent = {
  target: { closest: function () { return null; } },
  preventDefault: function () {}, stopPropagation: function () {}
};
(documentListeners.mousedown || []).forEach(callback => callback(outsideEvent));
(document.body.listeners.mousedown || []).forEach(callback => callback(outsideEvent));
(windowListeners.mousedown || []).forEach(callback => callback(outsideEvent));
assert.strictEqual(calls.some(call => call[0] === "start"), false);
assert.strictEqual(calls.some(call => call[0] === "move"), false);
assert.strictEqual(calls.some(call => call[0] === "move-physical"), false);

const mousedown = titlebar.listeners.mousedown[0];
const mouseEvent = {
  target: { closest: function () { return null; } },
  preventDefault: function () {}
};

// Settings est le deuxieme overlay : l'ancien querySelector seul le ratait.
marginOverlay.style.display = "none";
settingsOverlay.style.display = "flex";
mousedown(mouseEvent);
assert.strictEqual(calls.some(call => call[0] === "start"), false);

settingsOverlay.style.display = "none";
mousedown(mouseEvent);
assert.strictEqual(calls.filter(call => call[0] === "start").length, 1);
context.stopNativeDrag(false);

context.showSettings();
context.closeSettings();
context.showMargin({ ok: false, reason: "test" });
context.closeMargin();
context.showLocalIntel({ ok: true, state: "ready", total: 1, pilots: [] });
assert.strictEqual(localOverlay.style.display, "flex");
assert(document.body.classList.contains("local-intel-mode"));
assert.strictEqual(document.body.classList.contains("margin-mode-hud"), false);
context.closeLocalIntel();

let focusedSystem = null;
window.MapWorkspace = { focusSystem: function (id) { focusedSystem = id; } };
context.logLine('INTEL', 'watch', null, 'https://zkillboard.com/kill/42/', {
  system_id: 30000142, system_name: 'Jita', killmail_id: 42,
  kill_url: 'https://zkillboard.com/kill/42/', ship_name: 'Rifter', attacker_count: 2,
});
const combatRow = elements.logstream.children[0];
assert.match(combatRow.innerHTML, /Jita/);
assert.match(combatRow.innerHTML, /Rifter détruit/);
assert.doesNotMatch(combatRow.innerHTML, /\[KM\]/, 'le lien du kill est intégré au vaisseau détruit');
(documentListeners.click || []).forEach(callback => callback({
  preventDefault: function () {},
  target: { closest: function (selector) { return selector.includes('log-combat-action') ? { dataset: { combatSystemId: '30000142' } } : null; } },
}));
assert.strictEqual(focusedSystem, 30000142, 'le système du journal ouvre le focus carte');

assert.strictEqual(settingsOverlay.style.display, "none");
assert.strictEqual(marginOverlay.style.display, "none");
assert.strictEqual(localOverlay.style.display, "none");
assert.strictEqual(document.body.classList.contains("local-intel-mode"), false);
assert(calls.some(call => call[0] === "topmost" && call[1] === true));
assert(calls.some(call => call[0] === "restore" && call[1] === false));
assert.strictEqual(calls.some(call => call[0] === "move"), false);
assert.strictEqual(calls.some(call => call[0] === "move-physical"), false);
assert(calls.filter(call => call[0] === "stop").length >= 5);
console.log("popup stability: all overlays guarded, drag stopped, no move on close");
