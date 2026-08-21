const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

const source = fs.readFileSync('gui/trading-ui.js', 'utf8');
const index = fs.readFileSync('gui/index.html', 'utf8');

function node(id) {
  return {
    id, hidden: false, innerHTML: '', textContent: '', value: '', disabled: false,
    children: [], classList: { toggle() {} },
    appendChild(child) { this.children.push(child); },
  };
}

const elements = {
  'orders-metrics': node('orders-metrics'), 'orders-grid': node('orders-grid'),
  'trade-view': node('trade-view'), 'tab-assets-c': node('tab-assets-c'),
  'tab-transactions-c': node('tab-transactions-c'), 'tab-alerts-c': node('tab-alerts-c'),
};
const tabs = [
  { dataset: { tab: 'buy' }, classList: { toggle() {} } },
  { dataset: { workspace: 'assets' }, classList: { toggle() {} } },
];
const document = {
  getElementById(id) { return elements[id] || null; },
  querySelectorAll(selector) { return selector === '#tabs .tab' ? tabs : []; },
  createElement(tag) { return node(tag); },
};
const window = {
  document, __state: { workspace: 'orders', tab: 'buy', selIndex: -1 },
  pywebview: null, api: null, addEventListener() {},
};
const context = { window, document, console, BigInt, Set, Promise, String, Number, Array };
vm.createContext(context);
vm.runInContext(source, context);

const ui = window.TradingUI;
assert(ui, 'TradingUI doit être exposé');
assert.strictEqual(ui.fmtCents('9007199254740993123'), '90.071.992.547.409.931,23 ISK');
assert.strictEqual(ui.fmtCents('-25000000000'), '-250.000.000,00 ISK');

ui.acceptWorkspace({
  ok: true, source_label: 'Corporation 88 · Réserve & Projets (#4)',
  summary: {}, assets: [], transactions: [], alerts: [{
    severity: 'critical', action: 'HOLD', name: '<img src=x onerror=alert(1)>',
    message: '<script>bad()</script>', value_cents: '-100',
  }],
});
assert.strictEqual(ui.show('alerts'), true);
assert.strictEqual(window.__state.workspace, 'alerts');
assert.strictEqual(elements['orders-grid'].hidden, true);
assert.strictEqual(elements['trade-view'].hidden, false);
assert(elements['trade-view'].innerHTML.includes('&lt;img'));
assert(!elements['trade-view'].innerHTML.includes('<script>bad'));

ui.showOrders();
assert.strictEqual(window.__state.workspace, 'orders');
assert.strictEqual(elements['orders-grid'].hidden, false);
assert.strictEqual(elements['trade-view'].hidden, true);

assert(index.includes("if (window.__state.workspace !== 'orders') return false;"));
assert(index.includes("TradingUI.show('assets')"));
assert(index.includes("TradingUI.show('transactions')"));
assert(index.includes("TradingUI.show('alerts')"));
assert(source.includes("option(select, '', 'Choisir une division…')"));
assert(source.includes('save.disabled = !division.value'));
assert(!/\b(?:Capital|Master|Stock|Collector)\b/.test(source));
assert(!/division_id\s*:\s*['"]?1['"]?/.test(source));

(async () => {
  ui.show('assets');
  ui.acceptWorkspace({ ok: false, error: 'ESI <down>' });
  assert(elements['trade-view'].innerHTML.includes('TradingUI.refresh()'));
  assert(elements['trade-view'].innerHTML.includes('Réessayer'));
  assert(elements['trade-view'].innerHTML.includes('&lt;down&gt;'));
  assert(!elements['trade-view'].innerHTML.includes('<down>'));

  let cachedCalls = 0, refreshCalls = 0;
  window.api = {
    get_trade_workspace() { cachedCalls += 1; return { ok: true, summary: {}, assets: [], transactions: [], alerts: [] }; },
    refresh_trade_workspace() { refreshCalls += 1; return { ok: true, summary: {}, assets: [], transactions: [], alerts: [] }; },
  };
  ui.show('assets');
  await new Promise(resolve => setImmediate(resolve));
  assert.strictEqual(cachedCalls, 1, 'revisiter une erreur doit relancer le cache');
  await ui.refresh();
  assert.strictEqual(refreshCalls, 1, 'Réessayer doit appeler le refresh ESI');

  ui.acceptWorkspace({ ok: true, stale: true, sync_errors: ['ESI <timeout>'],
    summary: {}, assets: [], transactions: [], alerts: [] });
  assert(elements['trade-view'].innerHTML.includes('trade-sync-warning'));
  assert(elements['trade-view'].innerHTML.includes('&lt;timeout&gt;'));
  assert(!elements['trade-view'].innerHTML.includes('<timeout>'));
  ui.acceptWorkspace({ ok: true, stale: false, sync_errors: [],
    summary: {}, assets: [], transactions: [], alerts: [] });
  assert(!elements['trade-view'].innerHTML.includes('trade-sync-warning'));

  ['trade-division-select', 'trade-container-select', 'trade-settings-save',
    'trade-settings-status'].forEach(id => { elements[id] = node(id); });
  window.api.get_trade_settings = () => ({ ok: true, wallet_source: null,
    asset_source: null, containers: [], divisions: [
      { key: 'a', corporation_id: '101', division_id: '4', name: 'Libre' },
      { key: 'b', corporation_id: '202', division_id: '4', name: 'Libre' },
    ] });
  await ui.loadSettings(node('settings-slot'));
  const labels = elements['trade-division-select'].children.map(child => child.textContent);
  assert(labels.some(label => label.includes('Corporation 101')));
  assert(labels.some(label => label.includes('Corporation 202')));

  const freshElements = {
    'orders-metrics': node('orders-metrics'), 'orders-grid': node('orders-grid'),
    'trade-view': node('trade-view'), 'tab-assets-c': node('tab-assets-c'),
    'tab-transactions-c': node('tab-transactions-c'), 'tab-alerts-c': node('tab-alerts-c'),
  };
  let earlySettings = 0, earlyWorkspace = 0;
  const freshDocument = {
    getElementById(id) { return freshElements[id] || null; },
    querySelectorAll() { return []; }, createElement(tag) { return node(tag); },
  };
  const freshWindow = { document: freshDocument,
    __state: { workspace: 'orders', tab: 'buy', selIndex: -1 },
    pywebview: { api: {
      get_trade_settings() { earlySettings += 1; return { ok: true, divisions: [], containers: [] }; },
      get_trade_workspace() { earlyWorkspace += 1; return { ok: true, summary: {}, assets: [], transactions: [], alerts: [] }; },
    } }, addEventListener() {} };
  const freshContext = { window: freshWindow, document: freshDocument, console,
    BigInt, Set, Promise, String, Number, Array };
  vm.createContext(freshContext); vm.runInContext(source, freshContext);
  assert.strictEqual(earlySettings + earlyWorkspace, 0, 'aucun fetch trading au démarrage');
  freshWindow.TradingUI.show('dashboard');
  await new Promise(resolve => setImmediate(resolve));
  assert.strictEqual(earlySettings, 1); assert.strictEqual(earlyWorkspace, 1);
  await freshWindow.TradingUI.init();
  assert.strictEqual(freshWindow.__state.workspace, 'dashboard', 'init tardif ne renavigue pas');

  console.log('asset transaction UI tests: OK');
})().catch(error => { console.error(error); process.exitCode = 1; });
