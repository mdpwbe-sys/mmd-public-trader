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
  const elements = new Map(['eve-map-overlay', 'eve-map-canvas', 'eve-map-panel', 'eve-route-from', 'eve-map-search', 'eve-map-results', 'eve-map-fit', 'eve-map-gates', 'eve-map-traffic', 'eve-map-danger', 'eve-map-character', 'eve-map-route', 'eve-route-to', 'eve-route-result'].map(id => [id, element()]));
  const documentListeners = {};
  const graph = {
    backgroundColor() { return this; }, graphData(data) { this.nodes = data.nodes; return this; }, nodeId() { return this; }, nodeLabel() { return this; }, nodeColor() { return this; }, nodeVal() { return this; }, nodeRelSize() { return this; }, nodeResolution() { return this; }, nodeOpacity() { return this; }, nodeVisibility() { return this; }, linkVisibility() { return this; }, linkColor() { return this; }, linkOpacity() { return this; }, linkWidth() { return this; }, enableNodeDrag() { return this; }, nodePositionUpdate() { return this; }, onNodeClick() { return this; }, onNodeRightClick() { return this; }, onBackgroundClick() { return this; }, onNodeHover() { return this; }, width(value) { if (value !== undefined) this.widthValue = value; return this; }, height(value) { if (value !== undefined) this.heightValue = value; return this; }, d3Force() { return this; }, cooldownTicks() { return this; }, zoomToFit() {}, resumeAnimation() {}, pauseAnimation() {},
    cameraPosition(position, target) { this.lastCamera = { position, target }; },
  };
  const dataset = { systems: [
    { id: 30000142, name: 'Jita', security: .9, region: 'The Forge', constellation: 'Kimotoro', region_id: 1, position_m: { x: 0, y: 0, z: 0 } },
    { id: 30000144, name: 'Perimeter', security: .9, region: 'The Forge', constellation: 'Kimotoro', region_id: 1, position_m: { x: 1, y: 0, z: 0 } },
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
  elements.get('eve-map-search').value = 'ji';
  elements.get('eve-map-search').dispatch('input', { target: elements.get('eve-map-search') });
  assert.ok(graph.nodes.find(node => node.id === 30000142 && Number.isFinite(node.x)));
  elements.get('eve-map-results').children[0].onclick();

  assert.equal(graph.lastCamera.target.id, 30000142);
  assert.ok(Object.values(graph.lastCamera.position).every(Number.isFinite));
  elements.get('eve-map-canvas').clientWidth = 1200; elements.get('eve-map-canvas').clientHeight = 750; onMapResize();
  assert.equal(graph.widthValue, 1200); assert.equal(graph.heightValue, 750);
  assert.match(elements.get('eve-map-panel').innerHTML, /LIVE INTEL/);
  assert.match(elements.get('eve-map-panel').innerHTML, /Pilotes actifs/);
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
  assert.match(elements.get('eve-route-result').innerHTML, /1 saut/);
  assert.match(elements.get('eve-route-result').innerHTML, /eve-route-square/);
  assert.match(elements.get('eve-route-result').innerHTML, /Ship kills: 7/);
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
  assert.match(fs.readFileSync(path.join(__dirname, 'eve_map.js'), 'utf8'), /ForceGraph3D\(\{ controlType: 'orbit' \}\)/, 'la carte utilise OrbitControls, nécessaire au pan strictement aligné à l’écran');
  assert.doesNotMatch(mapMarkup, /id="eve-map-reset-camera"/, 'le bouton de réinitialisation redondant est supprimé');
  assert.doesNotMatch(mapMarkup, /id="eve-map-character"/, 'la carte réutilise le sélecteur personnage global MMD');
  const lowOnly = { securityCounts: { high: 0, low: 4, null: 0 } };
  assert.equal(window.__eveMapTest.visibleLabelGroup(lowOnly, { high: true, low: false, null: false }), false);
  assert.equal(window.__eveMapTest.visibleLabelGroup(lowOnly, { high: true, low: true, null: false }), true);
  assert.equal(window.__eveMapTest.characterPositionTrackingInterval(true), 15000, 'le tracking ESI actualise tous les pilotes connectés lorsque la carte est ouverte');
  assert.equal(window.__eveMapTest.characterPositionTrackingInterval(false), null, 'le tracking ESI est arrêté lorsque la carte est masquée');
  const oneLiveKill = window.__eveMapTest.liveWithCombat({ danger: 0 }, 1);
  const threeLiveKills = window.__eveMapTest.liveWithCombat({ danger: 0 }, 3);
  assert.deepEqual(JSON.parse(JSON.stringify(oneLiveKill)), { ship_jumps: 0, ship_kills: 0, pod_kills: 0, npc_kills: 0, recent_combat_kills: 1, danger: 20, danger_band: 'yellow' }, 'un kill R2Z2 récent relève immédiatement le danger du système');
  assert.equal(threeLiveKills.danger_band, 'orange', 'plusieurs kills R2Z2 élèvent l’alerte même avant le prochain agrégat ESI');
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
  assert.deepEqual(JSON.parse(JSON.stringify(window.__eveMapTest.mapLabelLayers(500))), [{ mode: 'constellation', opacity: 1 }], 'la vue région privilégie les constellations après le fondu');
  const regionFocus = { kind: 'region', area: { id: 7 } };
  assert.equal(window.__eveMapTest.isAreaMember({ region_id: 7, constellation_id: 3 }, regionFocus), true);
  assert.equal(window.__eveMapTest.isAreaMember({ region_id: 8, constellation_id: 3 }, regionFocus), false);
  assert.equal(window.__eveMapTest.focusNodeColor({ security: .8, region_id: 7 }, regionFocus), '#36d7a0');
  assert.equal(window.__eveMapTest.focusNodeColor({ security: .8, region_id: 8 }, regionFocus), '#23675b');
  assert.ok(window.__eveMapTest.characterMarkerScale(1800) > window.__eveMapTest.systemObjectScale(1800), 'le sélecteur de personnage reste prioritaire à distance');
  assert.ok(window.__eveMapTest.systemObjectScale(5000) <= 1.05, 'la croissance des nœuds système est fortement plafonnée');
  assert.ok(window.__eveMapTest.systemObjectScale(5000) / window.__eveMapTest.systemObjectScale(200) < 1.25, 'la compensation système augmente très lentement au dézoom');
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
    { system_id: 30000142, happenedAt: 110, expiresAt: 900_010 },
    { system_id: 30000142, happenedAt: 120, expiresAt: 900_020 },
    { system_id: 30000144, happenedAt: 130, expiresAt: 200 },
  ], 300);
  assert.equal(combatGroups.length, 1, 'les kills expirés sont exclus de la fenêtre tactique');
  assert.equal(combatGroups[0].count, 3, 'les kills récents sont condensés par système');
  assert.equal(combatGroups[0].value, 0, 'la synthèse agrège aussi la valeur ISK détruite');
  assert.equal(window.__eveMapTest.combatActivity(0).symbol, '●');
  assert.equal(window.__eveMapTest.combatActivity(1).symbol, '◉');
  assert.equal(window.__eveMapTest.combatActivity(3).symbol, '⚠');
  assert.equal(window.__eveMapTest.combatActivity(7).symbol, '🔥');
  assert.equal(window.__eveMapTest.combatActivity(10).symbol, '💀');
  const directionalGateFlows = window.__eveMapTest.estimateGateFlows({ id: 1 }, { id: 2 }, new Map([[1, 2], [2, 4]]), new Map([[1, 200], [2, 400]]));
  assert.deepEqual(JSON.parse(JSON.stringify(directionalGateFlows)), { sourceToTarget: 100, targetToSource: 100 }, 'chaque sens conserve sa propre estimation de trafic');
  const asymmetricGateFlows = window.__eveMapTest.estimateGateFlows({ id: 1 }, { id: 2 }, new Map([[1, 1], [2, 4]]), new Map([[1, 1200], [2, 200]]));
  assert.deepEqual(JSON.parse(JSON.stringify(asymmetricGateFlows)), { sourceToTarget: 1200, targetToSource: 50 }, 'les deux directions ne sont pas artificiellement fusionnées');
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
