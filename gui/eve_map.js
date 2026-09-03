/* Offline New Eden graph.  Positions are fixed from CCP's physical coordinates. */
(function () {
  const state = { graph: null, data: null, nodesById: null, renderNodes: [], trafficBySystem: new Map(), gateDegrees: new Map(), labelGroups: null, labelHitTargets: [], pointer: null, suppressNodeClickUntil: 0, linkCanvas: null, skyCanvas: null, skyField: null, skyImage: null, skyRays: null, skyTexture: null, skyTextureLoading: false, skyStars: [], skyFrameKey: null, skyLastPaintAt: 0, skyInteracting: false, nodeScaleKey: null, linkFrame: null, resizeMap: null, resizeObserver: null, lastLinkDraw: 0, visible: false, route: null, originId: null, destinationId: null, lastNodeClick: null, hoverCandidate: null, killHoverKey: null, killPopover: null, characters: [], characterTrackingTimer: null, combatMarkerTimer: null, combatMarkers: new Map(), live: { systems: {}, state: 'unavailable', updated_at: null }, sovereignty: { systems: {}, state: 'unavailable', updated_at: null }, filters: { high: true, low: true, null: true, gates: true, traffic: false, danger: false, sovereignty: false, empires: false } };
  const byId = () => new Map((state.data && state.data.systems || []).map(s => [s.id, s]));
  const api = () => window.pywebview && window.pywebview.api || window.api;
  const mapWorkspace = () => document.getElementById('eve-map-workspace') || document.getElementById('eve-map-overlay');
  const security = s => s >= .5 ? 'high' : s > 0 ? 'low' : 'null';
  const escapeHtml = value => String(value).replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
  const CHARACTER_COLORS = ['#66e7f5', '#ffca62', '#e978b1', '#aa8cff', '#7ee6a9', '#ff956e'];
  const CHARACTER_POSITION_POLL_MS = 15_000;
  const COMBAT_MARKER_TTL_MS = 30 * 60 * 1000;
  const characterPositionTrackingInterval = visible => visible ? CHARACTER_POSITION_POLL_MS : null;
  const characterColorInfo = character => {
    const dashboardColor = window.getCharColorInfo?.(character?.name);
    if (dashboardColor?.color) return dashboardColor;
    const color = CHARACTER_COLORS[Math.abs(Number(character?.character_id) || 0) % CHARACTER_COLORS.length];
    return { color, glow: color };
  };
  const characterColor = character => characterColorInfo(character).color;
  const characterGroups = () => {
    const groups = new Map();
    state.characters.filter(character => !state.selectedCharacterId || String(character.character_id) === String(state.selectedCharacterId)).forEach(character => {
      const group = groups.get(character.system_id) || [];
      group.push(character); groups.set(character.system_id, group);
    });
    return groups;
  };
  const visibleCharacterIds = () => [...characterGroups().values()].flat().map(character => character.character_id).sort((left, right) => left - right);
  const liveFor = system => state.live.systems[String(system?.id)] || { ship_jumps: 0, ship_kills: 0, pod_kills: 0, npc_kills: 0, danger: 0, danger_band: 'normal' };
  const sovereigntyFor = system => state.sovereignty.systems[String(system?.id)] || {};
  function influenceLayers(node, filters = state.filters, sovereignty = sovereigntyFor(node)) {
    const layers = [];
    const playerSovereignId = sovereignty.alliance_id || sovereignty.corporation_id;
    // Empires & NPC is static SDE data.  It must not disappear merely because
    // a previously loaded public Sov cache happens to know an owner here.
    if (filters.empires && node.faction_id) layers.push({ id: node.faction_id, alpha: .40, kind: 'empire', offset: layers.length });
    // CCP's sovereignty payload also contains faction-owned systems. Player
    // Sov intentionally means only player alliances/corporations in null-sec.
    if (filters.sovereignty && security(node.security) === 'null' && playerSovereignId) layers.push({ id: playerSovereignId, alpha: sovereignty.alliance_id ? .40 : .32, kind: 'sovereignty', offset: layers.length });
    return layers;
  }
  const FACTION_COLORS = { 500001: '#64aefb', 500002: '#e95768', 500003: '#e4bf65', 500004: '#60d99a', 500005: '#b493f2', 500007: '#8d99a7' };
  const influenceColor = id => {
    const known = FACTION_COLORS[Number(id)]; if (known) return known;
    const hue = Math.abs(Number(id) || 0) % 360;
    return `hsl(${hue} 70% 66%)`;
  };
  // The New Eden map uses Y as its up axis.  Keep the panoramic sky in that
  // same frame, with a deliberate quarter-turn yaw so its Milky-Way band wraps
  // around the map rather than cutting across the default overhead shot.
  const SKY_TEXTURE_YAW = Math.PI / 2;
  const SKY_POLE_MARGIN = .035;
  // Preserve the authentic 3D SDE layout while constraining navigation around
  // a stable X/Z ground frame: no pole crossing means right-drag does not turn
  // into accidental depth movement.
  const NEW_EDEN_MIN_POLAR_ANGLE = .12;
  const NEW_EDEN_MAX_POLAR_ANGLE = .56;
  // The ESO panorama is already equirectangular.  WebP keeps the 4K source
  // crisp while avoiding the 72+ MiB decoded cost of the original 6000x3000 TIFF.
  const SKY_TEXTURE_URL = 'data/sky/new-eden-sky-milky-way-4096.webp';
  const SKY_MOVING_FRAME_MS = 110;
  const SKY_SETTLED_FRAME_MS = 72;
  const SKY_TAU = Math.PI * 2;
  const SKY_GPU_MAX_DPR = 1;
  const SKY_GPU_MOVING_SCALE = .72;
  const SKY_GPU_SETTLED_SCALE = 1;
  const SKY_GPU_VERTEX_SHADER = `
    attribute vec2 aPosition;
    varying vec2 vUv;
    void main() {
      vUv = aPosition * .5 + .5;
      gl_Position = vec4(aPosition, 0.0, 1.0);
    }
  `;
  const SKY_GPU_FRAGMENT_SHADER = `
    precision highp float;
    uniform mat3 uCameraRotation;
    uniform mat2 uWorldToSky;
    uniform vec2 uInverseFocal;
    uniform sampler2D uSkyTexture;
    uniform float uSkyDebugMode;
    varying vec2 vUv;
    const float PI = 3.141592653589793;
    // One deterministic candidate per cube-face cell.  This keeps the nearby
    // stellar shell even over the poles, without a CPU star list or a second
    // canvas to repaint while the camera moves.
    vec3 hash33(vec3 value) {
      value = fract(value * vec3(.1031, .11369, .13787));
      value += dot(value, value.yxz + 19.19);
      return fract((value.xxy + value.yzz) * value.zyx);
    }
    vec2 cubeFaceUv(vec3 direction, out float face) {
      vec3 magnitude = abs(direction);
      vec2 coordinate;
      if (magnitude.x >= magnitude.y && magnitude.x >= magnitude.z) {
        float inverseAxis = 1.0 / max(magnitude.x, .0001);
        if (direction.x >= 0.0) { face = 0.0; coordinate = vec2(-direction.z, direction.y) * inverseAxis; }
        else { face = 1.0; coordinate = vec2(direction.z, direction.y) * inverseAxis; }
      } else if (magnitude.y >= magnitude.z) {
        float inverseAxis = 1.0 / max(magnitude.y, .0001);
        if (direction.y >= 0.0) { face = 2.0; coordinate = vec2(direction.x, -direction.z) * inverseAxis; }
        else { face = 3.0; coordinate = vec2(direction.x, direction.z) * inverseAxis; }
      } else {
        float inverseAxis = 1.0 / max(magnitude.z, .0001);
        if (direction.z >= 0.0) { face = 4.0; coordinate = vec2(direction.x, direction.y) * inverseAxis; }
        else { face = 5.0; coordinate = vec2(-direction.x, direction.y) * inverseAxis; }
      }
      return coordinate * .5 + .5;
    }
    vec3 nearStarLayer(vec2 faceUv, float face, float cells, float density, float radius, float seed) {
      vec2 cellCoordinate = faceUv * cells;
      vec2 cell = floor(cellCoordinate);
      vec2 localCoordinate = fract(cellCoordinate);
      vec3 candidate = hash33(vec3(cell, face * 29.17 + seed));
      float exists = 1.0 - step(density, candidate.z);
      vec2 starCoordinate = mix(vec2(.30), vec2(.70), candidate.xy);
      float distanceToStar = length(localCoordinate - starCoordinate);
      float core = 1.0 - smoothstep(radius * .38, radius, distanceToStar);
      float wing = 1.0 - smoothstep(radius, radius * 1.7, distanceToStar);
      float luminosity = .22 + .38 * inversesqrt(sqrt(max(candidate.x, .025)));
      float warm = smoothstep(.62, .97, candidate.y);
      vec3 tint = mix(vec3(.52, .70, 1.0), vec3(1.0, .69, .44), warm);
      return tint * exists * (core + wing * .055) * luminosity;
    }
    // Stable direction -> broad warped cloud mass -> density and colour.
    // This deliberately avoids periodic longitude bands: the panorama owns the
    // distant detail while this field only adds a few natural-looking gas veils.
    float hash31(vec3 point) {
      point = fract(point * .1031);
      point += dot(point, point.yzx + 33.33);
      return fract((point.x + point.y) * point.z);
    }
    float valueNoise3(vec3 point) {
      vec3 cell = floor(point), local = fract(point);
      local = local * local * (3.0 - 2.0 * local);
      float a = hash31(cell + vec3(0., 0., 0.)), b = hash31(cell + vec3(1., 0., 0.));
      float c = hash31(cell + vec3(0., 1., 0.)), d = hash31(cell + vec3(1., 1., 0.));
      float e = hash31(cell + vec3(0., 0., 1.)), f = hash31(cell + vec3(1., 0., 1.));
      float g = hash31(cell + vec3(0., 1., 1.)), h = hash31(cell + vec3(1., 1., 1.));
      return mix(mix(mix(a, b, local.x), mix(c, d, local.x), local.y), mix(mix(e, f, local.x), mix(g, h, local.x), local.y), local.z);
    }
    vec3 nebulaDirection(vec3 direction) {
      vec3 warp = vec3(valueNoise3(direction * 1.13 + vec3(13.7, 2.1, 7.9)), valueNoise3(direction * 1.13 + vec3(3.4, 19.2, 11.6)), valueNoise3(direction * 1.13 + vec3(8.1, 6.3, 23.4))) - .5;
      return normalize(direction + warp * .26);
    }
    float nebulaDensity(vec3 direction) {
      vec3 warped = nebulaDirection(direction);
      float macro = valueNoise3(warped * 1.62 + vec3(4.7, 9.1, 2.5));
      float meso = valueNoise3(warped * 4.20 + vec3(12.6, 3.8, 16.2));
      float galacticSheet = 1.0 - smoothstep(.18, .56, abs(dot(warped, normalize(vec3(.18, .91, -.37)))));
      return galacticSheet * smoothstep(.58, .78, macro * .68 + meso * .32);
    }
    void main() {
      vec2 ndc = vUv * 2.0 - 1.0;
      vec3 viewDirection = normalize(vec3(ndc.x * uInverseFocal.x, ndc.y * uInverseFocal.y, -1.0));
      vec3 worldDirection = uCameraRotation * viewDirection;
      vec2 skyXZ = uWorldToSky * worldDirection.xz;
      vec3 skyDirection = vec3(skyXZ.x, worldDirection.y, skyXZ.y);
      float longitude = .5 + atan(skyDirection.z, skyDirection.x) / (2.0 * PI);
      float latitude = .5 - asin(clamp(skyDirection.y, -1.0, 1.0)) / PI;
      vec3 distantSky = texture2D(uSkyTexture, vec2(fract(longitude), clamp(latitude, 0.0, 1.0))).rgb;
      distantSky = pow(max(distantSky, vec3(0.0)), vec3(1.16)) * .70;
      float darkSpace = 1.0 - smoothstep(.16, .72, max(distantSky.r, max(distantSky.g, distantSky.b)));
      vec3 warpedNebulaDirection = nebulaDirection(skyDirection);
      float nebula = nebulaDensity(skyDirection);
      float warmPocket = smoothstep(.52, .82, valueNoise3(warpedNebulaDirection * 2.15 + vec3(3.1, 14.7, 8.5))) * nebula;
      vec3 gasNebula = vec3(.018, .078, .145) * nebula;
      gasNebula += vec3(.095, .020, .062) * warmPocket;
      // Two sparse layers create nearby, crisp depth cues while the panorama
      // owns the distant Milky-Way detail and the ribbons stay a light accent.
      float nearFace;
      vec2 nearFaceUv = cubeFaceUv(skyDirection, nearFace);
      vec3 nearbyStars = nearStarLayer(nearFaceUv, nearFace, 164.0, .048, .125, 3.0);
      nearbyStars += nearStarLayer(nearFaceUv, nearFace, 72.0, .025, .15, 31.0);
      nearbyStars += nearStarLayer(nearFaceUv, nearFace, 36.0, .007, .19, 67.0);
      if (uSkyDebugMode > 1.5) { gl_FragColor = vec4(nearbyStars * 1.8, 1.0); return; }
      if (uSkyDebugMode > .5) { gl_FragColor = vec4(vec3(nebula), 1.0); return; }
      gl_FragColor = vec4(distantSky + gasNebula * (.20 + darkSpace * .46) + nearbyStars * .68, 1.0);
    }
  `;
  const rotateSkyYaw = (direction, angle) => {
    const cosine = Math.cos(angle), sine = Math.sin(angle);
    return { x: direction.x * cosine + direction.z * sine, y: direction.y, z: -direction.x * sine + direction.z * cosine };
  };
  const skyTextureDirection = direction => rotateSkyYaw(direction, -SKY_TEXTURE_YAW);
  const worldSkyDirection = direction => rotateSkyYaw(direction, SKY_TEXTURE_YAW);
  function stabilizeOrbitControls(controls) {
    if (!controls) return;
    controls.minPolarAngle = NEW_EDEN_MIN_POLAR_ANGLE;
    controls.maxPolarAngle = NEW_EDEN_MAX_POLAR_ANGLE;
    controls.screenSpacePanning = false;
    controls.enablePan = true;
    controls.object?.up?.set?.(0, 1, 0);
  }
  function bindSkyQualityControls(controls) {
    if (!controls?.addEventListener) return;
    controls.addEventListener('start', () => { state.skyInteracting = true; });
    controls.addEventListener('end', () => {
      state.skyInteracting = false;
      // The next pass redraws the same view at the settled quality rather than
      // leaving the lower-cost interaction frame on screen.
      state.skyFrameKey = null;
      state.skyLastPaintAt = 0;
    });
  }
  const ageText = seconds => seconds == null ? 'indisponible' : seconds < 60 ? 'à l’instant' : `${Math.max(1, Math.round(seconds / 60))} min`;
  const trafficSize = jumps => Math.min(2.15, 1 + Math.log1p(Math.max(0, jumps)) / Math.log(50000) * 1.15);
  const nodeSize = node => (node.security >= .5 ? 1.1 : 1) * (state.filters.traffic ? trafficSize(liveFor(node).ship_jumps) : 1);
  function buildGateDegrees(gates) {
    const degrees = new Map();
    gates.forEach(gate => { degrees.set(gate.source, (degrees.get(gate.source) || 0) + 1); degrees.set(gate.target, (degrees.get(gate.target) || 0) + 1); });
    return degrees;
  }
  function estimateSystemGateFlow(system, degrees, jumpsBySystem) {
    const jumps = Number(jumpsBySystem.get(system.id) ?? jumpsBySystem.get(String(system.id)) ?? 0);
    return Math.round(Math.max(0, jumps) / Math.max(1, degrees.get(system.id) || 1));
  }
  function estimateGateFlows(source, target, degrees, jumpsBySystem) {
    // ESI exposes aggregate jumps per solar system, not a count per stargate.
    // Treat each endpoint as an independent outbound flow distributed over its
    // gates: this yields an explicitly bidirectional, visual estimate.
    return {
      sourceToTarget: estimateSystemGateFlow(source, degrees, jumpsBySystem),
      targetToSource: estimateSystemGateFlow(target, degrees, jumpsBySystem),
    };
  }
  function estimateGateTraffic(source, target, degrees, jumpsBySystem) {
    const flows = estimateGateFlows(source, target, degrees, jumpsBySystem);
    return Math.round((flows.sourceToTarget + flows.targetToSource) / 2);
  }
  function trafficParticlePlan(estimatedJumps) {
    const value = Math.max(0, Number(estimatedJumps) || 0);
    if (value < 1) return { count: 0, particles: [] };
    // Each particle is a capacity tier, not another copy of the same flow:
    // 1–100, 101–1000, and 1001–9999. Completed tiers stay red while the
    // active tier evolves from green to red.
    const tierColor = (minimum, maximum) => {
      const t = Math.max(0, Math.min(1, (value - minimum) / (maximum - minimum)));
      return `hsl(${Math.round(142 - 130 * t)} 84% 66%)`;
    };
    const particles = [{ tier: 1, color: tierColor(1, 100), opacity: .70 }];
    if (value > 100) particles.push({ tier: 2, color: tierColor(101, 1000), opacity: .80 });
    if (value > 1000) particles.push({ tier: 3, color: tierColor(1001, 9999), opacity: .88 });
    return { count: particles.length, particles, jumps: value };
  }
  const trafficPacketOffsets = count => Array.from({ length: count }, (_, index) => index * .052);
  const trafficParticleSpeed = jumps => 5 + 18 * Math.pow(Math.max(0, Math.min(1, Math.log10(Math.max(1, jumps)) / 4)), .78);
  const trafficTransitSeconds = (jumps, worldLength) => Math.max(.7, Math.min(6, Math.max(1, worldLength) / trafficParticleSpeed(jumps)));
  const trafficParticleProgress = (now, phase, jumps, worldLength) => {
    // The travel period is derived from stable map-space coordinates, never
    // from the projected canvas length: moving the camera must not alter time.
    const transitSeconds = trafficTransitSeconds(jumps, worldLength);
    return (now / 1000 / transitSeconds + phase) % 1;
  };
  const trafficSegmentWorldLength = segment => {
    const source = segment?.source, target = segment?.target;
    if (!source || !target) return 1;
    return Math.hypot(Number(target.x) - Number(source.x), Number(target.y) - Number(source.y), Number(target.z) - Number(source.z));
  };
  const trafficSegmentProgress = (now, phase, jumps, segment) => {
    const explicitLength = Number(segment?.worldLength);
    return trafficParticleProgress(now, phase, jumps, explicitLength > 0 ? explicitLength : trafficSegmentWorldLength(segment));
  };
  const trafficParticleVisualStyle = particle => ({
    glowRadius: 3.6,
    glowAlpha: .22 * Number(particle?.opacity || 0),
    coreRadius: 1.36,
    coreAlpha: Math.min(1, .42 + Number(particle?.opacity || 0) * .72),
  });
  // Systems may gain a little screen presence at galaxy scale, but never enough
  // to compete with the explicitly selected character marker.
  const systemObjectScale = distance => Math.max(.7, Math.min(1.05, .7 + Math.log1p(Math.max(distance, 1) / 180) / 18));
  const characterMarkerScale = distance => Math.max(1, Math.min(3.5, .88 + Math.pow(Math.max(distance, 1) / 120, .5)));
  const MAX_SYSTEM_RADIUS_PX = 7;
  const systemScreenRadius = distance => {
    // Local systems should be easy to read around the camera; remote systems
    // must rapidly relinquish visual weight.  This is intentionally based on
    // camera distance, not only global zoom, so foreground and background can
    // coexist without becoming one flat cotton-ball layer.
    return .11 + 4 * Math.exp(-Math.max(0, distance) / 520);
  };
  const influenceOverlayOpacity = distance => .50 + .40 * Math.exp(-Math.max(0, distance) / 1500);
  const influenceOverlayStyle = (distance, kind) => {
    const far = Math.max(0, Math.min(1, (distance - 300) / 2200));
    if (kind === 'empire') {
      // Static NPC/faction space is a real filter, not a secondary hint: its
      // disc clears the largest rendered system node even at close range.
      return { radius: MAX_SYSTEM_RADIUS_PX + 1.5 + far * 3.0, opacity: influenceOverlayOpacity(distance) };
    }
    // Player Sov retains a slightly larger territorial mass, but both filters
    // share their opacity so one does not disappear when switching overlays.
    return { radius: MAX_SYSTEM_RADIUS_PX + 2.7 + far * 3.8, opacity: influenceOverlayOpacity(distance) };
  };
  const influenceOverlayRadius = (distance, kind) => influenceOverlayStyle(distance, kind).radius;
  function cappedSystemObjectScale(object, distance, camera, viewportHeight) {
    const desired = systemObjectScale(distance);
    const geometry = object.geometry || object.children?.find(child => child.geometry)?.geometry;
    const worldRadius = geometry?.boundingSphere?.radius || geometry?.parameters?.radius || 1;
    const pixelsPerWorld = viewportHeight * Math.abs(camera.projectionMatrix?.elements?.[5] || 1) / (2 * Math.max(distance, .001));
    const preferredRadius = worldRadius * desired * pixelsPerWorld;
    const targetRadius = Math.max(systemScreenRadius(distance), Math.min(MAX_SYSTEM_RADIUS_PX, preferredRadius));
    return targetRadius / Math.max(.000001, worldRadius * pixelsPerWorld);
  }
  function nodeScaleKey(camera, viewportHeight) {
    const position = camera.position, projection = camera.projectionMatrix?.elements;
    if (!position || !projection) return null;
    return `${Math.round(position.x * 40)}:${Math.round(position.y * 40)}:${Math.round(position.z * 40)}:${Math.round(projection[5] * 800)}:${viewportHeight}`;
  }
  function updateSystemScreenScales(nodes, camera, viewportHeight) {
    const key = nodeScaleKey(camera, viewportHeight);
    if (key && key === state.nodeScaleKey) return;
    nodes.forEach(node => {
      const object = node.__threeObj;
      if (!object?.scale?.setScalar) return;
      const distance = Math.hypot(camera.position.x - node.x, camera.position.y - node.y, camera.position.z - node.z);
      object.scale.setScalar(cappedSystemObjectScale(object, distance, camera, viewportHeight));
    });
    state.nodeScaleKey = key;
  }
  const dangerClass = system => `eve-route-danger--${liveFor(system).danger_band}`;
  const axes = ['x', 'y', 'z'];
  function buildSkyStars(count = 3000) {
    let seed = 0x5eede11;
    const random = () => { seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 0x100000000; };
    return Array.from({ length: count }, () => {
      // The sky is a full directional sphere.  The Milky Way-like band is an
      // accent, not the whole field, so looking above/below New Eden remains rich.
      const longitude = random() * Math.PI * 2, band = random() < .30;
      const latitude = band ? Math.sin(longitude * 2.3 - .7) * .19 + (random() - .5) * .42 : Math.asin(random() * 2 - 1);
      const cosLatitude = Math.cos(latitude);
      return { ...worldSkyDirection({ x: cosLatitude * Math.cos(longitude), y: Math.sin(latitude), z: cosLatitude * Math.sin(longitude) }), band, radius: random() < .025 ? 1.15 + random() * 1.25 : .2 + random() * .65, alpha: band ? .2 + random() * .68 : .1 + random() * .48, tone: random() > .88 ? 'warm' : random() > .72 ? 'blue' : 'white' };
    });
  }
  function buildAdjacency(systems, gates) {
    const systemsById = new Map(systems.map(system => [system.id, system]));
    const adjacency = new Map(systems.map(system => [system.id, new Set()]));
    gates.forEach(gate => { adjacency.get(gate.source)?.add(gate.target); adjacency.get(gate.target)?.add(gate.source); });
    return { systemsById, adjacency };
  }
  function findConnectedComponents(systems, systemsById, adjacency) {
    const components = [], visited = new Set();
    systems.forEach(system => {
      if (visited.has(system.id)) return;
      const component = [], queue = [system.id]; visited.add(system.id);
      while (queue.length) {
        const id = queue.pop(); component.push(systemsById.get(id));
        adjacency.get(id).forEach(next => { if (!visited.has(next)) { visited.add(next); queue.push(next); } });
      }
      components.push(component);
    });
    return components;
  }
  function calculateMainBounds(systems) {
    const bounds = systems.reduce((box, system) => {
      axes.forEach(axis => { box.min[axis] = Math.min(box.min[axis], system.position_m[axis]); box.max[axis] = Math.max(box.max[axis], system.position_m[axis]); });
      return box;
    }, { min: { x: Infinity, y: Infinity, z: Infinity }, max: { x: -Infinity, y: -Infinity, z: -Infinity } });
    const centre = Object.fromEntries(axes.map(axis => [axis, (bounds.min[axis] + bounds.max[axis]) / 2]));
    const span = Math.max(...axes.map(axis => bounds.max[axis] - bounds.min[axis]));
    return { centre, scale: 2200 / span };
  }
  function layoutMainCluster(main, centre, scale, positions) {
    main.forEach(system => positions.set(system.id, Object.fromEntries(axes.map(axis => [axis, (system.position_m[axis] - centre[axis]) * scale]))));
  }
  function layoutDetachedRegions(systems, mainIds, mainCentre, mainScale, positions) {
    const groups = new Map();
    systems.filter(system => !mainIds.has(system.id)).forEach(system => {
      const key = system.region_id || system.id, group = groups.get(key) || []; group.push(system); groups.set(key, group);
    });
    [...groups.values()].forEach((group, index) => {
      const centre = Object.fromEntries(axes.map(axis => [axis, group.reduce((sum, system) => sum + system.position_m[axis], 0) / group.length]));
      const extent = Math.max(1, ...group.flatMap(system => axes.map(axis => Math.abs(system.position_m[axis] - centre[axis]))));
      const direction = axes.map(axis => centre[axis] - mainCentre[axis]), length = Math.hypot(...direction) || 1, angle = index * 2.3999632297;
      const fallback = [Math.cos(angle), Math.sin(angle), Math.sin(angle * .5)];
      const anchor = Object.fromEntries(axes.map((axis, axisIndex) => [axis, (length > 1 ? direction[axisIndex] / length : fallback[axisIndex]) * 1280]));
      const scale = Math.min(mainScale, 190 / extent);
      group.forEach(system => positions.set(system.id, Object.fromEntries(axes.map(axis => [axis, anchor[axis] + (system.position_m[axis] - centre[axis]) * scale]))));
    });
  }
  function buildRenderNodes(systems, positions) {
    return systems.map(system => Object.assign({}, system, positions.get(system.id), { fx: positions.get(system.id).x, fy: positions.get(system.id).y, fz: positions.get(system.id).z }));
  }
  function displayNodes(systems, gates) {
    const { systemsById, adjacency } = buildAdjacency(systems, gates);
    const components = findConnectedComponents(systems, systemsById, adjacency);
    const main = components.reduce((largest, component) => component.length > largest.length ? component : largest, []);
    const { centre, scale } = calculateMainBounds(main), positions = new Map(), mainIds = new Set(main.map(system => system.id));
    layoutMainCluster(main, centre, scale, positions);
    layoutDetachedRegions(systems, mainIds, centre, scale, positions);
    return buildRenderNodes(systems, positions);
  }
  function buildLabelGroups(nodes, idKey, nameKey) {
    const groups = new Map();
    nodes.forEach(node => {
      const id = node[idKey], name = node[nameKey]; if (!name) return;
      const group = groups.get(id) || { id, name, x: 0, y: 0, z: 0, min: { x: Infinity, y: Infinity, z: Infinity }, max: { x: -Infinity, y: -Infinity, z: -Infinity }, count: 0, securityCounts: { high: 0, low: 0, null: 0 } };
      group.x += node.x; group.y += node.y; group.z += node.z; group.count += 1;
      group.securityCounts[security(node.security)] += 1;
      axes.forEach(axis => { group.min[axis] = Math.min(group.min[axis], node[axis]); group.max[axis] = Math.max(group.max[axis], node[axis]); }); groups.set(id, group);
    });
    return [...groups.values()].map(group => ({ ...group, x: group.x / group.count, y: group.y / group.count, z: group.z / group.count, span: Math.max(...axes.map(axis => group.max[axis] - group.min[axis])) }));
  }
  const visibleLabelGroup = (group, filters = state.filters) => Object.entries(group.securityCounts || {}).some(([kind, count]) => count > 0 && filters[kind]);
  const labelsCanOverlap = mode => mode === 'region';
  const labelFade = (distance, start, end) => Math.max(0, Math.min(1, (distance - start) / (end - start)));
  function mapLabelLayers(nearestDistance) {
    const regionOpacity = labelFade(nearestDistance, 700, 1100);
    const systemOpacity = 1 - labelFade(nearestDistance, 180, 300);
    const constellationOpacity = Math.min(1 - regionOpacity, 1 - systemOpacity);
    return [
      { mode: 'region', opacity: regionOpacity },
      { mode: 'constellation', opacity: constellationOpacity },
      { mode: 'system', opacity: systemOpacity },
    ].filter(layer => layer.opacity > .01);
  }
  function mapLabelPlacement(mode, point, textWidth, width, height) {
    let x = mode === 'system' ? point.x - 7 : point.x;
    let y = mode === 'system' ? point.y - 7 : point.y;
    if (mode === 'region') {
      const padding = 6, halfWidth = textWidth / 2;
      x = Math.max(halfWidth + padding, Math.min(width - halfWidth - padding, x));
      y = Math.max(12, Math.min(height - 12, y));
    }
    return { x, y };
  }
  function drawMapLabelLayer(context, camera, width, height, mode, opacity) {
    const labels = (mode === 'region' ? state.labelGroups?.regions : mode === 'constellation' ? state.labelGroups?.constellations : [...state.nodesById.values()].filter(visibleNode))?.filter(label => (mode === 'system' ? isAreaMember(label) : visibleLabelGroup(label)) && !(state.areaFocus?.kind === mode && label.id !== state.areaFocus.area.id));
    if (!labels) return;
    const minSpacing = mode === 'region' ? 92 : mode === 'constellation' ? 64 : 48, drawn = [];
    context.save(); context.textAlign = mode === 'system' ? 'right' : 'center'; context.textBaseline = mode === 'system' ? 'bottom' : 'middle';
    context.font = mode === 'region' ? '600 11px system-ui, sans-serif' : mode === 'constellation' ? '10px system-ui, sans-serif' : '10px system-ui, sans-serif';
    context.fillStyle = mode === 'region' ? 'rgba(177, 235, 245, .72)' : mode === 'constellation' ? 'rgba(166, 207, 220, .52)' : 'rgba(222, 244, 249, .76)';
    context.globalAlpha = opacity;
    labels.slice().sort((a, b) => b.count - a.count).forEach(label => {
      const point = projectVisible(label, camera, width, height); if (!point || point.x < 0 || point.x > width || point.y < 0 || point.y > height || (!labelsCanOverlap(mode) && drawn.some(other => Math.hypot(other.x - point.x, other.y - point.y) < minSpacing))) return;
      const text = mode === 'system' ? label.name : label.name.toUpperCase();
      const measure = context.measureText(text).width;
      const { x: labelX, y: labelY } = mapLabelPlacement(mode, point, measure, width, height);
      context.fillText(text, labelX, labelY); drawn.push(point);
      state.labelHitTargets.push({ kind: mode, target: label, x: mode === 'system' ? labelX - measure / 2 : labelX, y: labelY, width: measure, height: 14 });
    });
    context.restore();
  }
  function drawMapLabels(context, camera, width, height, nearestDistance) {
    state.labelHitTargets = [];
    mapLabelLayers(nearestDistance).forEach(layer => drawMapLabelLayer(context, camera, width, height, layer.mode, layer.opacity));
  }
  const gateKey = (source, target) => [source, target].sort((a, b) => a - b).join(':');
  const influenceFor = system => {
    const sov = sovereigntyFor(system);
    if (sov.alliance_id) return { id: sov.alliance_id, kind: 'Alliance' };
    if (sov.corporation_id) return { id: sov.corporation_id, kind: 'Corporation' };
    if (sov.faction_id) return { id: sov.faction_id, kind: 'Faction' };
    return system.faction_id ? { id: system.faction_id, kind: 'Faction' } : null;
  };
  const shipIconUrl = shipTypeId => Number.isSafeInteger(Number(shipTypeId)) && Number(shipTypeId) > 0 ? `https://images.evetech.net/types/${Number(shipTypeId)}/icon?size=64` : null;
  const formatKillDate = value => {
    const match = String(value || '').match(/^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/);
    return match ? `${match[1]} · ${match[2]} UTC` : 'Date inconnue';
  };
  const killLocation = (kill, fallbackSystemId) => {
    const system = systemFor(kill.solar_system_id || fallbackSystemId);
    return system ? `${system.name} · ${system.constellation}, ${system.region}` : `System ${kill.solar_system_id || fallbackSystemId || '—'}`;
  };
  async function loadInfluenceName(influence, systemId) {
    if (!influence || !api()?.get_eve_map_entity_names) return;
    try {
      const response = await api().get_eve_map_entity_names([influence.id]);
      if (state.selectedSystemId !== systemId) return;
      const name = response.names?.[String(influence.id)]?.name;
      const target = document.querySelector(`.eve-map-influence[data-eve-influence-id="${influence.id}"]`);
      if (name && target) target.textContent = `${influence.kind} · ${name}`;
    } catch (_) { /* The ID remains a safe fallback when public ESI is unavailable. */ }
  }
  function showSystem(system, { loadDetails = true } = {}) {
    const panel = document.getElementById('eve-map-panel');
    const live = liveFor(system), stale = state.live.state === 'stale';
    const pilots = state.characters.filter(character => character.system_id === system.id).map(character => `<span class="eve-pilot"><i style="background:${characterColor(character)}"></i>${escapeHtml(character.name)}</span>`);
    const influence = influenceFor(system);
    state.selectedSystemId = system.id;
    panel.querySelector('.eve-map-empty')?.remove(); panel.querySelector('.eve-map-system')?.remove();
    panel.insertAdjacentHTML('afterbegin', `<div class="eve-map-system"><h3>${escapeHtml(system.name)} <small>${system.security.toFixed(1)}</small></h3><dl><dt>Région</dt><dd>${escapeHtml(system.region)}</dd><dt>Constellation</dt><dd>${escapeHtml(system.constellation)}</dd>${influence ? `<dt>Influence</dt><dd class="eve-map-influence" data-eve-influence-id="${influence.id}">${escapeHtml(influence.kind)} · ${influence.id}</dd>` : ''}<dt>ID</dt><dd>${system.id}</dd>${pilots.length ? `<dt>Pilotes actifs</dt><dd>${pilots.join('<br>')}</dd>` : ''}</dl><section class="eve-live-intel"><h4>LIVE INTEL</h4><dl><dt>Traffic</dt><dd>${Number(live.ship_jumps).toLocaleString('fr-BE')} jumps</dd><dt>Kills</dt><dd>Ships ${live.ship_kills} · Pods ${live.pod_kills} · NPC ${live.npc_kills}</dd><dt>Danger</dt><dd><span class="eve-danger-meter eve-danger-meter--${live.danger_band}"><i style="width:${live.danger}%"></i></span> ${live.danger}/100</dd></dl><small class="eve-live-updated">${stale ? 'Live Intel stale · ' : ''}Updated ${ageText(state.live.age_seconds)}</small><div class="eve-map-latest" data-system-id="${system.id}"><small>Derniers kills zKill : chargement…</small></div></section><div class="eve-map-actions"><button class="btn mini" onclick="setEveMapOrigin(${system.id})">Définir origine</button><button class="btn mini" onclick="setEveMapDestination(${system.id})">Définir destination</button></div></div>`);
    if (loadDetails) { loadInfluenceName(influence, system.id); loadRecentKills(system.id); }
  }
  const visibleNode = node => state.filters[security(node.security)];
  const isAreaMember = (node, focus = state.areaFocus) => !focus || (focus.kind === 'region' ? node.region_id === focus.area.id : node.constellation_id === focus.area.id);
  const baseNodeColor = node => node.security >= .5 ? '#36d7a0' : node.security > 0 ? '#efb546' : '#e45878';
  const focusNodeColor = (node, focus = state.areaFocus) => isAreaMember(node, focus) ? baseNodeColor(node) : (node.security >= .5 ? '#23675b' : node.security > 0 ? '#745d31' : '#71394a');
  function render() { if (!state.graph) return; state.graph.nodeVisibility(visibleNode).nodeColor(focusNodeColor).nodeVal(node => nodeSize(node) * (isAreaMember(node) ? 1.22 : .84)).nodeOpacity(.96).nodeLabel(node => `${node.name} · ${node.security.toFixed(2)}${state.filters.traffic ? ` · ${Number(liveFor(node).ship_jumps).toLocaleString('fr-BE')} jumps` : ''}${state.filters.danger ? ` · Danger ${liveFor(node).danger}/100` : ''}`).linkVisibility(link => state.filters.gates && visibleNode(link.source) && visibleNode(link.target)); }
  async function loadLiveIntel() {
    if (!api()?.get_eve_map_live_intel) return;
    try {
      const response = await api().get_eve_map_live_intel();
      state.live = { systems: response.systems || {}, state: response.state || (response.ok ? 'live' : 'unavailable'), updated_at: response.updated_at || null, age_seconds: response.age_seconds };
      state.trafficBySystem = new Map(Object.entries(state.live.systems).map(([id, values]) => [Number(id), Number(values.ship_jumps) || 0]));
    } catch (_) { state.live = { systems: {}, state: 'unavailable', updated_at: null }; }
    render();
    if (state.selectedSystemId) showSystem(systemFor(state.selectedSystemId), { loadDetails: false });
  }
  async function loadCombatMarkers() {
    if (!api()?.get_eve_map_combat_markers) return;
    try {
      const response = await api().get_eve_map_combat_markers();
      const now = Date.now();
      (response.markers || []).forEach(marker => {
        const happenedAt = Number(marker.happened_at) * 1000;
        if (!Number.isFinite(happenedAt) || now - happenedAt > COMBAT_MARKER_TTL_MS) return;
        state.combatMarkers.set(`zkb:${marker.killmail_id}`, { ...marker, happenedAt, expiresAt: happenedAt + COMBAT_MARKER_TTL_MS });
      });
    } catch (_) { /* The static map remains fully usable when R2Z2 is offline. */ }
  }
  function startCombatStream() {
    const bridge = api();
    // Keep the renderer independently testable and compatible with an older
    // backend: combat is optional, the topology never is.
    if (!bridge?.get_eve_map_combat_markers || !bridge?.set_eve_map_combat_stream_active) return;
    bridge.set_eve_map_combat_stream_active(true);
    loadCombatMarkers();
    if (state.combatMarkerTimer == null) state.combatMarkerTimer = setInterval(loadCombatMarkers, 10_000);
  }
  function stopCombatStream() {
    if (state.combatMarkerTimer != null) clearInterval(state.combatMarkerTimer);
    state.combatMarkerTimer = null;
    api()?.set_eve_map_combat_stream_active?.(false);
  }
  async function loadSovereignty() {
    const indicator = document.getElementById('eve-map-sovereignty-state');
    if (!api()?.get_eve_map_sovereignty) { if (indicator) indicator.textContent = 'unavailable'; return; }
    if (indicator) indicator.textContent = 'loading…';
    try {
      const response = await api().get_eve_map_sovereignty();
      state.sovereignty = { systems: response.systems || {}, state: response.state || (response.ok ? 'live' : 'unavailable'), updated_at: response.updated_at || null, age_seconds: response.age_seconds };
    } catch (_) { state.sovereignty = { systems: {}, state: 'unavailable', updated_at: null }; }
    if (indicator) {
      const count = Object.keys(state.sovereignty.systems).length;
      indicator.textContent = state.sovereignty.state === 'live' || state.sovereignty.state === 'fresh' ? `${count.toLocaleString('fr-BE')} systems` : state.sovereignty.state === 'stale' ? `stale · ${count.toLocaleString('fr-BE')}` : 'unavailable';
    }
    render();
    if (state.selectedSystemId) showSystem(systemFor(state.selectedSystemId));
  }
  const characterPositionKey = positions => positions.map(character => `${character.character_id}:${character.system_id}`).sort().join('|');
  const characterPositionSystemId = characterId => state.characters.find(character => String(character.character_id) === String(characterId))?.system_id ?? null;
  async function loadCharacterPositions() {
    if (!api()?.get_eve_map_character_positions) return false;
    const previousKey = characterPositionKey(state.characters), selectedCharacterId = state.selectedCharacterId, previousSelectedSystemId = characterPositionSystemId(selectedCharacterId);
    try { const response = await api().get_eve_map_character_positions(); state.characters = response.positions || []; }
    catch (_) { state.characters = []; }
    const select = document.getElementById('eve-map-character');
    if (select) { select.innerHTML = '<option value="">Tous les pilotes</option>'; state.characters.forEach(character => { const option = document.createElement('option'); option.value = character.character_id; option.textContent = `${character.name} · ${systemFor(character.system_id)?.name || 'hors carte'}`; select.appendChild(option); }); }
    if (state.selectedSystemId) showSystem(systemFor(state.selectedSystemId));
    const selectedSystemId = characterPositionSystemId(selectedCharacterId);
    if (selectedCharacterId && previousSelectedSystemId != null && selectedSystemId != null && previousSelectedSystemId !== selectedSystemId) focus(systemFor(selectedSystemId));
    return previousKey !== characterPositionKey(state.characters);
  }
  function stopCharacterPositionTracking() {
    if (state.characterTrackingTimer != null) clearTimeout(state.characterTrackingTimer);
    state.characterTrackingTimer = null;
  }
  function scheduleCharacterPositionTracking() {
    const interval = characterPositionTrackingInterval(state.visible);
    if (interval == null || state.characterTrackingTimer != null) return;
    state.characterTrackingTimer = setTimeout(async () => {
      state.characterTrackingTimer = null;
      await loadCharacterPositions();
      scheduleCharacterPositionTracking();
    }, interval);
  }
  function startCharacterPositionTracking() {
    stopCharacterPositionTracking();
    scheduleCharacterPositionTracking();
  }
  async function focusCharacterPosition(characterId) {
    if (!state.visible || !state.graph || characterId == null) return false;
    await loadCharacterPositions();
    const character = state.characters.find(row => String(row.character_id) === String(characterId));
    if (!character) return false;
    state.selectedCharacterId = String(character.character_id);
    const select = document.getElementById('eve-map-character');
    if (select) select.value = state.selectedCharacterId;
    startCharacterPositionTracking();
    focus(systemFor(character.system_id));
    return true;
  }
  function clearCharacterSelection() {
    state.selectedCharacterId = null;
    const select = document.getElementById('eve-map-character');
    if (select) select.value = '';
  }
  async function loadRecentKills(systemId) {
    if (!api()?.get_eve_map_recent_kills) return;
    const box = document.querySelector(`.eve-map-latest[data-system-id="${systemId}"]`);
    if (!box) return;
    try {
      const response = await api().get_eve_map_recent_kills(systemId);
      if (state.selectedSystemId !== systemId) return;
      const kills = response.kills || [];
      box.innerHTML = kills.length ? `<h4>LATEST KILLS</h4>${kills.map(kill => {
        const icon = shipIconUrl(kill.ship_type_id);
        const image = icon ? `<img class="eve-kill-ship-icon" src="${icon}" alt="" loading="lazy" onerror="this.remove()">` : '';
        return `<a class="eve-kill" data-killmail-id="${Number(kill.killmail_id) || 0}" href="${escapeHtml(kill.url)}" target="_blank" rel="noopener">${image}<span><b>${formatKillDate(kill.time)} · ${Math.round(Number(kill.value || 0) / 1e6)} M ISK</b><small>${escapeHtml(killLocation(kill, systemId))}</small></span></a>`;
      }).join('')}` : '<small>Derniers kills zKill indisponibles.</small>';
      bindKillHover(box, systemId);
    } catch (_) { box.textContent = 'Derniers kills zKill indisponibles.'; }
  }
  function ensureKillPopover() {
    if (state.killPopover) return state.killPopover;
    const popover = document.createElement('div');
    popover.className = 'eve-kill-popover'; popover.setAttribute('role', 'tooltip');
    (document.body || mapWorkspace()).appendChild(popover); state.killPopover = popover;
    return popover;
  }
  function positionKillPopover(anchor, content) {
    const popover = ensureKillPopover();
    popover.innerHTML = content; popover.style.display = 'block';
    const rect = anchor.getBoundingClientRect(), width = Math.min(320, Math.max(230, popover.offsetWidth || 280));
    popover.style.left = `${Math.max(8, Math.min(window.innerWidth - width - 8, rect.left - width - 10))}px`;
    popover.style.top = `${Math.max(8, Math.min(window.innerHeight - 120, rect.top - 4))}px`;
  }
  function hideKillPopover(key) {
    window.setTimeout(() => {
      if (state.killHoverKey !== key) return;
      state.killHoverKey = null;
      if (state.killPopover) state.killPopover.style.display = 'none';
    }, 90);
  }
  const attackerPopoverMarkup = response => {
    const attackers = response.attackers || [];
    if (!attackers.length) return '<h4>ATTAQUANTS</h4><small>Détails indisponibles.</small>';
    const heading = response.total_attackers > attackers.length ? `ATTAQUANTS · ${attackers.length}/${response.total_attackers}` : `ATTAQUANTS · ${attackers.length}`;
    return `<h4>${heading}</h4>${attackers.map(attacker => {
      const icon = shipIconUrl(attacker.ship_type_id);
      return `<div class="eve-kill-attacker">${icon ? `<img src="${icon}" alt="" loading="lazy" onerror="this.remove()">` : ''}<span><b>${escapeHtml(attacker.pilot_name || 'Unknown pilot')}</b><small>${escapeHtml(attacker.ship_name || 'Unknown ship')}${attacker.final_blow ? ' · final blow' : ''}</small></span><em>${Number(attacker.damage_done || 0).toLocaleString('fr-BE')}</em></div>`;
    }).join('')}`;
  };
  function bindKillHover(box, systemId) {
    if (!api()?.get_eve_map_kill_attackers) return;
    box.querySelectorAll('.eve-kill[data-killmail-id]').forEach(anchor => {
      const killmailId = Number(anchor.dataset.killmailId);
      if (!killmailId) return;
      const key = `${systemId}:${killmailId}`;
      anchor.addEventListener('pointerenter', async () => {
        state.killHoverKey = key;
        positionKillPopover(anchor, '<h4>ATTAQUANTS</h4><small>Chargement…</small>');
        try {
          const response = await api().get_eve_map_kill_attackers(systemId, killmailId);
          if (state.killHoverKey === key) positionKillPopover(anchor, attackerPopoverMarkup(response));
        } catch (_) {
          if (state.killHoverKey === key) positionKillPopover(anchor, '<h4>ATTAQUANTS</h4><small>Détails indisponibles.</small>');
        }
      });
      anchor.addEventListener('pointerleave', () => hideKillPopover(key));
    });
  }
  function stopGateOverlay() { if (state.linkFrame) cancelAnimationFrame(state.linkFrame); state.linkFrame = null; }
  function startSkyMap(host) {
    if (state.skyCanvas) return;
    const canvas = document.createElement('canvas'); canvas.className = 'eve-map-sky'; canvas.setAttribute('aria-hidden', 'true');
    Object.assign(canvas.style, { position: 'absolute', inset: '0', zIndex: '0', pointerEvents: 'none' });
    host.appendChild(canvas); state.skyCanvas = canvas; state.skyRenderer = createSkyRenderer(canvas); state.skyStars = state.skyRenderer ? [] : buildSkyStars(); state.skyFrameKey = null; state.skyLastPaintAt = 0; state.skyInteracting = false;
    if (state.skyRenderer && state.skyTextureImage) uploadSkyTexture(state.skyRenderer, state.skyTextureImage);
    loadSkyTexture(); startSkyFrame();
  }
  function compileSkyShader(gl, type, source) {
    const shader = gl.createShader(type); gl.shaderSource(shader, source); gl.compileShader(shader);
    if (gl.getShaderParameter(shader, gl.COMPILE_STATUS)) return shader;
    gl.deleteShader(shader); return null;
  }
  function createSkyRenderer(canvas) {
    if (!canvas?.getContext) return null;
    let gl;
    try {
      const options = { alpha: false, antialias: false, depth: false, stencil: false, preserveDrawingBuffer: false, powerPreference: 'high-performance' };
      gl = canvas.getContext('webgl', options) || canvas.getContext('experimental-webgl', options);
    } catch (_) { return null; }
    if (!gl) return null;
    const vertex = compileSkyShader(gl, gl.VERTEX_SHADER, SKY_GPU_VERTEX_SHADER), fragment = compileSkyShader(gl, gl.FRAGMENT_SHADER, SKY_GPU_FRAGMENT_SHADER);
    if (!vertex || !fragment) return null;
    const program = gl.createProgram(); gl.attachShader(program, vertex); gl.attachShader(program, fragment); gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) { gl.deleteProgram(program); return null; }
    const position = gl.getAttribLocation(program, 'aPosition');
    const locations = { cameraRotation: gl.getUniformLocation(program, 'uCameraRotation'), worldToSky: gl.getUniformLocation(program, 'uWorldToSky'), inverseFocal: gl.getUniformLocation(program, 'uInverseFocal'), texture: gl.getUniformLocation(program, 'uSkyTexture'), debugMode: gl.getUniformLocation(program, 'uSkyDebugMode') };
    if (position < 0 || Object.values(locations).some(location => location == null)) { gl.deleteProgram(program); return null; }
    const buffer = gl.createBuffer(), texture = gl.createTexture();
    if (!buffer || !texture) { gl.deleteProgram(program); return null; }
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    gl.bindTexture(gl.TEXTURE_2D, texture); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE); gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array([3, 10, 18, 255]));
    gl.useProgram(program); gl.enableVertexAttribArray(position); gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0); gl.uniform1i(locations.texture, 0); gl.disable(gl.DEPTH_TEST); gl.disable(gl.CULL_FACE); gl.disable(gl.BLEND);
    const yaw = -SKY_TEXTURE_YAW;
    return { canvas, gl, program, buffer, position, locations, texture, cameraRotation: new Float32Array(9), worldToSky: new Float32Array([Math.cos(yaw), -Math.sin(yaw), Math.sin(yaw), Math.cos(yaw)]) };
  }
  function uploadSkyTexture(renderer, image) {
    if (!renderer || !image) return false;
    try {
      const { gl, texture } = renderer;
      gl.bindTexture(gl.TEXTURE_2D, texture); gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true); gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, image); gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
      return true;
    } catch (_) { return false; }
  }
  function cacheSkyTexturePixels(image) {
    const source = document.createElement('canvas'); source.width = image.naturalWidth; source.height = image.naturalHeight;
    const sourceContext = source.getContext('2d', { willReadFrequently: true });
    if (!sourceContext) throw new Error('Sky texture canvas is unavailable');
    sourceContext.drawImage(image, 0, 0);
    state.skyTexture = { width: source.width, height: source.height, pixels: sourceContext.getImageData(0, 0, source.width, source.height).data };
  }
  function loadSkyTexture() {
    if (state.skyTextureImage || state.skyTextureLoading || typeof Image !== 'function') return;
    state.skyTextureLoading = true;
    const image = new Image();
    image.decoding = 'async';
    image.onload = () => {
      try {
        if (!image.naturalWidth || !image.naturalHeight) throw new Error('Sky texture has no dimensions');
        state.skyTextureImage = image;
        if (!uploadSkyTexture(state.skyRenderer, image)) cacheSkyTexturePixels(image);
      } catch (_) { state.skyTexture = null; }
      state.skyTextureLoading = false; state.skyFrameKey = null; state.skyLastPaintAt = 0;
    };
    image.onerror = () => { state.skyTextureLoading = false; };
    image.src = SKY_TEXTURE_URL;
  }
  function stopSkyFrame() { if (state.skyFrame) cancelAnimationFrame(state.skyFrame); state.skyFrame = null; }
  function startSkyFrame() {
    if (!state.skyRenderer || state.skyFrame) return;
    const frame = () => {
      state.skyFrame = null;
      if (!state.visible || !state.skyRenderer || !state.graph || !state.skyCanvas) return;
      const host = document.getElementById('eve-map-canvas'), width = host?.clientWidth || 0, height = host?.clientHeight || 0;
      if (width && height) drawSkyMap(width, height, Math.min(window.devicePixelRatio || 1, SKY_GPU_MAX_DPR), state.graph.camera());
      state.skyFrame = requestAnimationFrame(frame);
    };
    state.skyFrame = requestAnimationFrame(frame);
  }
  function skyRayDirection(camera, ndcX, ndcY) {
    const world = camera.matrixWorld?.elements, projection = camera.projectionMatrix?.elements;
    if (!world || !projection) return null;
    const viewX = ndcX / Math.max(.001, projection[0]), viewY = ndcY / Math.max(.001, projection[5]), viewZ = -1;
    const length = Math.hypot(viewX, viewY, viewZ) || 1;
    const x = (world[0] * viewX + world[4] * viewY + world[8] * viewZ) / length;
    const y = (world[1] * viewX + world[5] * viewY + world[9] * viewZ) / length;
    const z = (world[2] * viewX + world[6] * viewY + world[10] * viewZ) / length;
    return skyTextureDirection({ x, y, z });
  }
  function skyFieldDimensions(width, height, dpr, moving = state.skyInteracting) {
    const aspect = Math.max(.25, width / Math.max(1, height));
    const floor = moving ? 82000 : 180000, cap = moving ? 145000 : 360000, coverage = moving ? .18 : .34;
    const targetPixels = Math.max(floor, Math.min(cap, Math.round(width * height * Math.min(1.25, Math.max(.75, dpr || 1)) * coverage)));
    const fieldWidth = Math.max(moving ? 300 : 460, Math.round(Math.sqrt(targetPixels * aspect)));
    return { width: fieldWidth, height: Math.max(moving ? 170 : 260, Math.round(fieldWidth / aspect)) };
  }
  function buildSkyRayGrid(width, height, projection) {
    const focalX = Math.max(.001, Math.abs(projection?.[0] || 1)), focalY = Math.max(.001, Math.abs(projection?.[5] || 1));
    const key = `${width}:${height}:${Math.round(focalX * 10000)}:${Math.round(focalY * 10000)}`;
    if (state.skyRays?.key === key) return state.skyRays;
    const count = width * height, x = new Float32Array(count), y = new Float32Array(count), z = new Float32Array(count);
    for (let row = 0, index = 0; row < height; row += 1) {
      const viewY = (1 - (row + .5) / height * 2) / focalY;
      for (let column = 0; column < width; column += 1, index += 1) {
        const viewX = ((column + .5) / width * 2 - 1) / focalX, inverseLength = 1 / Math.sqrt(viewX * viewX + viewY * viewY + 1);
        x[index] = viewX * inverseLength; y[index] = viewY * inverseLength; z[index] = -inverseLength;
      }
    }
    state.skyRays = { key, x, y, z };
    return state.skyRays;
  }
  function proceduralNebulaPacked(x, y, z) {
    // This is deliberately low-frequency: the panoramic texture carries the
    // fine detail, while this field only fills gaps and preserves a coherent
    // New Eden-aligned band when the texture cannot be decoded.
    const bend = .11 * Math.sin(x * 4.9 + z * 2.1);
    const plane = Math.abs(y - bend);
    const band = 1 / (1 + plane * plane * 95);
    const broad = .5 + .5 * Math.sin(x * 9.7 + y * 5.6 - z * 7.9);
    const density = band * (.14 + .86 * broad * broad);
    const warm = Math.max(0, Math.sin(x * 3.7 - z * 5.8 + .5)) * density;
    const r = Math.min(255, Math.round(3 + density * 24 + warm * 28)), g = Math.min(255, Math.round(10 + density * 52 + warm * 20)), b = Math.min(255, Math.round(18 + density * 70 + warm * 7));
    return (r << 16) | (g << 8) | b;
  }
  function nebulaColor(x, y, z) {
    const packed = proceduralNebulaPacked(x, y, z);
    return { r: packed >> 16 & 255, g: packed >> 8 & 255, b: packed & 255 };
  }
  function skyTextureCoordinates(x, y, z) {
    return {
      u: .5 + Math.atan2(z, x) / SKY_TAU,
      v: .5 - Math.asin(Math.max(-1, Math.min(1, y))) / Math.PI,
    };
  }
  function skyTexturePacked(texture, x, y, z) {
    if (!texture) return 0;
    // Keep the hot loop allocation-free; skyTextureCoordinates() is retained
    // separately for diagnostics and tests of the spherical mapping.
    const longitude = .5 + Math.atan2(z, x) / SKY_TAU;
    const latitude = .5 - Math.asin(Math.max(-1, Math.min(1, y))) / Math.PI;
    const column = Math.min(texture.width - 1, Math.max(0, Math.floor((longitude - Math.floor(longitude)) * texture.width)));
    const row = Math.min(texture.height - 1, Math.max(0, Math.floor(latitude * texture.height)));
    const index = (row * texture.width + column) * 4, pixels = texture.pixels;
    return (pixels[index] << 16) | (pixels[index + 1] << 8) | pixels[index + 2];
  }
  function writeSkyPixel(pixels, index, texture, x, y, z) {
    const procedural = proceduralNebulaPacked(x, y, z), textured = skyTexturePacked(texture, x, y, z);
    const proceduralR = procedural >> 16 & 255, proceduralG = procedural >> 8 & 255, proceduralB = procedural & 255;
    if (texture) {
      const textureR = textured >> 16 & 255, textureG = textured >> 8 & 255, textureB = textured & 255;
      pixels[index] = Math.min(255, Math.round(proceduralR * .38 + textureR * .82));
      pixels[index + 1] = Math.min(255, Math.round(proceduralG * .38 + textureG * .82));
      pixels[index + 2] = Math.min(255, Math.round(proceduralB * .38 + textureB * .82));
    } else {
      pixels[index] = proceduralR; pixels[index + 1] = proceduralG; pixels[index + 2] = proceduralB;
    }
    pixels[index + 3] = 255;
  }
  function drawProceduralNebula(context, camera, width, height, dpr) {
    const dimensions = skyFieldDimensions(width, height, dpr), world = camera.matrixWorld?.elements, projection = camera.projectionMatrix?.elements;
    if (!world || !projection) return;
    if (!state.skyField) state.skyField = document.createElement('canvas');
    const field = state.skyField;
    if (field.width !== dimensions.width || field.height !== dimensions.height) { field.width = dimensions.width; field.height = dimensions.height; state.skyImage = null; }
    const fieldContext = field.getContext('2d');
    if (!fieldContext) return;
    if (!state.skyImage) state.skyImage = fieldContext.createImageData(field.width, field.height);
    const pixels = state.skyImage.data, rays = buildSkyRayGrid(field.width, field.height, projection), texture = state.skyTexture;
    const cosine = Math.cos(-SKY_TEXTURE_YAW), sine = Math.sin(-SKY_TEXTURE_YAW);
    for (let point = 0, index = 0; point < rays.x.length; point += 1, index += 4) {
      const viewX = rays.x[point], viewY = rays.y[point], viewZ = rays.z[point];
      const worldX = world[0] * viewX + world[4] * viewY + world[8] * viewZ;
      const worldY = world[1] * viewX + world[5] * viewY + world[9] * viewZ;
      const worldZ = world[2] * viewX + world[6] * viewY + world[10] * viewZ;
      writeSkyPixel(pixels, index, texture, worldX * cosine + worldZ * sine, worldY, -worldX * sine + worldZ * cosine);
    }
    fieldContext.putImageData(state.skyImage, 0, 0);
    context.save(); context.imageSmoothingEnabled = true; context.imageSmoothingQuality = 'high'; context.drawImage(field, 0, 0, field.width, field.height, 0, 0, width, height); context.restore();
  }
  function skyCameraKey(camera, width, height, dpr) {
    const world = camera.matrixWorld?.elements, projection = camera.projectionMatrix?.elements;
    if (!world || !projection) return null;
    // Translation cannot move an infinitely distant celestial sphere. Quantising
    // rotation avoids repainting it while the graph itself animates beneath it.
    const rotation = [0, 1, 2, 4, 5, 6, 8, 9, 10].map(index => Math.round(world[index] * 700));
    return `${width}:${height}:${dpr}:${Math.round(projection[0] * 700)}:${Math.round(projection[5] * 700)}:${rotation.join(':')}`;
  }
  function skyForward(camera) {
    const world = camera.matrixWorld?.elements;
    if (!world) return null;
    const x = -world[8], y = -world[9], z = -world[10], length = Math.hypot(x, y, z) || 1;
    return { x: x / length, y: y / length, z: z / length };
  }
  function drawSkyGpu(renderer, camera, width, height, dpr, frameKey) {
    const world = camera.matrixWorld?.elements, projection = camera.projectionMatrix?.elements;
    if (!world || !projection) return;
    const { canvas, gl, program, position, locations, texture, cameraRotation, worldToSky } = renderer;
    const qualityScale = state.skyInteracting ? SKY_GPU_MOVING_SCALE : SKY_GPU_SETTLED_SCALE;
    const pixelWidth = Math.max(1, Math.round(width * dpr * qualityScale)), pixelHeight = Math.max(1, Math.round(height * dpr * qualityScale));
    if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) { canvas.width = pixelWidth; canvas.height = pixelHeight; canvas.style.width = `${width}px`; canvas.style.height = `${height}px`; }
    cameraRotation[0] = world[0]; cameraRotation[1] = world[1]; cameraRotation[2] = world[2]; cameraRotation[3] = world[4]; cameraRotation[4] = world[5]; cameraRotation[5] = world[6]; cameraRotation[6] = world[8]; cameraRotation[7] = world[9]; cameraRotation[8] = world[10];
    gl.viewport(0, 0, pixelWidth, pixelHeight); gl.useProgram(program); gl.bindBuffer(gl.ARRAY_BUFFER, renderer.buffer); gl.enableVertexAttribArray(position); gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0); gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, texture);
    const debugMode = window.__eveMapSkyDebug === 'gas' ? 1 : window.__eveMapSkyDebug === 'stars' ? 2 : 0;
    gl.uniformMatrix3fv(locations.cameraRotation, false, cameraRotation); gl.uniformMatrix2fv(locations.worldToSky, false, worldToSky); gl.uniform2f(locations.inverseFocal, 1 / Math.max(.001, projection[0]), 1 / Math.max(.001, projection[5])); gl.uniform1f(locations.debugMode, debugMode);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    state.skyFrameKey = frameKey; state.skyLastPaintAt = performance.now();
  }
  function drawSkyMap(width, height, dpr, camera) {
    const canvas = state.skyCanvas; if (!canvas) return;
    const frameKey = skyCameraKey(camera, width, height, dpr);
    if (frameKey && frameKey === state.skyFrameKey) return;
    if (state.skyRenderer) { drawSkyGpu(state.skyRenderer, camera, width, height, dpr, frameKey); return; }
    const now = performance.now();
    const minimumFrameTime = state.skyInteracting ? SKY_MOVING_FRAME_MS : SKY_SETTLED_FRAME_MS;
    if (state.skyFrameKey && now - state.skyLastPaintAt < minimumFrameTime) return;
    if (canvas.width !== width * dpr || canvas.height !== height * dpr) { canvas.width = width * dpr; canvas.height = height * dpr; canvas.style.width = `${width}px`; canvas.style.height = `${height}px`; }
    const context = canvas.getContext('2d'); context.setTransform(dpr, 0, 0, dpr, 0, 0); context.clearRect(0, 0, width, height);
    drawProceduralNebula(context, camera, width, height, dpr);
    const forward = skyForward(camera);
    state.skyStars.forEach(star => {
      // Cheap hemisphere culling: a perspective camera cannot see stars behind
      // its celestial shell, so they never reach the projection work.
      if (forward && star.x * forward.x + star.y * forward.y + star.z * forward.z <= 0) return;
      const point = projectSkyDirection(star, camera, width, height); if (!point) return;
      const color = star.tone === 'warm' ? '255, 205, 156' : star.tone === 'blue' ? '154, 191, 255' : '232, 238, 255';
      if (star.band) { context.beginPath(); context.arc(point.x, point.y, star.radius * 4.5, 0, Math.PI * 2); context.fillStyle = star.tone === 'warm' ? 'rgba(185, 96, 46, .035)' : 'rgba(65, 101, 158, .025)'; context.fill(); }
      context.beginPath(); context.arc(point.x, point.y, star.radius, 0, Math.PI * 2); context.fillStyle = `rgba(${color}, ${star.alpha})`; context.fill();
    });
    state.skyFrameKey = frameKey; state.skyLastPaintAt = now;
  }
  function projectSkyDirection(direction, camera, width, height) {
    const view = camera.matrixWorldInverse.elements, projection = camera.projectionMatrix.elements;
    const x = view[0] * direction.x + view[4] * direction.y + view[8] * direction.z;
    const y = view[1] * direction.x + view[5] * direction.y + view[9] * direction.z;
    const z = view[2] * direction.x + view[6] * direction.y + view[10] * direction.z;
    const clipX = projection[0] * x + projection[4] * y + projection[8] * z, clipY = projection[1] * x + projection[5] * y + projection[9] * z, clipW = projection[3] * x + projection[7] * y + projection[11] * z;
    if (clipW <= .001) return null;
    const nx = clipX / clipW, ny = clipY / clipW;
    return Math.abs(nx) <= 1.08 && Math.abs(ny) <= 1.08 ? { x: (nx + 1) * width / 2, y: (1 - ny) * height / 2 } : null;
  }
  function cameraViewPoint(point, camera) {
    const view = camera.matrixWorldInverse.elements;
    return {
      x: view[0] * point.x + view[4] * point.y + view[8] * point.z + view[12],
      y: view[1] * point.x + view[5] * point.y + view[9] * point.z + view[13],
      z: view[2] * point.x + view[6] * point.y + view[10] * point.z + view[14]
    };
  }
  function projectViewPoint(point, camera, width, height) {
    const projection = camera.projectionMatrix.elements;
    const clipX = projection[0] * point.x + projection[4] * point.y + projection[8] * point.z + projection[12];
    const clipY = projection[1] * point.x + projection[5] * point.y + projection[9] * point.z + projection[13];
    const clipW = projection[3] * point.x + projection[7] * point.y + projection[11] * point.z + projection[15];
    if (clipW <= .000001) return null;
    return { x: (clipX / clipW + 1) * width / 2, y: (1 - clipY / clipW) * height / 2 };
  }
  function projectGateSegment(source, target, camera, width, height) {
    let a = cameraViewPoint(source, camera), b = cameraViewPoint(target, camera);
    const nearPlane = -Math.max(.001, camera.near || .1);
    if (a.z > nearPlane && b.z > nearPlane) return null;
    if (a.z > nearPlane || b.z > nearPlane) {
      const ratio = (nearPlane - a.z) / (b.z - a.z);
      const intersection = { x: a.x + (b.x - a.x) * ratio, y: a.y + (b.y - a.y) * ratio, z: nearPlane };
      if (a.z > nearPlane) a = intersection;
      else b = intersection;
    }
    const projectedA = projectViewPoint(a, camera, width, height), projectedB = projectViewPoint(b, camera, width, height);
    return projectedA && projectedB ? clipSegmentToViewport(projectedA, projectedB, width, height) : null;
  }
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
    if (nz < -1 || nz > 1) return null;
    return { x: (nx + 1) * width / 2, y: (1 - ny) * height / 2 };
  }
  function clipSegmentToViewport(a, b, width, height) {
    const dx = b.x - a.x, dy = b.y - a.y, p = [-dx, dx, -dy, dy], q = [a.x, width - a.x, a.y, height - a.y];
    let start = 0, end = 1;
    for (let index = 0; index < 4; index += 1) {
      if (p[index] === 0) { if (q[index] < 0) return null; continue; }
      const ratio = q[index] / p[index];
      if (p[index] < 0) { if (ratio > end) return null; start = Math.max(start, ratio); }
      else { if (ratio < start) return null; end = Math.min(end, ratio); }
    }
    return { a: { x: a.x + dx * start, y: a.y + dy * start }, b: { x: a.x + dx * end, y: a.y + dy * end } };
  }
  function enableDistancePicker(host) {
    const label = document.createElement('div'); label.className = 'eve-map-hover'; label.setAttribute('aria-hidden', 'true'); host.appendChild(label);
    let lastPick = 0;
    const labelCandidateAt = (x, y) => state.labelHitTargets.find(candidate => Math.abs(candidate.x - x) <= candidate.width / 2 + 5 && Math.abs(candidate.y - y) <= candidate.height / 2 + 5);
    const selectCandidate = candidate => {
      if (!candidate) return;
      if (candidate.kind === 'system') onNodeClick(candidate.target);
      else focusArea(candidate.target, candidate.kind);
    };
    host.addEventListener('pointerdown', event => { state.pointer = { x: event.clientX, y: event.clientY, moved: false }; }, true);
    host.addEventListener('pointermove', event => {
      if (state.pointer && Math.hypot(event.clientX - state.pointer.x, event.clientY - state.pointer.y) > 7) state.pointer.moved = true;
      const now = performance.now(); if (now - lastPick < 60 || !state.graph) return; lastPick = now;
      const rect = host.getBoundingClientRect(), pointerX = event.clientX - rect.left, pointerY = event.clientY - rect.top, labelCandidate = labelCandidateAt(pointerX, pointerY), camera = state.graph.camera(); let nearest = null, best = Infinity, nearestDistance = Infinity;
      if (labelCandidate) {
        state.hoverCandidate = labelCandidate;
        if (labelCandidate.kind === 'system') label.innerHTML = combatHoverMarkup(labelCandidate.target); else label.textContent = `${labelCandidate.target.name} · ${labelCandidate.kind}`;
        label.style.display = 'block'; label.style.left = `${labelCandidate.x + 12}px`; label.style.top = `${labelCandidate.y + 12}px`; host.style.cursor = 'pointer'; return;
      }
      for (const node of state.nodesById.values()) {
        if (!visibleNode(node)) continue;
        const point = projectVisible(node, camera, rect.width, rect.height); if (!point) continue;
        nearestDistance = Math.min(nearestDistance, Math.hypot(camera.position.x - node.x, camera.position.y - node.y, camera.position.z - node.z));
        const distance = (point.x - pointerX) ** 2 + (point.y - pointerY) ** 2;
        if (distance < best) { best = distance; nearest = { node, point }; }
      }
      const radius = Math.min(42, Math.max(12, nearestDistance / 75));
      state.hoverCandidate = nearest && best <= radius ** 2 ? { kind: 'system', target: nearest.node } : null;
      if (state.hoverCandidate) { label.innerHTML = combatHoverMarkup(state.hoverCandidate.target); label.style.display = 'block'; label.style.left = `${nearest.point.x + 12}px`; label.style.top = `${nearest.point.y + 12}px`; host.style.cursor = 'pointer'; }
      else { label.style.display = 'none'; host.style.cursor = 'grab'; }
    });
    host.addEventListener('pointerup', () => { if (state.pointer?.moved) state.suppressNodeClickUntil = performance.now() + 180; }, true);
    host.addEventListener('click', event => { if (state.pointer?.moved) { event.stopImmediatePropagation(); state.pointer = null; return; } if (!state.hoverCandidate) return; event.stopImmediatePropagation(); selectCandidate(state.hoverCandidate); state.pointer = null; }, true);
    host.addEventListener('contextmenu', event => { if (!state.hoverCandidate || state.hoverCandidate.kind !== 'system' || state.pointer?.moved) return; event.preventDefault(); event.stopImmediatePropagation(); setEndpoint('origin', state.hoverCandidate.target); focus(state.hoverCandidate.target); }, true);
  }
  function combatMarkerGroups(markers, now = Date.now()) {
    const groups = new Map();
    for (const marker of markers) {
      if (!marker?.system_id || now >= marker.expiresAt) continue;
      const group = groups.get(marker.system_id) || { system_id: marker.system_id, count: 0, value: 0, latestAt: 0, expiresAt: 0, markers: [] };
      group.count += 1;
      group.value += Number(marker.value) || 0;
      group.latestAt = Math.max(group.latestAt, marker.happenedAt || 0);
      group.expiresAt = Math.max(group.expiresAt, marker.expiresAt || 0);
      group.markers.push(marker);
      groups.set(marker.system_id, group);
    }
    return [...groups.values()].map(group => ({ ...group, markers: group.markers.sort((a, b) => b.happenedAt - a.happenedAt) }));
  }
  function combatActivity(count) {
    if (count >= 10) return { symbol: '💀', level: 'critical', color: '#ff4867' };
    if (count >= 5) return { symbol: '🔥', level: 'hot', color: '#ff7652' };
    if (count >= 2) return { symbol: '⚠', level: 'warning', color: '#ffb34e' };
    if (count === 1) return { symbol: '◉', level: 'watch', color: '#f4d07f' };
    return { symbol: '●', level: 'quiet', color: '#88a7b2' };
  }
  const combatGroupFor = (systemId, now = Date.now()) => combatMarkerGroups(state.combatMarkers.values(), now).find(group => Number(group.system_id) === Number(systemId)) || { system_id: systemId, count: 0, value: 0, markers: [] };
  const combatAge = (happenedAt, now = Date.now()) => `${Math.max(0, Math.floor((now - happenedAt) / 60))}m`;
  const combatIsk = value => value >= 1e9 ? `${(value / 1e9).toFixed(2)} B` : value >= 1e6 ? `${Math.round(value / 1e6)} M` : `${Math.round(value / 1e3)} k`;
  function combatHoverMarkup(system, now = Date.now()) {
    const group = combatGroupFor(system.id, now), activity = combatActivity(group.count);
    const kills = group.markers.slice(0, 5).map(marker => `<span>${combatAge(marker.happenedAt, now)}&nbsp; ${escapeHtml(marker.victim_ship_name || (marker.victim_ship_type_id ? `Ship #${marker.victim_ship_type_id}` : 'Vaisseau inconnu'))}<small>${Number(marker.attacker_count) || 0} attaquant(s)</small></span>`).join('');
    return `<b>${escapeHtml(system.name)} <small>${system.security.toFixed(2)}</small></b><hr><strong style="color:${activity.color}">${activity.symbol} ${group.count} kill${group.count === 1 ? '' : 's'} / 30 min</strong>${group.count ? `<em>💰 ${combatIsk(group.value)} ISK détruits</em><div class="eve-map-hover-kills">${kills}</div>` : ''}`;
  }
  function drawCombatMarkers(context, camera, width, height, now = Date.now()) {
    for (const [key, marker] of state.combatMarkers) {
      if (!marker?.system_id || now >= marker.expiresAt) state.combatMarkers.delete(key);
    }
    for (const group of combatMarkerGroups(state.combatMarkers.values(), now)) {
      const node = state.nodesById.get(Number(group.system_id));
      if (!node || !visibleNode(node)) continue;
      const point = projectVisible(node, camera, width, height);
      if (!point) continue;
      const life = Math.max(0, Math.min(1, (group.expiresAt - now) / COMBAT_MARKER_TTL_MS));
      const activity = combatActivity(group.count);
      const pulse = .5 + .5 * Math.sin(now / 560 + Number(group.system_id) % 31);
      const underlineWidth = (group.count >= 2 ? 24 : 14) + Math.min(group.count, 4) * 3;
      const underlineY = point.y + 10;
      context.beginPath(); context.moveTo(point.x - underlineWidth / 2, underlineY); context.lineTo(point.x + underlineWidth / 2, underlineY);
      context.strokeStyle = activity.color; context.globalAlpha = (.38 + pulse * .18) * (.45 + life * .55);
      context.lineWidth = group.count >= 2 ? 2.2 : 1.45; context.stroke();
      if (group.count >= 1) {
        context.font = '700 12px system-ui, sans-serif'; context.textAlign = 'center'; context.textBaseline = 'bottom';
        context.fillStyle = activity.color; context.globalAlpha = .72 + pulse * .24; context.fillText(`${activity.symbol} ${group.count}`, point.x, underlineY - 4);
      }
    }
    context.globalAlpha = 1;
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
    const camera = state.graph.camera();
    // Camera navigation does not wake ForceGraph's stopped D3 simulation, so
    // nodePositionUpdate cannot own zoom scaling. Update the actual node meshes
    // from this live visual loop instead.
    updateSystemScreenScales([...state.nodesById.values()], camera, height);
    if (!state.skyRenderer) drawSkyMap(width, height, dpr, camera);
    const nearestSystemDistance = Math.min(...[...state.nodesById.values()].map(node => Math.hypot(camera.position.x - node.x, camera.position.y - node.y, camera.position.z - node.z)));
    if (state.filters.gates) {
      // Keep a faint topology trace at galaxy scale instead of fading local gates to zero.
      const localFade = .25 + .75 * Math.max(0, Math.min(1, (1100 - nearestSystemDistance) / 650));
      const regionalLineWidth = .32 + .43 * localFade;
      const regionalBoundaryLineWidth = .42 + .48 * localFade;
      const localSegments = [], regionalSegments = [], boundarySegments = [], routeSegments = [], focusSegments = [], trafficSegments = [];
      const trafficBySystem = state.filters.traffic ? new Map([...state.nodesById.values()].map(node => [node.id, liveFor(node).ship_jumps])) : null;
      state.data.gates.forEach(gate => {
        const source = state.nodesById.get(gate.source), target = state.nodesById.get(gate.target);
        const routeLeg = state.route?.legs.get(gateKey(gate.source, gate.target));
        if (!source || !target || (!routeLeg && (!visibleNode(source) || !visibleNode(target)))) return;
        const physicalDistance = Math.hypot(source.position_m.x - target.position_m.x, source.position_m.y - target.position_m.y, source.position_m.z - target.position_m.z);
        if (physicalDistance > 8e16 && !routeLeg) return;
        const clipped = projectGateSegment(source, target, camera, width, height);
        if (!clipped) return;
        let a = clipped.a, b = clipped.b;
        if (state.filters.traffic) {
          const flows = estimateGateFlows(source, target, state.gateDegrees, trafficBySystem);
          const directionalFlows = [
            { direction: 1, plan: trafficParticlePlan(flows.sourceToTarget) },
            { direction: -1, plan: trafficParticlePlan(flows.targetToSource) },
          ].filter(flow => flow.plan.count);
          if (directionalFlows.length && Math.hypot(b.x - a.x, b.y - a.y) >= 18) trafficSegments.push({ a, b, flows: directionalFlows, worldLength: trafficSegmentWorldLength({ source, target }), phase: (((Number(gate.source) * 31) ^ Number(gate.target)) >>> 0) % 997 / 997 });
        }
        if (routeLeg) {
          if (routeLeg.source === target.id) [a, b] = [b, a];
          routeSegments.push({ a, b, index: routeLeg.index });
          return;
        }
        if (state.areaFocus && isAreaMember(source) && isAreaMember(target)) focusSegments.push(a.x, a.y, b.x, b.y);
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
      if (localFade > .01) stroke(localSegments, `rgba(105, 232, 241, ${Math.max(.25, .34 * localFade)})`, .7);
      stroke(regionalSegments, 'rgba(115, 239, 249, .78)', regionalLineWidth);
      if (focusSegments.length) stroke(focusSegments, 'rgba(201, 250, 255, .92)', 1.05 + .5 * localFade);
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
      routeSegments.forEach(({ a, b, index }) => {
        context.beginPath(); context.moveTo(a.x, a.y); context.lineTo(b.x, b.y);
        context.strokeStyle = '#ffe279'; context.globalAlpha = .96; context.lineWidth = .45 + .9 * localFade; context.stroke();
        const progress = (now / 850 + index * .18) % 1;
        const particleX = a.x + (b.x - a.x) * progress, particleY = a.y + (b.y - a.y) * progress;
        context.beginPath(); context.arc(particleX, particleY, .9 + 1.8 * localFade, 0, Math.PI * 2);
        context.fillStyle = '#fff4bd'; context.fill();
      });
      trafficSegments.forEach(({ a, b, flows, phase, worldLength }) => {
        const dx = b.x - a.x, dy = b.y - a.y, length = Math.hypot(dx, dy) || 1;
        flows.forEach(({ direction, plan }, flowIndex) => {
          const progress = trafficSegmentProgress(now, phase + flowIndex * .37, plan.jumps, { worldLength });
          // Each direction owns a separate side of the line. Its tiered
          // particles form a small sequential packet, rather than a row.
          const laneOffset = direction * 2;
          trafficPacketOffsets(plan.count).forEach((packetOffset, index) => {
            const packetProgress = (progress - packetOffset + 1) % 1;
            const packetTravel = direction === 1 ? packetProgress : 1 - packetProgress;
            const particle = plan.particles[index];
            const visual = trafficParticleVisualStyle(particle);
            const x = a.x + dx * packetTravel - dy / length * laneOffset, y = a.y + dy * packetTravel + dx / length * laneOffset;
            context.beginPath(); context.arc(x, y, visual.glowRadius, 0, Math.PI * 2); context.fillStyle = particle.color; context.globalAlpha = visual.glowAlpha; context.fill();
            context.beginPath(); context.arc(x, y, visual.coreRadius, 0, Math.PI * 2); context.fillStyle = particle.color; context.globalAlpha = visual.coreAlpha; context.fill();
          });
        });
      });
      context.globalAlpha = 1;
      if (state.filters.traffic || state.filters.danger) {
        for (const node of state.nodesById.values()) {
          if (!visibleNode(node)) continue;
          const point = projectVisible(node, camera, width, height); if (!point) continue;
          const live = liveFor(node);
          if (state.filters.traffic && live.ship_jumps > 0) {
            const intensity = Math.max(0, Math.min(1, Math.log1p(live.ship_jumps) / Math.log(50000)));
            const radius = 2.5 + intensity * 8.7;
            context.beginPath(); context.arc(point.x, point.y, radius, 0, Math.PI * 2); context.fillStyle = `hsla(${190 - intensity * 24}, 92%, 66%, ${.18 + intensity * .20})`; context.fill();
          }
          if (state.filters.danger && live.danger >= 20) {
            const colors = { yellow: '#ffd35f', orange: '#ff913d', red: '#f0546e' };
            context.beginPath(); context.arc(point.x, point.y, 4 + live.danger / 15, 0, Math.PI * 2); context.strokeStyle = colors[live.danger_band]; context.globalAlpha = .3 + live.danger / 150; context.lineWidth = 1.15; context.stroke();
          }
        }
      }
      if (state.filters.sovereignty || state.filters.empires) {
        context.save();
        for (const node of state.nodesById.values()) {
          if (!visibleNode(node)) continue;
          const point = projectVisible(node, camera, width, height); if (!point) continue;
          const layers = influenceLayers(node);
          const distance = Math.hypot(camera.position.x - node.x, camera.position.y - node.y, camera.position.z - node.z);
          const far = Math.max(0, Math.min(1, (distance - 300) / 2200));
          layers.forEach(layer => {
            const style = influenceOverlayStyle(distance, layer.kind), radius = style.radius + layer.offset * 2.2;
            context.beginPath(); context.arc(point.x, point.y, radius, 0, Math.PI * 2);
            // At distance the translucent fills overlap into territories.  Do
            // not outline every system: the rings were visually competing with
            // the actual node layer and made dense regions look like clusters.
            context.fillStyle = influenceColor(layer.id); context.globalAlpha = layer.alpha * style.opacity; context.fill();
            if (far < .25) { context.strokeStyle = influenceColor(layer.id); context.globalAlpha = layer.alpha * .45; context.lineWidth = .55; context.stroke(); }
          });
        }
        context.restore();
      }
      characterGroups().forEach(characters => {
        const node = state.nodesById.get(characters[0].system_id), point = node && projectVisible(node, camera, width, height);
        if (!point || point.x < 0 || point.x > width || point.y < 0 || point.y > height) return;
        const distance = Math.hypot(camera.position.x - node.x, camera.position.y - node.y, camera.position.z - node.z);
        const markerScale = characterMarkerScale(distance), ringRadius = 5 + markerScale * 1.8;
        characters.forEach((character, index) => {
          const info = characterColorInfo(character), radius = ringRadius + index * 1.7;
          context.beginPath(); context.arc(point.x, point.y, radius + 2.1, 0, Math.PI * 2); context.strokeStyle = info.glow; context.globalAlpha = .16; context.lineWidth = 3.2; context.stroke();
          context.beginPath(); context.arc(point.x, point.y, radius, 0, Math.PI * 2); context.strokeStyle = info.color; context.globalAlpha = .88; context.lineWidth = 1.05; context.stroke();
        });
        const outerRadius = ringRadius + Math.max(0, characters.length - 1) * 1.7;
        context.font = `600 ${Math.min(14, 8.5 + markerScale * 1.45)}px system-ui, sans-serif`; context.textAlign = 'left';
        const lineHeight = 9 + markerScale * 2.25, dotRadius = 1.8 + markerScale * .55, textOffset = outerRadius + 5;
        const firstLineY = point.y - outerRadius - 3 - (characters.length - 1) * lineHeight / 2;
        characters.forEach((character, index) => {
          const lineY = firstLineY + index * lineHeight;
          context.beginPath(); context.arc(point.x + textOffset, lineY - dotRadius / 2, dotRadius, 0, Math.PI * 2); context.fillStyle = characterColor(character); context.fill();
          context.fillStyle = '#e3fbff'; context.fillText(character.name, point.x + textOffset + dotRadius * 2 + 4, lineY);
        });
      });
      context.globalAlpha = 1;
    }
    drawCombatMarkers(context, camera, width, height);
    drawMapLabels(context, camera, width, height, nearestSystemDistance);
    state.linkFrame = requestAnimationFrame(drawGateOverlay);
  }
  function startGateOverlay(host, nodes) {
    stopGateOverlay();
    const canvas = document.createElement('canvas'); canvas.className = 'eve-map-links'; canvas.setAttribute('aria-hidden', 'true'); Object.assign(canvas.style, { position: 'absolute', inset: '0', zIndex: '2', pointerEvents: 'none' }); host.appendChild(canvas);
    state.linkCanvas = canvas; state.renderNodes = nodes; state.nodesById = new Map(nodes.map(node => [node.id, node])); state.labelGroups = { regions: buildLabelGroups(nodes, 'region_id', 'region'), constellations: buildLabelGroups(nodes, 'constellation_id', 'constellation') }; state.linkFrame = requestAnimationFrame(drawGateOverlay);
  }
  function focus(system) {
    if (!system || !state.graph) return;
    // Search results come from the raw dataset; resolve them to the pinned render
    // node before using scene coordinates for the camera handoff.
    const renderedSystem = state.nodesById?.get(system.id) || system;
    if (!['x', 'y', 'z'].every(axis => Number.isFinite(renderedSystem[axis]))) return;
    state.areaFocus = null; render(); showSystem(renderedSystem);
    state.graph.cameraPosition(cameraPose(renderedSystem, 42), renderedSystem, 700);
  }
  // One deliberate, overhead New Eden frame. Home, search, and right-click
  // focus all use this vector so navigation translates over the map instead
  // of unexpectedly changing its orbital plane.
  const NEW_EDEN_CAMERA_DIRECTION = { x: .2, y: 1.9, z: .35 };
  const newEdenCameraPose = (target, distance) => {
    const direction = NEW_EDEN_CAMERA_DIRECTION;
    const magnitude = Math.hypot(direction.x, direction.y, direction.z) || 1;
    return { x: target.x + direction.x / magnitude * distance, y: target.y + direction.y / magnitude * distance, z: target.z + direction.z / magnitude * distance };
  };
  function cameraPose(target, distance) { return newEdenCameraPose(target, distance); }
  function galaxyShot(nodes, anchor = null) {
    const bounds = nodes.reduce((result, node) => {
      ['x', 'y', 'z'].forEach(axis => { result.min[axis] = Math.min(result.min[axis], node[axis]); result.max[axis] = Math.max(result.max[axis], node[axis]); });
      return result;
    }, { min: { x: Infinity, y: Infinity, z: Infinity }, max: { x: -Infinity, y: -Infinity, z: -Infinity } });
    const centre = Object.fromEntries(['x', 'y', 'z'].map(axis => [axis, (bounds.min[axis] + bounds.max[axis]) / 2]));
    const jita = nodes.find(node => Number(node.id) === 30000142 || node.name === 'Jita');
    const target = anchor || jita || centre;
    const span = Math.max(...['x', 'y', 'z'].map(axis => bounds.max[axis] - bounds.min[axis]), 1);
    return { target, position: newEdenCameraPose(target, span * 1.94) };
  }
  function focusHome(duration = 650) { if (!state.graph || !state.nodesById) return; state.areaFocus = null; render(); const shot = galaxyShot([...state.nodesById.values()]); state.graph.cameraPosition(shot.position, shot.target, duration); }
  function focusArea(area, kind) {
    if (!area || !state.graph) return;
    state.areaFocus = { area, kind }; render();
    const distance = Math.max(kind === 'region' ? 560 : 150, area.span * (kind === 'region' ? 1.55 : 1.4));
    state.graph.cameraPosition(cameraPose(area, distance), area, 850);
    const panel = document.getElementById('eve-map-panel'); panel.querySelector('.eve-map-empty')?.remove(); panel.querySelector('.eve-map-system')?.remove(); panel.querySelector('.eve-map-area')?.remove();
    panel.insertAdjacentHTML('afterbegin', `<div class="eve-map-area"><h3>${escapeHtml(area.name)}</h3><p>${kind === 'region' ? 'Région' : 'Constellation'} · ${area.count} systèmes · focus actif</p></div>`);
  }
  function systemFor(id) { return state.data?.systems.find(system => String(system.id) === String(id)); }
  function setEndpoint(kind, system) {
    const rawSystem = systemFor(system?.id || system);
    if (!rawSystem) return;
    if (kind === 'origin') { state.originId = rawSystem.id; document.getElementById('eve-route-from').value = rawSystem.name; }
    else { state.destinationId = rawSystem.id; document.getElementById('eve-route-to').value = rawSystem.name; }
    if (state.originId && state.destinationId) updateRoute();
  }
  async function updateRoute(safeOnly = false) {
    const result = document.getElementById('eve-route-result');
    const from = systemFor(state.originId), to = systemFor(state.destinationId);
    if (!from || !to) return;
    result.textContent = 'Calcul de route…'; state.route = null;
    const response = await api().find_eve_route(from.id, to.id, safeOnly ? .5 : null), route = response.data;
    if (!route || route.error) { result.textContent = safeOnly ? 'Aucune route entièrement high-sec.' : 'Aucun itinéraire stargate.'; return; }
    const legs = new Map(route.systems.slice(1).map((target, index) => {
      const source = route.systems[index]; return [gateKey(source, target), { source, target, index }];
    }));
    state.route = { systems: route.systems, legs };
    const risky = route.systems.some(id => Number(systemFor(id)?.security) < .6);
    const steps = route.systems.map((id, index) => {
      const system = systemFor(id);
      if (!system) return '';
      const endpoint = index === 0 ? ' eve-route-square--origin' : index === route.systems.length - 1 ? ' eve-route-square--destination' : '';
      const title = `${system.name} · ${Number(system.security || 0).toFixed(2)}`;
      return `<button class="eve-route-square eve-route-square--${security(system.security)} ${dangerClass(system)}${endpoint}" title="${escapeHtml(title)} · Danger ${liveFor(system).danger}/100" aria-label="Centrer sur ${escapeHtml(title)}" onclick="focusEveMapSystem(${Number(system.id)})"></button>`;
    }).filter(Boolean).join('<span class="eve-route-square-join" aria-hidden="true"></span>');
    const routeIntel = route.systems.map(systemFor).filter(Boolean).map(liveFor), totalJumps = routeIntel.reduce((sum, live) => sum + live.ship_jumps, 0), shipKills = routeIntel.reduce((sum, live) => sum + live.ship_kills, 0), podKills = routeIntel.reduce((sum, live) => sum + live.pod_kills, 0), riskyIntel = routeIntel.filter(live => live.danger >= 50).length, dangerous = routeIntel.filter(live => live.danger >= 75).length;
    const traffic = totalJumps >= 5000 ? 'HIGH' : totalJumps >= 500 ? 'MEDIUM' : 'LOW';
    const intelSummary = state.live.state === 'unavailable' ? '<div class="eve-live-state">Live Intel indisponible</div>' : `<div class="eve-route-intel"><b>Traffic: ${traffic}</b><span>Danger: ${riskyIntel} risky · ${dangerous} dangerous</span><span>Ship kills: ${shipKills} · Pod kills: ${podKills}</span></div>`;
    result.innerHTML = `<div class="eve-route-summary">${route.jumps} saut${route.jumps === 1 ? '' : 's'} · ${escapeHtml(from.name)} → ${escapeHtml(to.name)}</div>${intelSummary}${risky && !safeOnly ? '<div class="eve-route-warning">⚠ Passage sous 0,6 <button class="btn mini" onclick="useEveMapSafeRoute()">Route sûre high-sec</button></div>' : safeOnly ? '<div class="eve-route-safe">✓ Route high-sec (≥ 0,5)</div>' : ''}<div class="eve-route-steps" aria-label="Systèmes traversés">${steps}</div>`;
  }
  function onNodeClick(system) {
    if (performance.now() < state.suppressNodeClickUntil) return;
    const now = performance.now(), previous = state.lastNodeClick;
    if (previous?.id === system?.id && now - previous.at < 360) { setEndpoint('destination', system); state.lastNodeClick = null; return; }
    state.lastNodeClick = { id: system?.id, at: now }; focus(system);
  }
  function initialize(data) {
    state.data = data; state.gateDegrees = buildGateDegrees(data.gates); const host = document.getElementById('eve-map-canvas'); host.textContent = '';
    // ForceGraph3D is only our WebGL renderer.  Pin all CCP coordinates so its
    // default D3 physics can never turn New Eden into a force-directed cluster.
    const nodes = displayNodes(data.systems, data.gates);
    let graph;
    graph = ForceGraph3D()(host).backgroundColor('rgba(7, 17, 28, 0)').graphData({ nodes, links: data.gates }).nodeId('id').nodeLabel(node => `${node.name} · ${node.security.toFixed(2)}`).nodeColor(focusNodeColor).nodeVal(nodeSize).nodeRelSize(.14).nodeResolution(12).nodeOpacity(.96).linkColor(() => '#70e6ef').linkOpacity(.9).linkWidth(0).enableNodeDrag(false).nodePositionUpdate((object, coords) => {
      // The default spheres use world units and disappear when the camera pulls
      // back. Scale them by camera distance while keeping the CCP position fixed.
      const camera = graph.camera();
      const distance = Math.hypot(camera.position.x - coords.x, camera.position.y - coords.y, camera.position.z - coords.z);
      object.position.set(coords.x, coords.y, coords.z);
      object.scale.setScalar(cappedSystemObjectScale(object, distance, camera, host.clientHeight));
      return true;
    }).onNodeClick(onNodeClick).onNodeRightClick(node => { setEndpoint('origin', node); focus(node); }).onNodeHover(node => host.style.cursor = node ? 'pointer' : 'grab');
    const resizeMap = () => { if (!host.clientWidth || !host.clientHeight) return; graph.width(host.clientWidth).height(host.clientHeight); state.lastLinkDraw = 0; };
    state.resizeMap = resizeMap; resizeMap();
    if (window.ResizeObserver) { state.resizeObserver?.disconnect(); state.resizeObserver = new window.ResizeObserver(resizeMap); state.resizeObserver.observe(host); }
    else window.addEventListener?.('resize', resizeMap);
    enableDistancePicker(host);
    graph.d3Force('charge', null); graph.d3Force('link', null); graph.d3Force('center', null); graph.cooldownTicks(0);
    const controls = typeof graph.controls === 'function' ? graph.controls() : null;
    stabilizeOrbitControls(controls); bindSkyQualityControls(controls);
    state.graph = graph; render(); startSkyMap(host); startGateOverlay(host, nodes); setTimeout(() => focusHome(), 80);
  }
  async function load() { if (state.graph) return; if (!window.ForceGraph3D) throw new Error('La dépendance 3D est indisponible.'); const response = await api().get_eve_map_data(); if (!response.ok) throw new Error(response.error || 'Carte indisponible'); initialize(response.data); }
  window.openEveMap = async function () { const workspace = mapWorkspace(); if (!workspace) return; workspace.setAttribute('aria-hidden', 'false'); state.visible = true; try { await load(); state.resizeMap && state.resizeMap(); if (!state.linkFrame && state.linkCanvas) state.linkFrame = requestAnimationFrame(drawGateOverlay); startSkyFrame(); state.graph.resumeAnimation(); loadLiveIntel(); startCombatStream(); await loadCharacterPositions(); startCharacterPositionTracking(); } catch (error) { document.getElementById('eve-map-canvas').innerHTML = `<div class="eve-map-status">${escapeHtml(error.message)}</div>`; } };
  window.closeEveMap = function () { const workspace = mapWorkspace(); workspace?.setAttribute('aria-hidden', 'true'); state.visible = false; stopCharacterPositionTracking(); stopCombatStream(); stopGateOverlay(); stopSkyFrame(); state.graph && state.graph.pauseAnimation(); };
  window.setEveMapOrigin = id => setEndpoint('origin', id);
  window.setEveMapDestination = id => setEndpoint('destination', id);
  window.useEveMapSafeRoute = () => updateRoute(true);
  window.focusEveMapSystem = id => focus(systemFor(id));
  window.focusEveMapCharacter = id => focusCharacterPosition(id);
  window.clearEveMapCharacterSelection = clearCharacterSelection;
  window.refreshEveMapCharacterPositions = () => loadCharacterPositions();
  if (window.__eveMapTest) {
    Object.assign(window.__eveMapTest, {
      clipSegmentToViewport, projectGateSegment, characterColor, characterMarkerScale, systemObjectScale, combatMarkerGroups, combatActivity,
      estimateGateTraffic, estimateGateFlows, trafficParticlePlan, trafficPacketOffsets, trafficParticleSpeed, trafficParticleProgress, trafficSegmentProgress, trafficParticleVisualStyle,
      shipIconUrl, formatKillDate, killLocation, attackerPopoverMarkup,
      influenceOverlayRadius, influenceOverlayStyle, influenceLayers, cappedSystemObjectScale, updateSystemScreenScales,
      displayNodes, cameraPose, galaxyShot, visibleLabelGroup, labelsCanOverlap, mapLabelLayers, mapLabelPlacement, isAreaMember, focusNodeColor, characterPositionTrackingInterval, characterPositionSystemId, visibleCharacterIds,
      buildSkyStars, skyCameraKey, skyTextureDirection, worldSkyDirection, skyRayDirection, skyFieldDimensions,
      nebulaColor, skyTextureCoordinates, skyTexturePacked, skyGpuFragmentShader: SKY_GPU_FRAGMENT_SHADER, stabilizeOrbitControls, projectSkyDirection
    });
  }
  function bindControls() {
    document.querySelectorAll('[data-eve-security], #eve-map-gates, #eve-map-traffic, #eve-map-danger, #eve-map-sovereignty, #eve-map-empires').forEach(input => input.addEventListener('change', () => { const key = input.dataset.eveSecurity || input.id.replace('eve-map-', ''); state.filters[key] = input.checked; if (key === 'sovereignty') { const indicator = document.getElementById('eve-map-sovereignty-state'); if (input.checked) loadSovereignty(); else if (indicator) indicator.textContent = 'off'; } render(); }));
    document.getElementById('eve-map-character').addEventListener('change', event => { state.selectedCharacterId = event.target.value || null; const character = state.characters.find(row => String(row.character_id) === String(state.selectedCharacterId)); startCharacterPositionTracking(); if (character) focus(systemFor(character.system_id)); });
    document.getElementById('eve-map-fit').addEventListener('click', () => focusHome());
    document.getElementById('eve-map-reset-camera')?.addEventListener('click', () => focusHome(420));
    document.getElementById('eve-map-search').addEventListener('input', event => { const query = event.target.value.trim().toLowerCase(); const box = document.getElementById('eve-map-results'); box.innerHTML = ''; if (!query || !state.data) return; state.data.systems.filter(s => s.name.toLowerCase().includes(query)).slice(0, 8).forEach(s => { const button = document.createElement('button'); button.textContent = `${s.name} · ${s.region}`; button.onclick = () => { box.innerHTML = ''; focus(s); }; box.appendChild(button); }); });
    document.getElementById('eve-map-route').addEventListener('click', () => { const systems = state.data && state.data.systems || []; const resolve = value => systems.find(s => String(s.id) === value.trim() || s.name.toLowerCase() === value.trim().toLowerCase()); const from = resolve(document.getElementById('eve-route-from').value), to = resolve(document.getElementById('eve-route-to').value), result = document.getElementById('eve-route-result'); if (!from || !to) { result.textContent = 'Systèmes introuvables.'; return; } state.originId = from.id; state.destinationId = to.id; updateRoute(); });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bindControls);
  else bindControls();
}());
