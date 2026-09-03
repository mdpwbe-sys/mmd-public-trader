/* Map workspace adapter: keeps the optional New Eden renderer outside trading UI. */
(function () {
  'use strict';
  let loadPromise = null;
  const el = id => document.getElementById(id);

  function loadScript(src) {
    return new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = src; script.async = true;
      script.onload = resolve;
      script.onerror = () => reject(new Error(`Chargement impossible : ${src}`));
      document.head.appendChild(script);
    });
  }

  function ensureRenderer() {
    if (window.openEveMap) return Promise.resolve();
    if (!loadPromise) loadPromise = loadScript('vendor/3d-force-graph.min.js').then(() => loadScript('eve_map.js'));
    return loadPromise;
  }

  async function show() {
    const workspace = el('eve-map-workspace');
    if (!workspace) return false;
    const state = window.__state || (window.__state = {});
    state.workspace = 'map'; state.selIndex = -1;
    ['orders-metrics', 'orders-grid', 'trade-view'].forEach(id => { const node = el(id); if (node) node.hidden = true; });
    workspace.hidden = false;
    document.querySelectorAll('#tabs .tab').forEach(tab => tab.classList.toggle('active', tab.dataset.workspace === 'map'));
    try { await ensureRenderer(); await window.openEveMap(); return true; }
    catch (error) { el('eve-map-canvas').innerHTML = `<div class="eve-map-status">${String(error.message || error)}</div>`; return false; }
  }

  function hide() {
    const workspace = el('eve-map-workspace'); if (workspace) workspace.hidden = true;
    window.closeEveMap?.();
  }

  async function focusCharacter(characterId) {
    const workspace = el('eve-map-workspace');
    if (!characterId || !workspace || workspace.hidden || !window.focusEveMapCharacter) return false;
    return Boolean(await window.focusEveMapCharacter(characterId));
  }

  function clearCharacterFocus() {
    const workspace = el('eve-map-workspace');
    if (!workspace || workspace.hidden || !window.clearEveMapCharacterSelection) return false;
    window.clearEveMapCharacterSelection();
    return true;
  }

  window.MapWorkspace = { show, hide, focusCharacter, clearCharacterFocus };
}());
