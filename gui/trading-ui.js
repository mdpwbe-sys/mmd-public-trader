(function () {
  'use strict';
  const VALID_VIEWS = new Set(['dashboard', 'assets', 'transactions', 'alerts']);
  const T = { workspace: null, settings: null, containerKey: '', request: 0, settingsRequest: 0, initialized: false };
  const el = id => document.getElementById(id);
  const esc = value => String(value == null ? '' : value).replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#039;');
  const api = () => (window.pywebview && window.pywebview.api) || window.api || null;
  const asId = value => value == null ? null : String(value);

  function integer(value) {
    const raw = String(value == null ? '' : value).trim();
    return /^-?\d+$/.test(raw) ? BigInt(raw) : null;
  }
  function grouped(value) { return String(value).replace(/\B(?=(\d{3})+(?!\d))/g, '.'); }
  function fmtCents(value) {
    const amount = integer(value); if (amount == null) return '–';
    const neg = amount < 0n, abs = neg ? -amount : amount;
    return `${neg ? '-' : ''}${grouped(abs / 100n)},${String(abs % 100n).padStart(2, '0')} ISK`;
  }
  function fmtQty(value) { const n = integer(value); return n == null ? '–' : `${n < 0n ? '-' : ''}${grouped(n < 0n ? -n : n)}`; }
  function fmtBps(value) {
    const n = integer(value); if (n == null) return '–';
    const neg = n < 0n, abs = neg ? -n : n;
    return `${neg ? '-' : '+'}${abs / 100n},${String(abs % 100n).padStart(2, '0')} %`;
  }
  function tone(value) { const n = integer(value); return n == null || n === 0n ? 'trade-muted' : (n > 0n ? 'trade-positive' : 'trade-negative'); }
  function pick(row, keys, fallback) {
    for (const key of keys) if (row && row[key] != null && row[key] !== '') return row[key];
    return fallback == null ? '' : fallback;
  }
  function textCell(value, cls) { return `<td class="${cls || ''}" title="${esc(value)}">${esc(value || '–')}</td>`; }
  function moneyCell(value) { return `<td class="num ${tone(value)}">${fmtCents(value)}</td>`; }
  function table(rows, columns, emptyText) {
    if (!Array.isArray(rows) || !rows.length) return `<div class="trade-empty">${esc(emptyText)}</div>`;
    const head = columns.map(c => `<th class="${c.num ? 'num' : ''}">${esc(c.label)}</th>`).join('');
    const body = rows.map(row => `<tr>${columns.map(c => c.render(row)).join('')}</tr>`).join('');
    return `<div class="trade-table-wrap"><table class="trade-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
  }
  function sourceBadge() { return `<span class="trade-source">${esc((T.workspace && T.workspace.source_label) || 'Source non configurée')}</span>`; }
  function syncNotice() {
    const data = T.workspace || {}, errors = Array.isArray(data.sync_errors) ? data.sync_errors : [];
    if (!data.stale && !data.cached && !errors.length) return '';
    const details = errors.map(value => value && typeof value === 'object' ? (value.message || value.error || 'Erreur ESI') : String(value)).join(' · ');
    const message = details ? `Données partielles — synchronisation incomplète : ${details}` : (data.cached ? 'Données locales en cache — actualisez pour interroger ESI.' : 'Données potentiellement périmées.');
    return `<div class="trade-sync-warning">${esc(message)}</div>`;
  }
  function shell(title, body, controls) {
    return `<div class="trade-shell"><div class="trade-toolbar"><h2>${esc(title)}</h2>${controls || ''}${sourceBadge()}` +
      `<button class="btn small" onclick="TradingUI.refresh()">Actualiser trading</button></div>${syncNotice()}${body}</div>`;
  }
  function kpi(label, value, primary) {
    return `<div class="trade-kpi${primary ? ' primary' : ''}"><div class="k">${esc(label)}</div><div class="v ${tone(value)}">${fmtCents(value)}</div></div>`;
  }
  function percentKpi(label, value) { return `<div class="trade-kpi"><div class="k">${esc(label)}</div><div class="v ${tone(value)}">${fmtBps(value)}</div></div>`; }
  function assetName(row) { return pick(row, ['name', 'item_name', 'type_name'], 'Objet'); }

  function dashboard(data) {
    const s = data.summary || {}, assets = Array.isArray(data.assets) ? data.assets : [];
    const ranked = assets.filter(a => integer(pick(a, ['unrealized_pnl_cents', 'latent_pnl_cents'], null)) != null)
      .sort((a, b) => { const av = integer(pick(a, ['unrealized_pnl_cents', 'latent_pnl_cents'], 0)); const bv = integer(pick(b, ['unrealized_pnl_cents', 'latent_pnl_cents'], 0)); return av === bv ? 0 : (av < bv ? 1 : -1); });
    const winner = ranked[0], loser = ranked[ranked.length - 1];
    const insight = (label, row) => `<div class="trade-insight"><div class="title">${label}</div><div class="name">${esc(row ? assetName(row) : '–')}</div>` +
      `<div class="amount ${row ? tone(pick(row, ['unrealized_pnl_cents', 'latent_pnl_cents'], 0)) : 'trade-muted'}">${row ? fmtCents(pick(row, ['unrealized_pnl_cents', 'latent_pnl_cents'], null)) : '–'}</div></div>`;
    const cards = kpi('Fund Value', s.fund_value_cents, true) + kpi('Cash', s.cash_cents) + kpi('Inventaire', s.inventory_value_cents) +
      kpi('Escrow achats', s.buy_escrow_cents) + kpi('P&L réalisé', s.realized_pnl_cents) + kpi('P&L latent', s.unrealized_pnl_cents) +
      percentKpi('Rendement réalisé 30j', s.monthly_return_bp) +
      `<div class="trade-kpi"><div class="k">Jours actifs</div><div class="v">${fmtQty(s.days_running)}</div></div>` +
      `<div class="trade-kpi"><div class="k">Alertes</div><div class="v">${fmtQty((data.alerts || []).length)}</div></div>`;
    return shell('Dashboard', `<div class="trade-kpis">${cards}</div><div class="trade-insights">${insight('Meilleure position', winner)}${insight('Position à surveiller', loser)}</div>`);
  }
  function assets(data) {
    const selectedPath = data.asset_source && (Array.isArray(data.asset_source.path) ? data.asset_source.path.join(' / ') : data.asset_source.name);
    const columns = [
      { label: 'Item', render: r => textCell(assetName(r), 'item') },
      { label: 'Qté', num: true, render: r => `<td class="num">${fmtQty(pick(r, ['quantity', 'qty'], null))}</td>` },
      { label: 'Conteneur', render: r => textCell(pick(r, ['container_path', 'container_name'], selectedPath || '–')) },
      { label: 'Source', render: r => textCell(pick(r, ['acquisition_source', 'source'], '–')) },
      { label: 'Coût FIFO', num: true, render: r => moneyCell(pick(r, ['fifo_unit_cost_cents', 'unit_cost_cents', 'avg_cost_cents'], null)) },
      { label: 'Marché', num: true, render: r => moneyCell(pick(r, ['market_unit_cents', 'market_price_cents', 'valuation_price_cents', 'market_median_cents'], null)) },
      { label: 'Valeur', num: true, render: r => moneyCell(r.inventory_value_cents) },
      { label: 'P&L latent', num: true, render: r => moneyCell(pick(r, ['unrealized_pnl_cents', 'latent_pnl_cents'], null)) },
      { label: 'Profit projeté', num: true, render: r => moneyCell(r.projected_profit_cents) },
      { label: 'Taxes / frais', num: true, render: r => moneyCell(pick(r, ['taxes_fees_cents', 'projected_fees_cents', 'fees_cents'], null)) },
      { label: 'Marge', num: true, render: r => { const value = pick(r, ['margin_bps', 'margin_bp'], null); return `<td class="num ${tone(value)}">${fmtBps(value)}</td>`; } },
      { label: 'Liquidité', num: true, render: r => `<td class="num">${fmtQty(pick(r, ['liquidity_volume_30d', 'volume_30d', 'daily_volume'], null))}</td>` },
      { label: 'État', render: r => textCell(pick(r, ['listing_state', 'status', 'action'], '–')) }
    ];
    return shell('Assets', table(data.assets, columns, 'Aucun asset dans ce conteneur.'), '<select id="trade-container-filter" class="trade-select" aria-label="Conteneur d’assets"></select>');
  }
  function transactions(data) {
    const columns = [
      { label: 'Date', render: r => textCell(pick(r, ['date', 'occurred_at'], '–')) },
      { label: 'Sens', render: r => textCell(pick(r, ['side', 'direction'], '–')) },
      { label: 'Item', render: r => textCell(assetName(r), 'item') },
      { label: 'Qté', num: true, render: r => `<td class="num">${fmtQty(pick(r, ['quantity', 'qty'], null))}</td>` },
      { label: 'Prix unitaire', num: true, render: r => moneyCell(r.unit_price_cents) },
      { label: 'Taxes / frais', num: true, render: r => moneyCell(pick(r, ['tax_fee_cents', 'fees_cents', 'tax_cents'], null)) },
      { label: 'Statut frais', render: r => textCell(pick(r, ['fee_status'], 'indisponible')) },
      { label: 'Valeur', num: true, render: r => moneyCell(pick(r, ['value_cents', 'total_cents'], null)) },
      { label: 'Coût FIFO', num: true, render: r => moneyCell(r.fifo_cost_cents) },
      { label: 'P&L réalisé', num: true, render: r => moneyCell(r.realized_pnl_cents) },
      { label: 'Owner', render: r => textCell(pick(r, ['owner_name', 'owner'], '–')) },
      { label: 'Station', render: r => textCell(pick(r, ['station_name', 'station'], '–')) }
    ];
    return shell('Transactions', table(data.transactions, columns, 'Aucune transaction pour cette source.'));
  }
  function alertTone(row) {
    const level = String(pick(row, ['severity', 'level'], '')).toLowerCase();
    return /critical|high|danger|error/.test(level) ? 'bad' : (/warn|medium|risk/.test(level) ? 'warn' : 'good');
  }
  function alerts(data) {
    const rows = Array.isArray(data.alerts) ? data.alerts : [];
    const cards = rows.length ? `<div class="trade-alerts">${rows.map(row => { const cls = alertTone(row); return `<article class="trade-alert ${cls}">` +
      `<div class="trade-alert-head"><span class="trade-chip ${cls}">${esc(pick(row, ['severity', 'level'], 'Info'))}</span><span class="trade-chip ${cls}">${esc(pick(row, ['action'], 'Voir'))}</span></div>` +
      `<div class="trade-alert-name">${esc(assetName(row))}</div><div class="trade-alert-message">${esc(pick(row, ['message', 'reason'], 'Aucun détail'))}</div>` +
      `<div class="trade-alert-impact ${tone(pick(row, ['impact_cents', 'value_cents'], null))}">Impact : ${fmtCents(pick(row, ['impact_cents', 'value_cents'], null))}</div></article>`; }).join('')}</div>` : '<div class="trade-empty">Aucune alerte active.</div>';
    return shell('Alerts', cards);
  }

  function updateCounts(data) {
    [['tab-assets-c', data.assets], ['tab-transactions-c', data.transactions], ['tab-alerts-c', data.alerts]].forEach(([id, rows]) => { const node = el(id); if (node) node.textContent = Array.isArray(rows) ? rows.length : 0; });
  }
  function render() {
    const root = el('trade-view'); if (!root || window.__state.workspace === 'orders') return;
    if (!T.workspace) { root.innerHTML = '<div class="trade-loading">Chargement des données trading…</div>'; return; }
    if (!T.workspace.ok) { root.innerHTML = `<div class="trade-error">${esc(T.workspace.error || 'Données trading indisponibles.')}<br><button class="btn small" onclick="TradingUI.refresh()">Réessayer</button></div>`; return; }
    const view = window.__state.workspace;
    root.innerHTML = view === 'dashboard' ? dashboard(T.workspace) : (view === 'assets' ? assets(T.workspace) : (view === 'transactions' ? transactions(T.workspace) : alerts(T.workspace)));
    if (view === 'assets') hydrateContainerFilter();
  }
  function layout(view) {
    const orders = view === 'orders'; window.__state.workspace = view;
    const metrics = el('orders-metrics'), grid = el('orders-grid'), trade = el('trade-view');
    if (metrics) metrics.hidden = !orders; if (grid) grid.hidden = !orders; if (trade) trade.hidden = orders;
    document.querySelectorAll('#tabs .tab').forEach(tab => tab.classList.toggle('active', orders ? tab.dataset.tab === window.__state.tab : tab.dataset.workspace === view));
  }
  function show(view) { if (!VALID_VIEWS.has(view)) return false; layout(view); window.__state.selIndex = -1; render(); if (!T.settings) fetchSettings(false).then(render); if (!T.workspace || !T.workspace.ok) loadWorkspace(false); return true; }
  function showOrders() { layout('orders'); return true; }
  async function call(name, payload) { const bridge = api(); if (!bridge || typeof bridge[name] !== 'function') return undefined; return Promise.resolve(payload === undefined ? bridge[name]() : bridge[name](payload)); }
  function filters() { return { container_key: T.containerKey || null }; }
  function acceptWorkspace(data) { T.workspace = data || { ok: false, error: 'Réponse trading vide.' }; if (T.workspace.ok) updateCounts(T.workspace); render(); return T.workspace; }
  async function loadWorkspace(refresh) {
    const request = ++T.request; if (window.__state.workspace !== 'orders') { T.workspace = null; render(); }
    try {
      let data = await call(refresh ? 'refresh_trade_workspace' : 'get_trade_workspace', filters());
      if (refresh && data == null) data = await call('get_trade_workspace', filters());
      if (request !== T.request) return null;
      return acceptWorkspace(data === undefined ? { ok: false, error: 'API trading indisponible.' } : data);
    } catch (error) { if (request === T.request) acceptWorkspace({ ok: false, error: String(error) }); return null; }
  }
  async function fetchSettings(force) {
    if (T.settings && !force) return T.settings; const request = ++T.settingsRequest;
    try { const data = await call('get_trade_settings'); if (request !== T.settingsRequest) return T.settings; T.settings = data || { ok: false, error: 'Réponse settings vide.' }; if (T.settings.ok && !T.containerKey) T.containerKey = selectedKey(T.settings.containers, T.settings.asset_source, ['character_id', 'item_id']); return T.settings; }
    catch (error) { if (request === T.settingsRequest) T.settings = { ok: false, error: String(error) }; return T.settings; }
  }
  function selectedKey(items, source, fields) {
    if (!source) return ''; const exact = (items || []).find(item => String(item.key) === String(source.key || source.container_key || source.division_key || '')); if (exact) return String(exact.key);
    const match = (items || []).find(item => fields.every(field => source[field] != null && String(item[field]) === String(source[field]))); return match ? String(match.key) : '';
  }
  function option(select, value, label, title) { const node = document.createElement('option'); node.value = value; node.textContent = label; node.title = title || label; select.appendChild(node); }
  function fillDivisionSelect(select, data) {
    option(select, '', 'Choisir une division…'); (data.divisions || []).forEach(row => option(select, String(row.key), `${row.corporation_name || `Corporation ${row.corporation_id}`} · ${row.name || row.label || 'Division'} · ID ${row.division_id}`, `Corporation ${row.corporation_id} · division ${row.division_id}`));
    select.value = selectedKey(data.divisions, data.wallet_source, ['corporation_id', 'division_id']);
  }
  function fillContainerSelect(select, data, placeholder) {
    option(select, '', placeholder || 'Choisir un conteneur…'); (data.containers || []).forEach(row => { const indent = '— '.repeat(Math.max(0, Number(row.depth) || 0)); option(select, String(row.key), `${indent}${row.name || 'Conteneur'} · ID ${row.item_id}`, Array.isArray(row.path) ? row.path.join(' / ') : String(row.path || row.name || '')); });
    select.value = T.containerKey || selectedKey(data.containers, data.asset_source, ['character_id', 'item_id']); select.disabled = !(data.containers || []).length;
  }
  function hydrateContainerFilter() {
    const select = el('trade-container-filter'); if (!select) return; if (!T.settings || !T.settings.ok) { option(select, '', 'Configuration requise'); select.disabled = true; return; }
    fillContainerSelect(select, T.settings, 'Choisir un conteneur…'); select.onchange = () => { if (!select.value) return; T.containerKey = String(select.value); loadWorkspace(false); };
  }
  async function loadSettings(slot) {
    if (!slot) return; slot.innerHTML = '<div class="trade-settings-box trade-loading">Chargement des divisions et conteneurs…</div>';
    const data = await fetchSettings(true); if (!data || !data.ok) { slot.innerHTML = `<div class="trade-settings-box trade-error">${esc((data && data.error) || 'Settings trading indisponibles.')}</div>`; return; }
    const issueText = (data.errors || []).map(row => pick(row && row.error ? row.error : row, ['message', 'error'], 'Découverte ESI partielle')).join(' · ');
    slot.innerHTML = (issueText ? `<div class="trade-sync-warning">${esc(issueText)}</div>` : '') + '<div class="trade-settings-box"><div class="trade-settings-title">Sources Asset / Transaction</div><div class="trade-settings-grid">' +
      '<label for="trade-division-select">Division wallet corporation</label><select id="trade-division-select" class="trade-select"></select>' +
      '<label for="trade-container-select">Conteneur assets personnel</label><select id="trade-container-select" class="trade-select"></select></div>' +
      '<div class="trade-settings-actions"><span id="trade-settings-status" class="trade-settings-status">Division requise; conteneur personnel optionnel. Sélections enregistrées par ID.</span><button id="trade-settings-save" class="btn primary">Sauver</button></div></div>';
    const division = el('trade-division-select'), container = el('trade-container-select'), save = el('trade-settings-save');
    fillDivisionSelect(division, data); fillContainerSelect(container, data); const validate = () => { save.disabled = !division.value; };
    division.onchange = validate; container.onchange = validate; save.onclick = () => saveSettings(); validate();
  }
  async function saveSettings() {
    const divisionSelect = el('trade-division-select'), containerSelect = el('trade-container-select'), status = el('trade-settings-status'), save = el('trade-settings-save');
    if (!divisionSelect || !containerSelect || !divisionSelect.value || !T.settings) return false;
    const division = (T.settings.divisions || []).find(row => String(row.key) === String(divisionSelect.value)); const container = (T.settings.containers || []).find(row => String(row.key) === String(containerSelect.value)); if (!division || (containerSelect.value && !container)) return false;
    const payload = { wallet_source: { key: String(division.key), corporation_id: asId(division.corporation_id), division_id: asId(division.division_id) }, asset_source: container ? { key: String(container.key), character_id: asId(container.character_id), item_id: asId(container.item_id) } : null };
    save.disabled = true; status.textContent = 'Sauvegarde…';
    try { const result = await call('save_trade_settings', payload); if (result === undefined || (result && result.ok === false)) throw new Error((result && result.error) || 'Sauvegarde indisponible'); T.settings.wallet_source = payload.wallet_source; T.settings.asset_source = payload.asset_source; T.containerKey = payload.asset_source ? payload.asset_source.key : ''; status.textContent = 'Sources sauvegardées.'; await loadWorkspace(false); return true; }
    catch (error) { status.textContent = `Erreur : ${String(error)}`; return false; } finally { save.disabled = false; }
  }
  async function init() { if (T.initialized) return; T.initialized = true; showOrders(); }

  window.TradingUI = { init, show, showOrders, refresh: () => loadWorkspace(true), loadSettings, saveSettings, acceptWorkspace, fmtCents };
  window.renderTradeWorkspace = acceptWorkspace;
  if (api()) init();
  else window.addEventListener('pywebviewready', init);
}());
