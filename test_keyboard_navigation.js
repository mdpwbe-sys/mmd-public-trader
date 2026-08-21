"use strict";
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const html = fs.readFileSync("gui/index.html", "utf8");
const scriptMatch = html.match(/<script[^>]*>([\s\S]*?)<\/script>/i);
assert(scriptMatch, "script GUI introuvable");

const listeners = {};
const rows = [{ scrolled: false }, { scrolled: false }];
rows.forEach(function (row) {
  row.scrollIntoView = function () { row.scrolled = true; };
});

function dummyElement() {
  const base = {
    style: {}, dataset: {}, classList: {
      add: function () {}, remove: function () {}, toggle: function () {}
    },
    addEventListener: function () {}, removeEventListener: function () {},
    appendChild: function () {}, setAttribute: function () {},
    querySelectorAll: function () { return []; },
    contains: function () { return false; },
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

const document = {
  readyState: "loading",
  body: dummyElement(),
  addEventListener: function (type, callback, capture) {
    (listeners[type] = listeners[type] || []).push({ callback, capture });
  },
  removeEventListener: function () {},
  getElementById: function () { return dummyElement(); },
  createElement: function () { return dummyElement(); },
  querySelector: function () { return null; },
  querySelectorAll: function (selector) {
    return selector === "#tbody tr" ? rows : [];
  }
};
const window = {
  document,
  addEventListener: function (type, callback) {
    (listeners["window:" + type] = listeners["window:" + type] || []).push({
      callback, capture: false
    });
  }
};
window.window = window;

const audioSweeps = [];
let audioContextCreates = 0;
let failNextAudio = true;
function FakeAudioContext() {
  audioContextCreates += 1;
  this.currentTime = 10;
  this.state = "suspended";
  this.destination = {};
}
FakeAudioContext.prototype.resume = function () {
  this.state = "running";
  return { catch: function () {} };
};
FakeAudioContext.prototype.createOscillator = function () {
  if (failNextAudio) {
    failNextAudio = false;
    throw new Error("sortie audio indisponible");
  }
  const sweep = {};
  audioSweeps.push(sweep);
  return {
    frequency: {
      setValueAtTime: function (value) { sweep.start = value; },
      exponentialRampToValueAtTime: function (value) { sweep.end = value; }
    },
    connect: function () {}, start: function () {}, stop: function () {}
  };
};
FakeAudioContext.prototype.createGain = function () {
  return {
    gain: {
      setValueAtTime: function () {},
      exponentialRampToValueAtTime: function () {}
    },
    connect: function () {}
  };
};
window.AudioContext = FakeAudioContext;

const context = {
  window, document, console,
  localStorage: { getItem: function () { return null; }, setItem: function () {} },
  navigator: {}, performance: { now: function () { return 0; } },
  setInterval: function () { return 1; }, clearInterval: function () {},
  setTimeout: function () { return 1; },
  requestAnimationFrame: function () {}, alert: function () {},
  confirm: function () { return true; }
};
vm.createContext(context);
vm.runInContext(scriptMatch[1], context);

let renderCalls = 0;
context.renderTable = function () {
  renderCalls += 1;
  rows.forEach(function (row, index) {
    row.active = index === window.__state.selIndex;
  });
};
context.copyPrice = function () {};
window.__state.orders = undefined;
assert.strictEqual(window.navigateOrders(1), false);
window.__state.orders = [
  { type_id: 34, side: 0, price_cents: 100, char_id: 1 },
  { type_id: 35, side: 0, price_cents: 200, char_id: 1 }
];
window.__state.tab = "buy";
window.__state.sortKey = "price";
window.__state.sortDir = 1;

const binding = (listeners.keydown || []).find(function (item) {
  return item.callback.name === "handleNavigationShortcut";
});
assert(binding, "handler clavier non enregistre");
assert.strictEqual(binding.capture, true);
const event = function (mods) {
  return Object.assign({
    key: "F", code: "KeyF", preventDefault: function () {},
    stopPropagation: function () {}
  }, mods);
};
binding.callback(event({ altKey: true, shiftKey: true }));
assert.strictEqual(window.__state.selIndex, 0);
assert.strictEqual(rows[0].active, true);
assert.strictEqual(rows[0].scrolled, true);
assert.strictEqual(failNextAudio, false);
window.__state.selIndex = 1;
binding.callback(event({ ctrlKey: true, shiftKey: true }));
assert.strictEqual(window.__state.selIndex, 0);
assert.strictEqual(rows[0].active, true);
window.__state.selIndex = -1;
assert.strictEqual(window.navigateOrders(1), true);
assert.strictEqual(window.__state.selIndex, 0);
assert(renderCalls >= 3);
assert.strictEqual(audioContextCreates, 1);
assert.deepStrictEqual(audioSweeps, [
  { start: 480, end: 300 },
  { start: 1050, end: 1250 }
]);
console.log("keyboard navigation + audio: failure safe, reverse 480->300, forward 1050->1250");
