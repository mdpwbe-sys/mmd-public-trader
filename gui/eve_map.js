/* Offline New Eden graph.  Positions are fixed from CCP's physical coordinates. */
(function () {
  const state = { graph: null, data: null, nodesById: null, linkCanvas: null, linkFrame: null, resizeMap: null, lastLinkDraw: 0, visible: false, filters: { high: true, low: true, null: true, gates: true } };
  const byId = () => new Map((state.data && state.data.systems || []).map(s => [s.id, s]));
  const api = () => window.pywebview && window.pywebview.api || window.api;
  const security = s => s >= .5 ? 'high' : s > 0 ? 'low' : 'null';
  const escapeHtml = value => String(value).replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
  function displayNodes(systems, gates) {
    const bySystemId = new Map(systems.map(system => [system.id, system]));
    const neighbors = new Map(systems.map(system => [system.id, new Set()]));
    gates.forEach(gate => { neighbors.get(gate.source)?.add(gate.target); neighbors.get(gate.target)?.add(gate.source); });
    const components = [], visited = new Set();
    systems.forEach(system => { if (visited.has(system.id)) return; const component = [], queue = [system.id]; visited.add(system.id); while (queue.length) { const id = queue.pop(); component.push(bySystemId.get(id)); neighbors.get(id).forEach(next => { if (!visited.has(next)) { visited.add(next); queue.push(next); } }); } components.push(component); });
    const main = components.reduce((largest, component) => component.length > largest.length ? component : largest, []);
    const bounds = main.reduce((box, system) => { ['x', 'y', 'z'].forEach(axis => { box.min[axis] = Math.min(box.min[axis], system.position_m[axis]); box.max[axis] = Math.max(box.max[axis], system.position_m[axis]); }); return box; }, { min: { x: Infinity, y: Infinity, z: Infinity }, max: { x: -Infinity, y: -Infinity, z: -Infinity } });
    const centre = Object.fromEntries(['x', 'y', 'z'].map(axis => [axis, (bounds.min[axis] + bounds.max[axis]) / 2]));
    const span = Math.max(...['x', 'y', 'z'].map(axis => bounds.max[axis] - bounds.min[axis]));
    const mainScale = 2200 / span;
    const mainIds = new Set(main.map(system => system.id));
    const externalGroups = new Map();
    systems.filter(system => !mainIds.has(system.id)).forEach(system => { const key = system.region_id || system.id; const group = externalGroups.get(key) || []; group.push(system); externalGroups.set(key, group); });
    const positions = new Map();
    main.forEach(system => positions.set(system.id, Object.fromEntries(['x', 'y', 'z'].map(axis => [axis, (system.position_m[axis] - centre[axis]) * mainScale]))));
    [...externalGroups.values()].forEach((group, index) => {
      const groupCentre = Object.fromEntries(['x', 'y', 'z'].map(axis => [axis, group.reduce((sum, system) => sum + system.position_m[axis], 0) / group.length]));
      const extent = Math.max(1, ...group.flatMap(system => ['x', 'y', 'z'].map(axis => Math.abs(system.position_m[axis] - groupCentre[axis]))));
      const direction = ['x', 'y', 'z'].map(axis => groupCentre[axis] - centre[axis]);
      const length = Math.hypot(...direction) || 1;
      const angle = index * 2.3999632297;
      const fallback = [Math.cos(angle), Math.sin(angle), Math.sin(angle * .5)];
      const anchor = Object.fromEntries(['x', 'y', 'z'].map((axis, axisIndex) => [axis, (length > 1 ? direction[axisIndex] / length : fallback[axisIndex]) * 1280]));
      const groupScale = Math.min(mainScale, 190 / extent);
      group.forEach(system => positions.set(system.id, Object.fromEntries(['x', 'y', 'z'].map(axis => [axis, anchor[axis] + (system.position_m[axis] - groupCentre[axis]) * groupScale]))));
    });
    return systems.map(system => Object.assign({}, system, positions.get(system.id), { fx: positions.get(system.id).x, fy: positions.get(system.id).y, fz: positions.get(system.id).z }));
  }
  const showSystem = system => { const panel = document.getElementById('eve-map-panel'); panel.querySelector('.eve-map-empty')?.remove(); panel.insertAdjacentHTML('afterbegin', `<div class="eve-map-system"><h3>${escapeHtml(system.name)}</h3><dl><dt>Security</dt><dd>${system.security.toFixed(3)}</dd><dt>Region</dt><dd>${escapeHtml(system.region)}</dd><dt>Constellation</dt><dd>${escapeHtml(system.constellation)}</dd><dt>ID</dt><dd>${system.id}</dd></dl></div>`); document.getElementById('eve-route-from').value = system.name; };
  const visibleNode = node => state.filters[security(node.security)];
  function render() { if (!state.graph) return; state.graph.nodeVisibility(visibleNode).linkVisibility(link => state.filters.gates && visibleNode(link.source) && visibleNode(link.target)); }
  function stopGateOverlay() { if (state.linkFrame) cancelAnimationFrame(state.linkFrame); state.linkFrame = null; }
  function projectVisible(node, camera, width, height) {
    const view = camera.matrixWorldInverse.elements, projection = camera.projectionMatrix.elements;
    const x = view[0] * node.x + view[4] * node.y + view[8] * node.z + view[12];
    const y = view[1] * node.x + view[5] * node.y + view[9] * node.z + view[13];
    const z = view[2] * node.x + view[6] * node.y + view[10] * node.z + view[14];
    const w = projection[3] * x + projection[7] * y + projection[11] * z + projection[15];
    if (w <= 0) return null;
    const nx = (projection[0] * x + projection[4] * y + projection[8] * z + projection[12]) / w;
    const ny = (projection[1] * x + projection[5] * y + projection[9] * z + projection[13]) / w;
    const nz = (projection[2] * x + projection[6] * y + projection[10] * z + projection[14]) / w;
    if (nx < -1.04 || nx > 1.04 || ny < -1.04 || ny > 1.04 || nz < -1 || nz > 1) return null;
    return { x: (nx + 1) * width / 2, y: (1 - ny) * height / 2 };
  }
  function drawGateOverlay() {
    if (!state.visible || !state.graph || !state.linkCanvas) return;
    const now = performance.now();
    if (now - state.lastLinkDraw < 33) { state.linkFrame = requestAnimationFrame(drawGateOverlay); return; }
    state.lastLinkDraw = now;
    const canvas = state.linkCanvas, host = document.getElementById('eve-map-canvas'), dpr = Math.min(window.devicePixelRatio || 1, 2);
    const width = host.clientWidth, height = host.clientHeight;
    if (canvas.width !== width * dpr || canvas.height !== height * dpr) { canvas.width = width * dpr; canvas.height = height * dpr; canvas.style.width = `${width}px`; canvas.style.height = `${height}px`; }
    const context = canvas.getContext('2d'); context.setTransform(dpr, 0, 0, dpr, 0, 0); context.clearRect(0, 0, width, height);
    if (state.filters.gates) {
      const camera = state.graph.camera();
      const nearestSystemDistance = Math.min(...[...state.nodesById.values()].map(node => Math.hypot(camera.position.x - node.x, camera.position.y - node.y, camera.position.z - node.z)));
      // Keep a faint topology trace at galaxy scale instead of fading local gates to zero.
      const localFade = .15 + .85 * Math.max(0, Math.min(1, (1100 - nearestSystemDistance) / 650));
      const regionalLineWidth = .32 + .43 * localFade;
      const regionalBoundaryLineWidth = .42 + .48 * localFade;
      const localSegments = [], regionalSegments = [], boundarySegments = [];
      state.data.gates.forEach(gate => {
        const source = state.nodesById.get(gate.source), target = state.nodesById.get(gate.target);
        if (!source || !target || !visibleNode(source) || !visibleNode(target)) return;
        const physicalDistance = Math.hypot(source.position_m.x - target.position_m.x, source.position_m.y - target.position_m.y, source.position_m.z - target.position_m.z);
        if (physicalDistance > 8e16) return;
        const a = projectVisible(source, camera, width, height), b = projectVisible(target, camera, width, height);
        if (!a || !b) return;
        const sourceBand = security(source.security), targetBand = security(target.security);
        const regional = source.region_id !== target.region_id;
        if (sourceBand !== targetBand) {
          // The safety transitions are rare enough to draw individually. This
          // preserves a readable directional gradient without thousands of draw calls.
          boundarySegments.push({ a, b, sourceBand, targetBand, regional });
        } else {
          (regional ? regionalSegments : localSegments).push(a.x, a.y, b.x, b.y);
        }
      });
      const stroke = (segments, color, lineWidth) => { if (!segments.length) return; context.beginPath(); for (let i = 0; i < segments.length; i += 4) { context.moveTo(segments[i], segments[i + 1]); context.lineTo(segments[i + 2], segments[i + 3]); } context.strokeStyle = color; context.lineWidth = lineWidth; context.stroke(); };
      if (localFade > .01) stroke(localSegments, `rgba(105, 232, 241, ${.34 * localFade})`, .7);
      stroke(regionalSegments, 'rgba(115, 239, 249, .78)', regionalLineWidth);
      const securityColors = { high: '#36d7a0', low: '#efb546', null: '#e45878' };
      boundarySegments.forEach(({ a, b, sourceBand, targetBand, regional }) => {
        if (!regional && localFade <= .01) return;
        const gradient = context.createLinearGradient(a.x, a.y, b.x, b.y);
        gradient.addColorStop(0, securityColors[sourceBand]);
        gradient.addColorStop(1, securityColors[targetBand]);
        context.beginPath(); context.moveTo(a.x, a.y); context.lineTo(b.x, b.y);
        context.strokeStyle = gradient;
        context.globalAlpha = regional ? .96 : .92 * localFade;
        context.lineWidth = regional ? regionalBoundaryLineWidth : 1.05;
        context.stroke();
      });
      context.globalAlpha = 1;
    }
    state.linkFrame = requestAnimationFrame(drawGateOverlay);
  }
  function startGateOverlay(host, nodes) {
    stopGateOverlay();
    const canvas = document.createElement('canvas'); canvas.className = 'eve-map-links'; canvas.setAttribute('aria-hidden', 'true'); Object.assign(canvas.style, { position: 'absolute', inset: '0', zIndex: '2', pointerEvents: 'none' }); host.appendChild(canvas);
    state.linkCanvas = canvas; state.nodesById = new Map(nodes.map(node => [node.id, node])); state.linkFrame = requestAnimationFrame(drawGateOverlay);
  }
  function focus(system) {
    if (!system || !state.graph) return;
    // Search results come from the raw dataset; resolve them to the pinned render
    // node before using scene coordinates for the camera handoff.
    const renderedSystem = state.nodesById?.get(system.id) || system;
    if (!['x', 'y', 'z'].every(axis => Number.isFinite(renderedSystem[axis]))) return;
    showSystem(renderedSystem);
    state.graph.cameraPosition({ x: renderedSystem.x + 34, y: renderedSystem.y + 24, z: renderedSystem.z + 34 }, renderedSystem, 700);
  }
  function initialize(data) {
    state.data = data; const host = document.getElementById('eve-map-canvas'); host.textContent = '';
    // ForceGraph3D is only our WebGL renderer.  Pin all CCP coordinates so its
    // default D3 physics can never turn New Eden into a force-directed cluster.
    const nodes = displayNodes(data.systems, data.gates);
    let graph;
    graph = ForceGraph3D()(host).backgroundColor('#07111c').graphData({ nodes, links: data.gates }).nodeId('id').nodeLabel(node => `${node.name} · ${node.security.toFixed(2)}`).nodeColor(node => node.security >= .5 ? '#36d7a0' : node.security > 0 ? '#efb546' : '#e45878').nodeVal(node => node.security >= .5 ? 1.1 : 1).nodeRelSize(.11).nodeResolution(12).nodeOpacity(.96).linkColor(() => '#70e6ef').linkOpacity(.9).linkWidth(0).enableNodeDrag(false).nodePositionUpdate((object, coords) => {
      // The default spheres use world units and disappear when the camera pulls
      // back. Scale them by camera distance while keeping the CCP position fixed.
      const camera = graph.camera();
      const distance = Math.hypot(camera.position.x - coords.x, camera.position.y - coords.y, camera.position.z - coords.z);
      object.position.set(coords.x, coords.y, coords.z);
      object.scale.setScalar(Math.max(1, Math.min(7, distance / 180)));
      return true;
    }).onNodeClick(focus).onNodeHover(node => host.style.cursor = node ? 'pointer' : 'grab');
    const resizeMap = () => { if (host.clientWidth && host.clientHeight) graph.width(host.clientWidth).height(host.clientHeight); };
    state.resizeMap = resizeMap; resizeMap();
    graph.d3Force('charge', null); graph.d3Force('link', null); graph.d3Force('center', null); graph.cooldownTicks(0); state.graph = graph; render(); startGateOverlay(host, nodes); setTimeout(() => graph.zoomToFit(500, 40), 80);
  }
  async function load() { if (state.graph) return; if (!window.ForceGraph3D) throw new Error('The 3D dependency is unavailable.'); const response = await api().get_eve_map_data(); if (!response.ok) throw new Error(response.error || 'Map unavailable'); initialize(response.data); }
  window.openEveMap = async function () { const overlay = document.getElementById('eve-map-overlay'); overlay.classList.add('is-open'); overlay.setAttribute('aria-hidden', 'false'); state.visible = true; try { await load(); state.resizeMap && state.resizeMap(); if (!state.linkFrame && state.linkCanvas) state.linkFrame = requestAnimationFrame(drawGateOverlay); state.graph.resumeAnimation(); } catch (error) { document.getElementById('eve-map-canvas').innerHTML = `<div class="eve-map-status">${escapeHtml(error.message)}</div>`; } };
  window.closeEveMap = function () { const overlay = document.getElementById('eve-map-overlay'); overlay.classList.remove('is-open'); overlay.setAttribute('aria-hidden', 'true'); state.visible = false; stopGateOverlay(); state.graph && state.graph.pauseAnimation(); };
  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('[data-eve-security], #eve-map-gates').forEach(input => input.addEventListener('change', () => { state.filters[input.dataset.eveSecurity || 'gates'] = input.checked; render(); }));
    document.getElementById('eve-map-fit').addEventListener('click', () => state.graph && state.graph.zoomToFit(500, 40));
    document.getElementById('eve-map-search').addEventListener('input', event => { const query = event.target.value.trim().toLowerCase(); const box = document.getElementById('eve-map-results'); box.innerHTML = ''; if (!query || !state.data) return; state.data.systems.filter(s => s.name.toLowerCase().includes(query)).slice(0, 8).forEach(s => { const button = document.createElement('button'); button.textContent = `${s.name} · ${s.region}`; button.onclick = () => { box.innerHTML = ''; focus(s); }; box.appendChild(button); }); });
    document.getElementById('eve-map-route').addEventListener('click', async () => { const systems = state.data && state.data.systems || []; const resolve = value => systems.find(s => String(s.id) === value.trim() || s.name.toLowerCase() === value.trim().toLowerCase()); const from = resolve(document.getElementById('eve-route-from').value), to = resolve(document.getElementById('eve-route-to').value), result = document.getElementById('eve-route-result'); if (!from || !to) { result.textContent = 'System not found.'; return; } const response = await api().find_eve_route(from.id, to.id); const route = response.data; if (route.error) { result.textContent = 'No stargate route found.'; return; } const steps = new Set(route.systems.slice(1).map((id, index) => [route.systems[index], id].sort().join(':'))); state.graph.linkColor(link => steps.has([link.source.id || link.source, link.target.id || link.target].sort().join(':')) ? '#ffe279' : '#70e6ef').linkWidth(0); result.textContent = `${route.jumps} jump${route.jumps === 1 ? '' : 's'}`; });
  });
}());
