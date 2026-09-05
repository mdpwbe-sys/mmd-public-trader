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
    querySelector() { return null; }, insertAdjacentHTML(_, html) { this.innerHTML += html; }, addEventListener(type, callback) { listeners[type] = callback; },
    dispatch(type, event = {}) { listeners[type]?.(event); },
  };
}

test('search result focuses the rendered node coordinates', async () => {
  const elements = new Map(['eve-map-overlay', 'eve-map-canvas', 'eve-map-content', 'eve-map-panel', 'eve-map-panel-toggle', 'eve-route-from', 'eve-map-search', 'eve-map-results', 'eve-map-fit', 'eve-map-gates', 'eve-map-traffic', 'eve-map-danger', 'eve-map-character', 'eve-map-route', 'eve-map-route-clear', 'eve-route-to', 'eve-route-result'].map(id => [id, element()]));
  const documentListeners = {};
  const graph = {
    backgroundColor() { return this; }, graphData(data) { this.nodes = data.nodes; return this; }, nodeId() { return this; }, nodeLabel() { return this; }, nodeColor() { return this; }, nodeVal() { return this; }, nodeRelSize() { return this; }, nodeResolution() { return this; }, nodeOpacity() { return this; }, nodeVisibility() { return this; }, linkVisibility() { return this; }, linkColor() { return this; }, linkOpacity() { return this; }, linkWidth() { return this; }, enableNodeDrag() { return this; }, nodePositionUpdate() { return this; }, onNodeClick() { return this; }, onNodeRightClick() { return this; }, onBackgroundClick() { return this; }, onNodeHover() { return this; }, width(value) { if (value !== undefined) this.widthValue = value; return this; }, height(value) { if (value !== undefined) this.heightValue = value; return this; }, d3Force() { return this; }, cooldownTicks() { return this; }, zoomToFit() {}, resumeAnimation() {}, pauseAnimation() {},
    cameraPosition(position, target) { this.lastCamera = { position, target }; },
  };
  const dataset = { systems: [
    { id: 30000142, name: 'Jita', security: .9, region: 'The Forge', constellation: 'Kimotoro', region_id: 1, constellation_id: 10, position_m: { x: 0, y: 0, z: 0 }, planet_count: 2, moon_count: 3, belt_count: 1, npc_station_count: 1, planets: [{ id: 1, name: 'Jita I', type_name: 'Barren', moon_count: 2 }, { id: 2, name: 'Jita II', type_name: 'Temperate', moon_count: 1 }], belts: [{ id: 3, name: 'Jita I - Asteroid Belt 1', type_id: 15 }], npc_stations: [{ id: 4, name: 'Jita - Caldari Navy', services: ['Market', 'Repair'] }] },
    { id: 30000144, name: 'Perimeter', security: .9, region: 'The Forge', constellation: 'Kimotoro', region_id: 1, constellation_id: 10, position_m: { x: 1, y: 0, z: 0 }, planet_count: 0, moon_count: 0, belt_count: 0, npc_station_count: 0, planets: [], belts: [], npc_stations: [] },
  ], gates: [{ source: 30000142, target: 30000144 }] };
  let pilotSystemId = 30000142;
  let onMapResize;
  class ResizeObserver { constructor(callback) { onMapResize = callback; } observe() {} disconnect() {} }
  const window = { __eveMapTest: {}, ResizeObserver, api: { async get_eve_map_data() { return { ok: true, data: dataset }; }, async get_eve_map_live_intel() { return { ok: true, state: 'live', age_seconds: 0, systems: { 30000142: { ship_jumps: 42315, ship_kills: 7, pod_kills: 2, npc_kills: 183, danger: 63, danger_band: 'orange' } } }; }, async get_eve_map_character_positions() { return { ok: true, positions: [{ character_id: 1, name: 'Pilot', system_id: pilotSystemId }, { character_id: 2, name: 'Wingmate', system_id: 30000142 }] }; }, async find_eve_route(source, target) { return { ok: true, data: { systems: [source, target], jumps: 1 } }; } }, ForceGraph3D: () => () => graph };
  const context = vm.createContext({ window, document: { readyState: 'loading', getElementById(id) { return elements.get(id); }, querySelectorAll() { return []; }, createElement() { return element(); }, addEventListener(type, callback) { documentListeners[type] = callback; } }, ForceGraph3D: window.ForceGraph3D, requestAnimationFrame() { return 1; }, cancelAnimationFrame() {}, setTimeout() {}, performance: { now() { return 100; } }, Math, Map, Set, Object, String });
  vm.runInContext(fs.readFileSync(path.join(__dirname, 'eve_map.js'), 'utf8'), context);
  documentListeners.DOMContentLoaded();
  await window.openEveMap();
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(window.__eveMapTest.formatKillDate('2026-09-02T17:34:21Z'), '2026-09-02 · 17:34 UTC', 'Latest kills conserve date et heure UTC complètes');
  assert.match(window.__eveMapTest.killLocation({ solar_system_id: 30000142 }), /Jita · Kimotoro, The Forge/, 'Latest kills affiche le système, la constellation et la région');
  assert.match(window.__eveMapTest.systemHoverMarkup(graph.nodes[0]), /Jita.*0\.90.*The Forge.*Kimotoro.*Traffic 42,315 jumps\/h.*Ships 7.*Pods 2.*NPC 183.*R2Z2 0 \/ 30 min/s, 'le hover système expose sécurité et les quatre compteurs live');
  assert.match(window.__eveMapTest.systemHoverMarkup(graph.nodes[0]), /Tracked pilots: Pilot, Wingmate/, 'le hover système réutilise les pilotes ESI déjà synchronisés');
  assert.match(window.__eveMapTest.systemHoverMarkup(graph.nodes[0]), /PLANETS.*2 planets · 3 moons.*Barren 1 · Temperate 1.*MINING.*1 asteroid belts.*STATIONS.*1 NPC stations.*Market · Repair/s, 'le hover système condense PI, lunes, belts et services NPC du SDE local');
  assert.match(window.__eveMapTest.areaHoverMarkup({ id: 10, name: 'Kimotoro' }, 'constellation'), /Constellation · 2 systems.*Traffic 42,315 jumps\/h.*NPC 183.*2 planets · 3 moons.*1 belts · 1 NPC stations/s, 'le hover constellation agrège live et statique sans requête');
  assert.match(window.__eveMapTest.areaHoverMarkup({ id: 1, name: 'The Forge' }, 'region'), /Region · 2 systems.*Traffic 42,315 jumps\/h.*NPC 183.*2 planets · 3 moons/s, 'le hover région agrège live et statique sans requête');
  const gateData = window.__eveMapTest.gateHoverData(graph.nodes[0], graph.nodes[1], new Map([[30000142, 1], [30000144, 1]]), new Map([[30000142, 500], [30000144, 200]]));
  assert.deepEqual(JSON.parse(JSON.stringify(gateData.flows)), { sourceEndpoint: 500, targetEndpoint: 200 }, 'le hover gate expose les parts estimées des extrémités, sans inventer de direction');
  assert.equal(gateData.estimated_jumps, 350, 'le hover gate affiche la synthèse du trafic estimé');
  assert.match(window.__eveMapTest.gateHoverMarkup({ source: graph.nodes[0], target: graph.nodes[1] }), /Jita ↔ Perimeter.*Estimated gate activity.*Endpoint estimates.*jumps\/h/s, 'le tooltip gate reste compact, honnête sur la direction et sans appel réseau');
  assert.equal(window.__eveMapTest.gateHoverCandidate([{ key: 'a', a: { x: 10, y: 10 }, b: { x: 110, y: 10 } }], 55, 14)?.key, 'a', 'un lien projeté devient une cible de hover tolérante');
  elements.get('eve-map-search').value = 'ji';
  elements.get('eve-map-search').dispatch('input', { target: elements.get('eve-map-search') });
  assert.ok(graph.nodes.find(node => node.id === 30000142 && Number.isFinite(node.x)));
  elements.get('eve-map-results').children[0].onclick();

  assert.equal(graph.lastCamera.target.id, 30000142);
  assert.ok(Object.values(graph.lastCamera.position).every(Number.isFinite));
  elements.get('eve-map-canvas').clientWidth = 1200; elements.get('eve-map-canvas').clientHeight = 750; onMapResize();
  assert.equal(graph.widthValue, 1200); assert.equal(graph.heightValue, 750);
  assert.match(elements.get('eve-map-panel').innerHTML, /LIVE INTEL/);
  assert.match(elements.get('eve-map-panel').innerHTML, /Active pilots/);
  assert.match(elements.get('eve-map-panel').innerHTML, /<br>/);
  assert.notEqual(window.__eveMapTest.characterColor({ character_id: 1 }), window.__eveMapTest.characterColor({ character_id: 2 }));
  const pilotRing = window.__eveMapTest.characterRingSegments([{ character_id: 1 }, { character_id: 2 }]);
  assert.equal(pilotRing.length, 2, 'deux pilotes partagent un seul cercle');
  assert.ok(Math.abs((pilotRing[0].end - pilotRing[0].start) - Math.PI) < .2, 'chaque pilote reçoit approximativement une moitié du cercle');
  assert.equal(await window.focusEveMapCharacter(1), true, 'une puce personnage peut focaliser la carte sur sa position ESI');
  assert.equal(graph.lastCamera.target.id, 30000142);
  pilotSystemId = 30000144;
  assert.equal(await window.refreshEveMapCharacterPositions(), true, 'un changement de système détecté par ESI met à jour le suivi');
  assert.equal(window.__eveMapTest.characterPositionSystemId(1), 30000144, 'le marqueur personnage reçoit immédiatement le nouveau système après un jump');
  assert.equal(graph.lastCamera.target.id, 30000144, 'le pilote sélectionné entraîne la caméra vers son nouveau système après un jump');
  window.clearEveMapCharacterSelection();
  assert.deepEqual(JSON.parse(JSON.stringify(window.__eveMapTest.visibleCharacterIds())), [1, 2], 'revenir à Tous les pilotes réaffiche les marqueurs de chaque pilote connecté');
  window.setEveMapOrigin(30000142);
  window.setEveMapDestination(30000144);
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.match(elements.get('eve-route-result').innerHTML, /1 jump/);
  assert.match(elements.get('eve-route-result').innerHTML, /eve-route-square/);
  assert.match(elements.get('eve-route-result').innerHTML, /Ship kills: 7/);
  const systemSelection = { kind: 'system', target: dataset.systems[0] }, constellationSelection = { kind: 'constellation', target: { id: 10, name: 'Kimotoro' } }, regionSelection = { kind: 'region', target: { id: 1, name: 'The Forge' } };
  assert.equal(window.__eveMapTest.selectionSystems(systemSelection).length, 1, 'la sélection système ne contient qu’un système');
  assert.equal(window.__eveMapTest.selectionSystems(constellationSelection).length, 2, 'la sélection constellation agrège ses systèmes');
  assert.equal(window.__eveMapTest.selectionSystems(regionSelection).length, 2, 'la sélection région agrège ses systèmes');
  const constellationLive = window.__eveMapTest.aggregateLiveIntel(window.__eveMapTest.selectionSystems(constellationSelection));
  assert.equal(constellationLive.ship_jumps, 42315, 'l’intel ESI de zone agrège les systèmes disponibles sans requête supplémentaire');
  assert.deepEqual(JSON.parse(JSON.stringify(window.__eveMapTest.aggregateStaticIntel(window.__eveMapTest.selectionSystems(constellationSelection)))), { planet_count: 2, moon_count: 3, belt_count: 1, npc_station_count: 1 }, 'les agrégats SDE de constellation restent entièrement locaux');
  assert.equal(window.__eveMapTest.staticSystemHoverMarkup(graph.nodes[1]), '', 'un système sans planète, belt ni station NPC ne casse pas le hover');
  assert.match(window.__eveMapTest.selectionInfoMarkup(systemSelection), /Jita.*0\.9/, 'INFOS adapte le titre à un système');
  assert.match(window.__eveMapTest.selectionInfoMarkup(constellationSelection), /Constellation.*Region/s, 'INFOS affiche région et niveau pour une constellation');
  assert.match(window.__eveMapTest.selectionInfoMarkup(regionSelection), /Region.*Systems/s, 'INFOS adapte son contenu à une région');
  const panelKill = { solar_system_id: 30000142 };
  assert.equal(window.__eveMapTest.panelKillLocation(panelKill, systemSelection), '', 'un kill système ne répète pas sa localisation');
  assert.equal(window.__eveMapTest.panelKillLocation(panelKill, constellationSelection), 'Jita', 'un kill constellation affiche son système');
  assert.equal(window.__eveMapTest.panelKillLocation(panelKill, regionSelection), 'Jita · Kimotoro', 'un kill région affiche système et constellation');
  assert.match(window.__eveMapTest.killRowsMarkup([{ killmail_id: 42, solar_system_id: 30000142, time: '2026-09-04T11:12:00Z', value: 2e6, ship_type_id: 34, url: 'https://zkillboard.com/kill/42/' }], regionSelection), /data-kill-system-id="30000142"/, 'le popover peut retrouver le système du kill, même depuis une région');
  assert.equal(window.__eveMapTest.togglePanelSection('intel'), false, 'un accordéon se ferme sans toucher aux autres sections');
  assert.equal(window.__eveMapTest.panelSectionMarkup('info', 'INFOS', '').includes('is-open'), true, 'l’état ouvert reste conservé lors d’une nouvelle sélection');
  assert.equal(window.__eveMapTest.setPanelCollapsed(true), true, 'le panneau peut être entièrement escamoté');
  assert.equal(window.__eveMapTest.setPanelCollapsed(false), false, 'le toggle du header peut rouvrir le panneau');
  assert.equal(window.__eveMapTest.isKillPopoverInteractionTarget({ closest: selector => selector === '.eve-kill' ? {} : null }, { contains: () => false }), true, 'un déplacement/clic entre la ligne kill et son popover reste interactif');
  assert.equal(window.__eveMapTest.isKillPopoverInteractionTarget({}, { contains: () => false }), false, 'un clic extérieur ferme le popover kill');
  assert.match(window.__eveMapTest.attackerPopoverMarkup({ attackers: [{ character_id: 9, pilot_name: 'Pilot test', ship_name: 'Loki', zkill_url: 'https://zkillboard.com/character/9/' }], total_attackers: 1 }), /target="_blank"/, 'le popover conserve des liens cliquables vers les profils attackers');
  assert.equal(window.clearEveMapRoute(), true, 'Effacer réinitialise la route');
  assert.equal(elements.get('eve-route-from').value, ''); assert.equal(elements.get('eve-route-to').value, ''); assert.equal(elements.get('eve-route-result').textContent, '');
});

test('viewport clipping retains the visible portion of a gate crossing the screen edge', () => {
  const window = { __eveMapTest: {} };
  const context = vm.createContext({ window, document: { readyState: 'loading', addEventListener() {} }, Math, Map, Set, Object, String });
  vm.runInContext(fs.readFileSync(path.join(__dirname, 'eve_map.js'), 'utf8'), context);
  const clipped = window.__eveMapTest.clipSegmentToViewport({ x: -40, y: 50 }, { x: 140, y: 50 }, 100, 100);
  assert.deepEqual(JSON.parse(JSON.stringify(clipped)), { a: { x: 0, y: 50 }, b: { x: 100, y: 50 } });
  assert.equal(window.__eveMapTest.clipSegmentToViewport({ x: -40, y: -10 }, { x: -5, y: -10 }, 100, 100), null);
  const shot = window.__eveMapTest.galaxyShot([{ x: -50, y: 0, z: -20 }, { x: 50, y: 10, z: 20 }]);
  assert.ok(shot.position.y > shot.target.y, 'la vue initiale se place au-dessus de la galaxie');
  assert.ok(shot.position.y - shot.target.y > 180, 'la vue initiale reste éloignée');
  const jitaAnchor = { id: 30000142, name: 'Jita', x: 12, y: 5, z: -7 };
  const jitaShot = window.__eveMapTest.galaxyShot([{ x: -50, y: 0, z: -20 }, jitaAnchor, { x: 50, y: 10, z: 20 }]);
  assert.equal(jitaShot.target, jitaAnchor, 'la vue Home est ancrée sur Jita');
  const focusPose = window.__eveMapTest.cameraPose({ x: 0, y: 0, z: 0 }, 100);
  const homeDirection = { x: jitaShot.position.x - jitaShot.target.x, y: jitaShot.position.y - jitaShot.target.y, z: jitaShot.position.z - jitaShot.target.z };
  assert.ok(Math.abs(focusPose.x / focusPose.y - homeDirection.x / homeDirection.y) < 1e-9 && Math.abs(focusPose.z / focusPose.y - homeDirection.z / homeDirection.y) < 1e-9, 'les focus système restent dans le plan de caméra New Eden');
  const preservedFocus = window.__eveMapTest.cameraFocusPosition(
    { x: 80, y: -20, z: 15 },
    { position: { x: 18, y: 45, z: 73 } },
    { target: { x: 8, y: 5, z: 3 } },
  );
  assert.deepEqual(JSON.parse(JSON.stringify(preservedFocus)), { x: 90, y: 20, z: 85 }, 'un focus système translate la caméra et conserve exactement son angle courant');
  const fallbackFocus = window.__eveMapTest.cameraFocusPosition({ x: 0, y: 0, z: 0 }, null, null, 42);
  assert.deepEqual(JSON.parse(JSON.stringify(fallbackFocus)), JSON.parse(JSON.stringify(window.__eveMapTest.cameraPose({ x: 0, y: 0, z: 0 }, 42))), 'le premier focus conserve une pose New Eden déterministe sans caméra active');
  const planarNodes = window.__eveMapTest.displayNodes([
    { id: 1, position_m: { x: 0, y: -9000, z: 0 } },
    { id: 2, position_m: { x: 400, y: 12000, z: 600 } },
  ], [{ source: 1, target: 2 }]);
  assert.notEqual(planarNodes[0].y, planarNodes[1].y, 'la carte conserve la profondeur 3D authentique de New Eden');
  const flatControls = { target: { x: 4, y: 23, z: -8 }, mouseButtons: {}, object: { up: { set(x, y, z) { this.value = [x, y, z]; } } } };
  window.__eveMapTest.stabilizeOrbitControls(flatControls);
  assert.equal(flatControls.target.y, 23, 'le contrôle ne déforme pas la profondeur de la cible caméra');
  assert.ok(flatControls.minPolarAngle > 0 && flatControls.maxPolarAngle > Math.PI / 2 && flatControls.maxPolarAngle < Math.PI, 'la rotation gauche reste libre autour de New Eden, hors singularité exacte des pôles');
  assert.equal(flatControls.screenSpacePanning, true, 'le clic droit reste aligné aux axes visibles de l’écran, quelle que soit l’inclinaison de la caméra');
  assert.deepEqual(JSON.parse(JSON.stringify(flatControls.mouseButtons)), { LEFT: 0, MIDDLE: 1, RIGHT: 2 }, 'le contrôle impose rotation à gauche, zoom au centre/molette et pan à droite');
  assert.equal(flatControls.enableDamping, true, 'la rotation gauche retrouve son inertie fluide');
  assert.ok(flatControls.dampingFactor > 0 && flatControls.dampingFactor < .1, 'l’amortissement de caméra reste léger');
  const reticleNear = window.__eveMapTest.selectionReticleStyle(40), reticleFar = window.__eveMapTest.selectionReticleStyle(3000);
  assert.ok(reticleNear.radius >= 17 && reticleFar.radius > reticleNear.radius, 'le viseur sélectionné suit le nœud tout en restant lisible à distance');
  assert.ok(window.__eveMapTest.systemSelectionRadius(2500) > window.__eveMapTest.systemSelectionRadius(100), 'la zone de clic d’un système augmente au dézoom');
  const mapMarkup = fs.readFileSync(path.join(__dirname, 'index.html'), 'utf8');
  assert.match(mapMarkup, /id="eve-map-fit"/, 'l’en-tête de carte expose un unique bouton Home');
  assert.match(mapMarkup, /id="eve-map-panel-toggle"/, 'l’en-tête expose un toggle unique pour le panneau intel');
  assert.doesNotMatch(mapMarkup, /eve-map-panel-collapse|eve-map-panel-handle/, 'les contrôles latéraux dupliqués sont supprimés');
  assert.match(fs.readFileSync(path.join(__dirname, 'eve_map.js'), 'utf8'), /ForceGraph3D\(\{ controlType: 'orbit' \}\)/, 'la carte utilise OrbitControls, nécessaire au pan strictement aligné à l’écran');
  assert.doesNotMatch(mapMarkup, /id="eve-map-reset-camera"/, 'le bouton de réinitialisation redondant est supprimé');
  assert.doesNotMatch(mapMarkup, /id="eve-map-character"/, 'la carte réutilise le sélecteur personnage global MMD');
  const lowOnly = { securityCounts: { high: 0, low: 4, null: 0 } };
  assert.equal(window.__eveMapTest.visibleLabelGroup(lowOnly, { high: true, low: false, null: false }), false);
  assert.equal(window.__eveMapTest.visibleLabelGroup(lowOnly, { high: true, low: true, null: false }), true);
  assert.equal(window.__eveMapTest.characterPositionTrackingInterval(true), 15000, 'le tracking ESI actualise tous les pilotes connectés lorsque la carte est ouverte');
  assert.equal(window.__eveMapTest.characterPositionTrackingInterval(false), null, 'le tracking ESI est arrêté lorsque la carte est masquée');
  const oneLiveKill = window.__eveMapTest.liveWithCombat({ danger: 0 }, 1);
  assert.equal(window.__eveMapTest.security(.487), 'high', 'un statut brut 0.487 est high-sec comme son affichage 0.5 en jeu');
  const threeLiveKills = window.__eveMapTest.liveWithCombat({ danger: 0 }, 3);
  const podCombat = window.__eveMapTest.liveWithCombat({ danger: 13 }, { kills: 6, pods: 2 });
  assert.deepEqual(JSON.parse(JSON.stringify(oneLiveKill)), { ship_jumps: 0, ship_kills: 0, pod_kills: 0, npc_kills: 0, recent_combat_kills: 1, recent_combat_pods: 0, danger: 20, danger_band: 'yellow' }, 'un kill R2Z2 récent relève immédiatement le danger du système');
  assert.equal(threeLiveKills.danger_band, 'orange', 'plusieurs kills R2Z2 élèvent l’alerte même avant le prochain agrégat ESI');
  assert.equal(podCombat.danger, 85, 'deux pods sur six kills font immédiatement dominer le Danger live, même si ESI reste à 13');
  assert.equal(podCombat.recent_combat_pods, 2, 'le panneau conserve le nombre de pods live');
  const mergedAreaKills = window.__eveMapTest.mergeKillRows(
    [{ killmail_id: 84, time: '2026-09-04T02:35:00Z', value: 99, attacker_count: 2 }],
    [{ killmail_id: 84, time: '2026-09-04T02:34:00Z', value: 1, ship_type_id: 670 }, { killmail_id: 83, time: '2026-09-04T02:10:00Z', value: 2 }],
  );
  assert.deepEqual(JSON.parse(JSON.stringify(mergedAreaKills.map(kill => kill.killmail_id))), [84, 83], 'R2Z2 live et zKill historique de zone restent visibles sans doublon');
  assert.equal(mergedAreaKills[0].value, 99, 'la version R2Z2 en direct complète/priorise le même kill historique');
  const skyShader = window.__eveMapTest.skyGpuFragmentShader;
  assert.match(skyShader, /float nebulaDensity/, 'le gaz repose sur un champ de nuages stable plutôt que sur un motif de rubans');
  assert.doesNotMatch(skyShader, /gasRibbon/, 'aucune sinusoïde visible ne doit découper le fond en bandes');
  assert.match(skyShader, /uSkyDebugMode/, 'le champ de gaz et la coquille d’étoiles restent inspectables en debug');
  assert.equal(window.__eveMapTest.labelsCanOverlap('region'), true, 'les noms de région restent tous visibles à l’échelle galaxie, même lorsqu’ils se superposent');
  assert.equal(window.__eveMapTest.labelsCanOverlap('constellation'), false, 'les constellations conservent leur filtre anti-chevauchement');
  assert.deepEqual(JSON.parse(JSON.stringify(window.__eveMapTest.mapLabelPlacement('region', { x: 2, y: 4 }, 80, 320, 180))), { x: 46, y: 12 }, 'un libellé de région est décalé dans le viewport au lieu d’être tronqué sur un bord');
  assert.deepEqual(JSON.parse(JSON.stringify(window.__eveMapTest.mapLabelPlacement('region', { x: 318, y: 178 }, 80, 320, 180))), { x: 274, y: 168 }, 'un libellé de région reste entièrement lisible près du bord opposé');
  assert.deepEqual(JSON.parse(JSON.stringify(window.__eveMapTest.mapLabelLayers(1200))), [{ mode: 'region', opacity: 1 }], 'la vue galaxie privilégie les régions');
  const crossoverLabels = window.__eveMapTest.mapLabelLayers(900);
  assert.equal(crossoverLabels.length, 2, 'la transition région/constellation affiche les deux couches simultanément');
  assert.ok(crossoverLabels.every(layer => layer.opacity > 0 && layer.opacity < 1), 'la transition région/constellation est progressive');
  const mediumLabels = window.__eveMapTest.mapLabelLayers(500);
  assert.deepEqual(JSON.parse(JSON.stringify(mediumLabels.map(layer => layer.mode))), ['constellation', 'system'], 'le zoom moyen garde les constellations et une couche système sélective');
  assert.equal(mediumLabels[0].opacity, 1, 'les constellations restent la lecture principale au zoom moyen');
  const regionFocus = { kind: 'region', area: { id: 7 } };
  assert.equal(window.__eveMapTest.isAreaMember({ region_id: 7, constellation_id: 3 }, regionFocus), true);
  assert.equal(window.__eveMapTest.isAreaMember({ region_id: 8, constellation_id: 3 }, regionFocus), false);
  const securityPalette = [[1, '#54c9ff'], [.9, '#54c9ff'], [.8, '#45dfc8'], [.7, '#63d985'], [.6, '#b8dc63'], [.5, '#f1d163'], [.4, '#f6a75d'], [.3, '#ef8249'], [.2, '#e45d5d'], [.1, '#c84359'], [0, '#e45878'], [-.1, '#e45878']];
  securityPalette.forEach(([status, color]) => assert.equal(window.__eveMapTest.securityColor(status), color, `security ${status} reçoit sa couleur de base`));
  assert.equal(window.__eveMapTest.securityColor(.5), '#f1d163', 'la frontière 0.5 reste jaune');
  assert.equal(window.__eveMapTest.securityColor(.4), '#f6a75d', 'la frontière 0.4 devient orange clair');
  assert.equal(window.__eveMapTest.securityColor(.1), '#c84359', 'la frontière 0.1 devient rouge profond');
  assert.equal(window.__eveMapTest.securityColor(0), '#e45878', 'la frontière 0.0 garde le magenta null-sec');
  assert.equal(window.__eveMapTest.focusNodeColor({ security: .8, region_id: 7 }, regionFocus), '#45dfc8');
  assert.match(window.__eveMapTest.focusNodeColor({ security: .8, region_id: 8 }, regionFocus), /^rgb\(/, 'la désaccentuation hors zone part de la même palette de sécurité');
  assert.equal(window.__eveMapTest.musicDuckMultiplier(5), .60, 'une alerte à cinq jumps applique le duck léger');
  assert.equal(window.__eveMapTest.musicDuckMultiplier(3), .35, 'une alerte à trois jumps applique le duck moyen');
  assert.equal(window.__eveMapTest.musicDuckMultiplier(1), .15, 'une alerte à un jump applique le duck fort');
  assert.deepEqual(JSON.parse(JSON.stringify(window.__eveMapTest.musicDuckingPlan(5))), { multiplier: .60, fadeDownMs: 250, holdMs: 3000, fadeUpMs: 2000 }, 'le duck léger a une enveloppe audio bornée');
  assert.equal(window.__eveMapTest.musicDuckingPlan(0).multiplier, .15, 'un kill dans le système du pilote applique le duck fort');
  assert.deepEqual(JSON.parse(JSON.stringify(window.__eveMapTest.musicVisibilityPlan(true))), { fadeMs: 2500, pause: false }, 'ouvrir New Eden démarre la musique avec un fade-in de 2.5 s');
  assert.deepEqual(JSON.parse(JSON.stringify(window.__eveMapTest.musicVisibilityPlan(false))), { fadeMs: 600, pause: true }, 'quitter New Eden effectue un fade-out puis une pause');
  assert.ok(window.__eveMapTest.characterMarkerScale(1800) > window.__eveMapTest.systemObjectScale(1800), 'le sélecteur de personnage reste prioritaire à distance');
  assert.ok(window.__eveMapTest.systemObjectScale(5000) <= 1.05, 'la croissance des nœuds système est fortement plafonnée');
  assert.ok(window.__eveMapTest.systemObjectScale(5000) / window.__eveMapTest.systemObjectScale(200) < 1.25, 'la compensation système augmente très lentement au dézoom');
  assert.ok(window.__eveMapTest.trafficSize(50_000) <= 1.101, 'Traffic ne peut plus gonfler les nodes de plus de 10 %');
  assert.ok(window.__eveMapTest.trafficNodeStyle(50_000).radius <= 3.51 && window.__eveMapTest.trafficNodeStyle(50_000).alpha <= .131, 'le halo Traffic reste un repère discret, pas une bulle concurrente');
  const distantGates = window.__eveMapTest.gateVisualProfile(3000), localGates = window.__eveMapTest.gateVisualProfile(100);
  assert.ok(distantGates.regionalAlpha < .2 && localGates.localAlpha < .2, 'les gates au repos restent plus calmes que les overlays tactiques');
  assert.ok(localGates.localWidth > distantGates.localWidth, 'la topologie gagne un peu de présence seulement à proximité');
  assert.equal(window.__eveMapTest.gateCameraFade(99_999), .14, 'une gate loin de la caméra conserve un plancher topologique discret');
  assert.ok(localGates.combatAlpha < localGates.characterAlpha && localGates.combatWidth < localGates.characterWidth, 'une alerte combat reste sous le niveau visuel d’une gate adjacente à un pilote');
  assert.equal(window.__eveMapTest.gateEmphasisKind({ key: '1:2', source: 1, target: 2 }, { characterSystemIds: new Set([2]), combatSystemIds: new Set([1]), tacticalSystemIds: new Set(), hoverGateKey: null }), 'character', 'une gate directement adjacente à un pilote ESI a la priorité visuelle maximale');
  assert.equal(window.__eveMapTest.gateEmphasisKind({ key: '1:2', source: 1, target: 2 }, { characterSystemIds: new Set(), combatSystemIds: new Set([1]), tacticalSystemIds: new Set(), hoverGateKey: null }), 'combat', 'une gate combat reste mise en évidence sans dominer un pilote voisin');
  assert.ok(window.__eveMapTest.influenceOverlayRadius(3000, 'sovereignty') > window.__eveMapTest.influenceOverlayRadius(300, 'sovereignty'), 'la souveraineté se regroupe visuellement à distance');
  assert.ok(window.__eveMapTest.influenceOverlayRadius(3000, 'sovereignty') > window.__eveMapTest.influenceOverlayRadius(3000, 'empire'), 'la souveraineté garde une présence distincte au dézoom');
  const empireLayers = window.__eveMapTest.influenceLayers({ faction_id: 500001 }, { sovereignty: false, empires: true }, { alliance_id: 99000001 });
  assert.deepEqual(JSON.parse(JSON.stringify(empireLayers)), [{ id: 500001, alpha: .40, kind: 'empire', offset: 0 }], 'l’Empire/NPC SDE garde la même présence que Player Sov même si le cache Sov connaît un propriétaire');
  assert.deepEqual(JSON.parse(JSON.stringify(window.__eveMapTest.influenceLayers({ security: -.4 }, { sovereignty: true, empires: false }, { faction_id: 500007 }))), [], 'Player Sov exclut les propriétaires NPC/pirates même en null-sec');
  assert.deepEqual(JSON.parse(JSON.stringify(window.__eveMapTest.influenceLayers({ security: .9 }, { sovereignty: true, empires: false }, { alliance_id: 99000001 }))), [], 'Player Sov exclut le high-sec');
  assert.equal(window.__eveMapTest.influenceLayers({ security: -.4 }, { sovereignty: true, empires: false }, { alliance_id: 99000001 })[0].kind, 'sovereignty', 'Player Sov conserve les alliances en null-sec');
  const empireRegional = window.__eveMapTest.influenceOverlayStyle(700, 'empire');
  const empireGalaxy = window.__eveMapTest.influenceOverlayStyle(3000, 'empire');
  const sovereigntyRegional = window.__eveMapTest.influenceOverlayStyle(700, 'sovereignty');
  assert.ok(empireRegional.radius > 7 && empireRegional.opacity >= .5, 'Empire/NPC reste plus large que le node système et lisible en vue rapprochée');
  assert.ok(sovereigntyRegional.radius > empireRegional.radius && sovereigntyRegional.opacity === empireRegional.opacity, 'Player Sov garde un diamètre supérieur, mais la même opacité qu’Empire/NPC');
  assert.ok(empireGalaxy.radius > empireRegional.radius && empireGalaxy.opacity >= .32, 'Empire/NPC garde une masse lisible à l’échelle galaxie');
  const inferredGateTraffic = window.__eveMapTest.estimateGateTraffic({ id: 1 }, { id: 2 }, new Map([[1, 2], [2, 4]]), new Map([[1, 200], [2, 400]]));
  assert.equal(inferredGateTraffic, 100, 'le trafic de gate est une estimation équilibrée depuis les deux systèmes');
  const combatGroups = window.__eveMapTest.combatMarkerGroups([
    { system_id: 30000142, happenedAt: 100, expiresAt: 900_000 },
    { system_id: 30000142, happenedAt: 110, expiresAt: 900_010, victim_ship_type_id: 670 },
    { system_id: 30000142, happenedAt: 120, expiresAt: 900_020, victim_ship_type_id: 670 },
    { system_id: 30000144, happenedAt: 130, expiresAt: 200 },
  ], 300);
  assert.equal(combatGroups.length, 1, 'les kills expirés sont exclus de la fenêtre tactique');
  assert.equal(combatGroups[0].count, 3, 'les kills récents sont condensés par système');
  assert.equal(combatGroups[0].pod_count, 2, 'les pods sont distingués des autres kills live');
  assert.equal(window.__eveMapTest.combatMarkerLabel({ count: 6, pod_count: 2 }), '🔥 6²', 'les pods mixtes sont affichés en exposant');
  assert.equal(window.__eveMapTest.combatMarkerLabel({ count: 6, pod_count: 6 }), '🥚 6', 'un système à pods uniquement utilise le marqueur œuf');
  assert.equal(combatGroups[0].value, 0, 'la synthèse agrège aussi la valeur ISK détruite');
  assert.equal(window.__eveMapTest.combatActivity(0).symbol, '●');
  assert.equal(window.__eveMapTest.combatActivity(1).symbol, '◉');
  assert.equal(window.__eveMapTest.combatActivity(3).symbol, '⚠');
  assert.equal(window.__eveMapTest.combatActivity(7).symbol, '🔥');
  assert.equal(window.__eveMapTest.combatActivity(10).symbol, '💀');
  const directionalGateFlows = window.__eveMapTest.estimateGateFlows({ id: 1 }, { id: 2 }, new Map([[1, 2], [2, 4]]), new Map([[1, 200], [2, 400]]));
  assert.deepEqual(JSON.parse(JSON.stringify(directionalGateFlows)), { sourceEndpoint: 100, targetEndpoint: 100 }, 'chaque extrémité conserve sa contribution estimée');
  const asymmetricGateFlows = window.__eveMapTest.estimateGateFlows({ id: 1 }, { id: 2 }, new Map([[1, 1], [2, 4]]), new Map([[1, 1200], [2, 200]]));
  assert.deepEqual(JSON.parse(JSON.stringify(asymmetricGateFlows)), { sourceEndpoint: 1200, targetEndpoint: 50 }, 'les contributions d’extrémité ne sont pas artificiellement fusionnées');
  assert.equal(window.__eveMapTest.trafficParticlePlan(100).count, 1, '1–100 sauts utilise une particule');
  assert.equal(window.__eveMapTest.trafficParticlePlan(101).count, 2, '101–1000 sauts utilise deux particules');
  assert.equal(window.__eveMapTest.trafficParticlePlan(1001).count, 3, '1001+ sauts utilise trois particules');
  assert.match(window.__eveMapTest.trafficParticlePlan(100).particles[0].color, /hsl\(12 /, 'le premier palier atteint un rouge doux à 100');
  assert.match(window.__eveMapTest.trafficParticlePlan(101).particles[1].color, /hsl\(142 /, 'le deuxième palier commence vert à 101');
  assert.match(window.__eveMapTest.trafficParticlePlan(1000).particles[1].color, /hsl\(12 /, 'le deuxième palier atteint le rouge doux à 1000');
  assert.match(window.__eveMapTest.trafficParticlePlan(1001).particles[2].color, /hsl\(142 /, 'le troisième palier commence vert à 1001');
  assert.deepEqual(JSON.parse(JSON.stringify(window.__eveMapTest.trafficPacketOffsets(3))), [0, .052, .104], 'les trois particules circulent en paquet séquentiel');
  assert.ok(window.__eveMapTest.trafficParticleSpeed(5000) > window.__eveMapTest.trafficParticleSpeed(10), 'un trafic plus intense accélère son paquet');
  assert.ok(window.__eveMapTest.trafficParticleProgress(1000, 0, 100, 400) < window.__eveMapTest.trafficParticleProgress(1000, 0, 100, 40), 'une liaison longue prend davantage de temps à parcourir');
  const shortProjectedGate = { source: { x: 0, y: 0, z: 0 }, target: { x: 0, y: 0, z: 80 }, screenLength: 24 };
  const longProjectedGate = { ...shortProjectedGate, screenLength: 860 };
  assert.equal(window.__eveMapTest.trafficSegmentProgress(1000, .3, 750, shortProjectedGate), window.__eveMapTest.trafficSegmentProgress(1000, .3, 750, longProjectedGate), 'le mouvement traffic est invariant quand la caméra change la longueur écran du même lien');
  const trafficVisual = window.__eveMapTest.trafficParticleVisualStyle(window.__eveMapTest.trafficParticlePlan(1001).particles[2]);
  assert.ok(trafficVisual.coreAlpha >= .95 && trafficVisual.glowAlpha >= .18, 'les pastilles traffic gardent un cœur saturé et un halo lisible');
  assert.equal(window.__eveMapTest.shipIconUrl(587), 'https://images.evetech.net/types/587/icon?size=64', 'un type de vaisseau construit son icône publique');
  assert.equal(window.__eveMapTest.shipIconUrl('not-a-type'), null, 'une valeur de type invalide ne devient jamais une URL image');
  const attackerMarkup = window.__eveMapTest.attackerPopoverMarkup({ total_attackers: 2, attackers: [{ pilot_name: 'Hunter', ship_name: 'Rifter', ship_type_id: 587, final_blow: true, damage_done: 1234 }] });
  assert.match(attackerMarkup, /Hunter/);
  assert.match(attackerMarkup, /Rifter/);
  assert.match(attackerMarkup, /final blow/);
  const nearCamera = { projectionMatrix: { elements: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, -1, 0, 0, 0, 1] } };
  const nearNode = { geometry: { boundingSphere: { radius: 1 } } };
  const nearScale = window.__eveMapTest.cappedSystemObjectScale(nearNode, 10, nearCamera, 600);
  assert.ok(nearScale * 600 / (2 * 10) <= 7.001, 'un système proche ne dépasse jamais le plafond écran');
  const extremeNearScale = window.__eveMapTest.cappedSystemObjectScale(nearNode, .01, nearCamera, 600);
  assert.ok(extremeNearScale * 600 / (.01 * 2) <= 7.001, 'le plafond reste effectif au zoom extrême');
  const farScale = window.__eveMapTest.cappedSystemObjectScale(nearNode, 5000, nearCamera, 600);
  assert.ok(farScale * 600 / (5000 * 2) <= .161, 'la vue galaxie très éloignée réduit encore les nœuds');
  const localScale = window.__eveMapTest.cappedSystemObjectScale(nearNode, 100, nearCamera, 600);
  assert.ok(localScale * 600 / 200 > farScale * 600 / 10000 * 12, 'le voisinage caméra garde une présence nettement supérieure au fond');
  const renderedNode = { x: 0, y: 0, z: 0, __threeObj: { geometry: { boundingSphere: { radius: 1 } }, scale: { setScalar(value) { this.value = value; } } } };
  const movingCamera = { position: { x: 10, y: 0, z: 0 }, projectionMatrix: nearCamera.projectionMatrix };
  window.__eveMapTest.updateSystemScreenScales([renderedNode], movingCamera, 600);
  assert.ok(renderedNode.__threeObj.scale.value * 600 / 20 <= 7.001, 'la boucle visuelle applique le plafond au mesh rendu');
  assert.deepEqual(JSON.parse(JSON.stringify(window.__eveMapTest.buildSkyStars(3))), JSON.parse(JSON.stringify(window.__eveMapTest.buildSkyStars(3))));
  assert.equal(window.__eveMapTest.buildSkyStars().length, 3000, 'le dôme conserve une densité suffisante hors bande galactique');
  const textureDirection = window.__eveMapTest.skyTextureDirection({ x: 1, y: 0, z: 0 });
  assert.ok(Math.abs(textureDirection.x) < 1e-9 && Math.abs(textureDirection.z - 1) < 1e-9, 'la panoramique reçoit le quart de tour autour de l’axe vertical');
  const restoredDirection = window.__eveMapTest.worldSkyDirection(textureDirection);
  assert.ok(Math.abs(restoredDirection.x - 1) < 1e-9 && Math.abs(restoredDirection.z) < 1e-9, 'le repère texture et le repère monde restent inverses');
  const controls = {};
  window.__eveMapTest.stabilizeOrbitControls(controls);
  assert.ok(controls.minPolarAngle > 0 && controls.maxPolarAngle < Math.PI, 'l’orbite ne traverse jamais la singularité visuelle des pôles');
  const settledSkyField = window.__eveMapTest.skyFieldDimensions(1920, 1080, 1, false);
  const movingSkyField = window.__eveMapTest.skyFieldDimensions(1920, 1080, 1, true);
  assert.ok(settledSkyField.width * settledSkyField.height > movingSkyField.width * movingSkyField.height, 'la qualité du fond augmente après le mouvement caméra');
  assert.ok(settledSkyField.width * settledSkyField.height <= 365000, 'le fond calculé garde un budget de pixels borné');
  assert.match(window.__eveMapTest.skyGpuFragmentShader, /texture2D\(uSkyTexture/, 'le fond normal est projeté par le GPU depuis la texture');
  assert.match(window.__eveMapTest.skyGpuFragmentShader, /nearStarLayer/, 'une couche d’étoiles proches reste calculée dans le même shader GPU');
  assert.doesNotMatch(window.__eveMapTest.skyGpuFragmentShader, /proceduralNebulaPacked/, 'le chemin GPU ne réintroduit pas de raster procédural CPU');
  const skyAsset = fs.readFileSync(path.join(__dirname, 'data/sky/new-eden-sky-milky-way-4096.webp'));
  assert.equal(skyAsset.toString('ascii', 0, 4), 'RIFF', 'la texture de ciel optimisée est un fichier WebP valide');
  assert.equal(skyAsset.toString('ascii', 8, 12), 'WEBP', 'la texture de ciel reste adaptée au décodage Chromium/WebView2');
  assert.match(window.__eveMapTest.skyGpuFragmentShader, /nebulaDensity/, 'les voiles gazeux reposent sur un champ de nuages GPU, sans boucle CPU de fond');
  assert.doesNotMatch(window.__eveMapTest.skyGpuFragmentShader, /gasRibbon/, 'le gaz ne redevient pas un motif périodique en rubans');
  assert.match(fs.readFileSync(path.join(__dirname, 'eve_map.js'), 'utf8'), /SKY_GPU_MOVING_SCALE = \.72/, 'le fond réduit explicitement sa résolution pendant le mouvement caméra');
  const packedTexture = window.__eveMapTest.skyTexturePacked({ width: 2, height: 2, pixels: new Uint8ClampedArray([1, 2, 3, 255, 4, 5, 6, 255, 7, 8, 9, 255, 10, 11, 12, 255]) }, 1, 0, 0);
  assert.equal(packedTexture, (10 << 16) | (11 << 8) | 12, 'la texture equirectangulaire est échantillonnée depuis la direction céleste calculée');
  const band = window.__eveMapTest.nebulaColor(1, 0, 0), pole = window.__eveMapTest.nebulaColor(0, 1, 0);
  assert.ok(band.g + band.b > pole.g + pole.b, 'le fallback procédural conserve une bande galactique cohérente dans le repère monde');
  assert.match(fs.readFileSync(path.join(__dirname, 'eve_map.css'), 'utf8'), /\.eve-map-canvas \.scene-container \{[^}]*z-index:1/, 'le rendu WebGL est au-dessus du canevas de nébuleuse');
  const skyCamera = { matrixWorld: { elements: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 99, -22, 8, 1] }, projectionMatrix: { elements: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, -1, 0, 0, 0, 1] } };
  const movedSkyCamera = { matrixWorld: { elements: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, -300, 17, 40, 1] }, projectionMatrix: skyCamera.projectionMatrix };
  assert.equal(window.__eveMapTest.skyCameraKey(skyCamera, 800, 600, 1), window.__eveMapTest.skyCameraKey(movedSkyCamera, 800, 600, 1), 'une translation ne recalcule pas la sphère infiniment lointaine');
  const sideCamera = { matrixWorld: { elements: [0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 0, 0, 0, 0, 1] }, projectionMatrix: skyCamera.projectionMatrix };
  const upperSkyRay = window.__eveMapTest.skyRayDirection(sideCamera, 0, .65);
  const lowerSkyRay = window.__eveMapTest.skyRayDirection(sideCamera, 0, -.65);
  assert.ok(upperSkyRay.y > 0 && lowerSkyRay.y < 0, 'en vue latérale, un mouvement vertical de la carte reste vertical dans le repère céleste New Eden');
  const upperTextureCoordinate = window.__eveMapTest.skyTextureCoordinates(upperSkyRay.x, upperSkyRay.y, upperSkyRay.z);
  const lowerTextureCoordinate = window.__eveMapTest.skyTextureCoordinates(lowerSkyRay.x, lowerSkyRay.y, lowerSkyRay.z);
  assert.ok(Math.abs(upperTextureCoordinate.u - lowerTextureCoordinate.u) < 1e-9 && upperTextureCoordinate.v < lowerTextureCoordinate.v, 'le glissement vertical échantillonne la latitude de la texture, jamais une translation horizontale');
  const identityCamera = { matrixWorldInverse: { elements: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1] }, projectionMatrix: { elements: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, -1, 0, 0, 0, 1] } };
  assert.deepEqual(JSON.parse(JSON.stringify(window.__eveMapTest.projectSkyDirection({ x: 0, y: 0, z: -1 }, identityCamera, 800, 600))), { x: 400, y: 300 });
  assert.equal(window.__eveMapTest.projectSkyDirection({ x: 0, y: 0, z: 1 }, identityCamera, 800, 600), null);
  const gateCamera = { ...identityCamera, near: .1 };
  const clippedGate = window.__eveMapTest.projectGateSegment({ x: -.5, y: 0, z: -2 }, { x: 5, y: 0, z: 1 }, gateCamera, 800, 600);
  assert.deepEqual(JSON.parse(JSON.stringify(clippedGate)), { a: { x: 333.33333333333337, y: 300 }, b: { x: 800, y: 300 } }, 'une gate qui franchit le plan caméra reste visible jusqu’au bord');
});
