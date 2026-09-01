const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

function element() {
  const listeners = {};
  return {
    style: {}, value: '', textContent: '', innerHTML: '', clientWidth: 900, clientHeight: 600,
    children: [], classList: { add() {}, remove() {} }, setAttribute() {}, appendChild(child) { this.children.push(child); },
    querySelector() { return null; }, insertAdjacentHTML() {}, addEventListener(type, callback) { listeners[type] = callback; },
    dispatch(type, event = {}) { listeners[type]?.(event); },
  };
}

test('search result focuses the rendered node coordinates', async () => {
  const elements = new Map(['eve-map-overlay', 'eve-map-canvas', 'eve-map-panel', 'eve-route-from', 'eve-map-search', 'eve-map-results', 'eve-map-fit', 'eve-map-gates', 'eve-map-route', 'eve-route-to', 'eve-route-result'].map(id => [id, element()]));
  const documentListeners = {};
  const graph = {
    backgroundColor() { return this; }, graphData(data) { this.nodes = data.nodes; return this; }, nodeId() { return this; }, nodeLabel() { return this; }, nodeColor() { return this; }, nodeVal() { return this; }, nodeRelSize() { return this; }, nodeResolution() { return this; }, nodeOpacity() { return this; }, nodeVisibility() { return this; }, linkVisibility() { return this; }, linkColor() { return this; }, linkOpacity() { return this; }, linkWidth() { return this; }, enableNodeDrag() { return this; }, nodePositionUpdate() { return this; }, onNodeClick() { return this; }, onNodeHover() { return this; }, width() { return this; }, height() { return this; }, d3Force() { return this; }, cooldownTicks() { return this; }, zoomToFit() {}, resumeAnimation() {}, pauseAnimation() {},
    cameraPosition(position, target) { this.lastCamera = { position, target }; },
  };
  const dataset = { systems: [
    { id: 30000142, name: 'Jita', security: .9, region: 'The Forge', constellation: 'Kimotoro', region_id: 1, position_m: { x: 0, y: 0, z: 0 } },
    { id: 30000144, name: 'Perimeter', security: .9, region: 'The Forge', constellation: 'Kimotoro', region_id: 1, position_m: { x: 1, y: 0, z: 0 } },
  ], gates: [{ source: 30000142, target: 30000144 }] };
  const window = { api: { async get_eve_map_data() { return { ok: true, data: dataset }; } }, ForceGraph3D: () => () => graph };
  const context = vm.createContext({ window, document: { getElementById(id) { return elements.get(id); }, querySelectorAll() { return []; }, createElement() { return element(); }, addEventListener(type, callback) { documentListeners[type] = callback; } }, ForceGraph3D: window.ForceGraph3D, requestAnimationFrame() { return 1; }, cancelAnimationFrame() {}, setTimeout() {}, performance: { now() { return 100; } }, Math, Map, Set, Object, String });
  vm.runInContext(fs.readFileSync(path.join(__dirname, 'eve_map.js'), 'utf8'), context);
  documentListeners.DOMContentLoaded();
  await window.openEveMap();
  elements.get('eve-map-search').value = 'ji';
  elements.get('eve-map-search').dispatch('input', { target: elements.get('eve-map-search') });
  assert.ok(graph.nodes.find(node => node.id === 30000142 && Number.isFinite(node.x)));
  elements.get('eve-map-results').children[0].onclick();

  assert.equal(graph.lastCamera.target.id, 30000142);
  assert.ok(Object.values(graph.lastCamera.position).every(Number.isFinite));
});
