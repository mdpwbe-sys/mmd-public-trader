"use strict";
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const html = fs.readFileSync("gui/index.html", "utf8");
const script = html.match(/<script[^>]*>([\s\S]*?)<\/script>/i);
assert(script, "script GUI introuvable");

function element() {
  const base = {
    style: {}, dataset: {}, innerHTML: "", textContent: "", title: "",
    attributes: {}, disabled: false,
    classList: { add() {}, remove() {}, toggle() {} },
    addEventListener() {}, removeEventListener() {}, appendChild() {},
    setAttribute(name, value) { this.attributes[name] = String(value); },
    querySelector() { return null; }, querySelectorAll() { return []; },
    contains() { return false; },
    getBoundingClientRect() { return { left: 0, top: 0, width: 100, height: 100 }; }
  };
  return new Proxy(base, {
    get(target, key) { return key in target ? target[key] : ""; }
  });
}

const elements = new Map();
const getElement = id => {
  if (!elements.has(id)) elements.set(id, element());
  return elements.get(id);
};
const document = {
  readyState: "loading", body: element(),
  addEventListener() {}, removeEventListener() {},
  getElementById: getElement, createElement: element,
  querySelector() { return null; }, querySelectorAll() { return []; }
};
const saves = [];
const api = {
  save_sparklines(payload) {
    saves.push(JSON.parse(JSON.stringify(payload)));
    return true;
  }
};
const window = { document, pywebview: { api }, addEventListener() {} };
window.window = window;
const storage = {};
const context = {
  window, document, console,
  localStorage: {
    getItem(key) { return storage[key] || null; },
    setItem(key, value) { storage[key] = value; }
  },
  navigator: {}, performance: { now() { return 0; } },
  setInterval() { return 1; }, clearInterval() {}, setTimeout() { return 1; },
  requestAnimationFrame() {}, alert() {}, confirm() { return true; },
  getComputedStyle(node) { return node.style; }
};
vm.createContext(context);
vm.runInContext(script[1], context);

const snapshot = (timestamp, firstTotal, synced = ["1", "2"]) => ({
  source: "ESI sync", counts_timestamp_ms: timestamp,
  sso_chars: [{ id: 1, name: "One" }, { id: 2, name: "Two" }],
  synced_char_ids: synced,
  orders_to_update_by_char: {
    "1": { total: firstTotal, buy: firstTotal, sell: 0 },
    "2": { total: synced.length === 1 ? 99 : 2, buy: 0,
      sell: synced.length === 1 ? 99 : 2 }
  }
});

assert.strictEqual(context.ingestOrderUpdateCounts(snapshot(1000, 1), true), true);
assert.strictEqual(window.__sparklineStore.char_1.length, 1);
assert.strictEqual(window.__sparklineStore.char_2.length, 1);
assert.strictEqual(window.__sparklineStore.all[0].val, 3);
assert.strictEqual(saves.length, 1);

assert.strictEqual(context.ingestOrderUpdateCounts(snapshot(2000, 1), true), false);
assert.strictEqual(window.__sparklineStore.char_1.length, 1);
assert.strictEqual(window.__sparklineStore.char_2.length, 1);
assert.strictEqual(window.__sparklineStore.all.length, 1);
assert.strictEqual(saves.length, 1);

assert.strictEqual(context.ingestOrderUpdateCounts(snapshot(3000, 2, ["1"]), true), true);
assert.strictEqual(window.__updateCountsByChar["2"].total, 2,
  "un perso non synchronise doit garder son dernier compteur connu");
assert.strictEqual(window.__sparklineStore.char_1.length, 2);
assert.strictEqual(window.__sparklineStore.char_2.length, 1);
assert.deepStrictEqual(
  Array.from(window.__sparklineStore.all, point => point.val), [3, 4]);
assert.strictEqual(context.getStableUpdateCount(null).total, 4);
assert.strictEqual(saves.length, 2);

assert.strictEqual(context.ingestOrderUpdateCounts(snapshot(2500, 9, ["1"]), true), false,
  "une reponse plus ancienne doit etre ignoree");
assert.strictEqual(window.__updateCountsByChar["1"].total, 2);
assert.deepStrictEqual(
  Array.from(window.__sparklineStore.all, point => point.val), [3, 4]);
assert.strictEqual(saves.length, 2);

assert.strictEqual(context.ingestOrderUpdateCounts(snapshot(4000, 7, []), false), false);
assert.strictEqual(window.__updateCountsByChar["1"].total, 2,
  "un cache non synchronise ne doit pas remplacer le dernier point fiable");
assert.strictEqual(saves.length, 2);

const beforeFilter = JSON.stringify(window.__sparklineStore);
window.__state.orders = [];
window.__dupList = [];
window.__selChar = "1";
context.updateDynamicMetrics();
assert.strictEqual(getElement("m-update").textContent, 2);
window.__selChar = null;
context.updateDynamicMetrics();
assert.strictEqual(getElement("m-update").textContent, 4);
assert.strictEqual(JSON.stringify(window.__sparklineStore), beforeFilter,
  "changer de filtre ne doit jamais ecrire un point");
assert.strictEqual(saves.length, 2);

assert.strictEqual((getElement("spark-dots").innerHTML.match(/<circle/g) || []).length, 2);
assert.strictEqual((getElement("spark-grid").innerHTML.match(/<line/g) || []).length, 3);
assert.match(getElement("spark-y-labels").innerHTML, /<span>4<\/span>/);
assert.match(getElement("spark-last-value").textContent, /^4 \(\d{2}:\d{2}\)$/);
const persisted = JSON.parse(storage.mmd_order_counts_v2);
assert.strictEqual(persisted.version, 2);
assert.deepStrictEqual(Array.from(persisted.active_char_ids), ["1", "2"]);

window.__sparklineStore = {};
window.__updateCountsByChar = {};
window.__activeUpdateCharIds = [];
context.ingestOrderUpdateCounts({
  source: "partial", counts_timestamp_ms: 5000,
  sso_chars: [{ id: 1 }, { id: 2 }], synced_char_ids: ["1"],
  orders_to_update_by_char: { "1": { total: 1, buy: 1, sell: 0 } }
}, true);
assert.strictEqual(window.__sparklineStore.all, undefined,
  "Tous ne doit jamais enregistrer une somme partielle");
console.log("orders to update: sync-only stable, all=sum, readable persisted graph");
