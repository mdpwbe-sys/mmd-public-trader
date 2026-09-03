const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

test('New Eden tab lazy-loads map renderer and leaves trading hidden', async () => {
  const nodes = new Map(['eve-map-workspace', 'orders-metrics', 'orders-grid', 'trade-view', 'eve-map-canvas'].map(id => [id, { id, hidden: false, innerHTML: '' }]));
  const scripts = [], tabs = [{ dataset: { workspace: 'map' }, classList: { toggle() {} } }];
  let opened = 0;
  const window = { __state: {} };
  const document = {
    getElementById(id) { return nodes.get(id); },
    querySelectorAll() { return tabs; },
    createElement() { return {}; },
    head: { appendChild(script) { scripts.push(script.src); if (script.src === 'eve_map.js') window.openEveMap = async () => { opened += 1; }; script.onload(); } },
  };
  vm.runInContext(fs.readFileSync(path.join(__dirname, 'map-workspace.js'), 'utf8'), vm.createContext({ window, document, Promise, Error, String }));
  assert.equal(scripts.length, 0, 'aucun renderer avant le clic');
  assert.equal(await window.MapWorkspace.show(), true);
  assert.deepEqual(scripts, ['vendor/3d-force-graph.min.js', 'eve_map.js']);
  assert.equal(nodes.get('eve-map-workspace').hidden, false);
  assert.equal(nodes.get('orders-grid').hidden, true);
  assert.equal(opened, 1);
  let focusedCharacter = null;
  window.focusEveMapCharacter = async id => { focusedCharacter = id; return true; };
  assert.equal(await window.MapWorkspace.focusCharacter(42), true);
  assert.equal(focusedCharacter, 42, 'le pont de workspace transmet le clic d’une puce personnage au renderer actif');
  let clearedCharacterFocus = 0;
  window.clearEveMapCharacterSelection = () => { clearedCharacterFocus += 1; };
  assert.equal(window.MapWorkspace.clearCharacterFocus(), true);
  assert.equal(clearedCharacterFocus, 1, 'le pont de workspace annule aussi le filtre de marqueur quand Tous les pilotes est choisi');
  nodes.get('eve-map-workspace').hidden = true;
  assert.equal(await window.MapWorkspace.focusCharacter(42), false, 'un clic personnage hors carte ne charge ni ne déplace la carte');
});
