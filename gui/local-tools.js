/* Compact read-only Local tools; no EVE client input or trading dependencies. */
(() => {
  'use strict';
  window.loadLocalTools = async () => {
    const slot = document.getElementById('local-tools-settings');
    if (!slot) return;
    slot.innerHTML = '<div class="cfg-note"><b>Local tools</b> <button type="button" class="btn small" id="local-analyze-now">Analyze Clipboard Now</button><span id="local-tools-status" role="status"></span></div><div id="local-watchdog-fields" class="cfg-note"></div>';
    slot.querySelector('#local-analyze-now').onclick = () => window.analyzeLocalClipboard();
    const status = slot.querySelector('#local-tools-status');
    try {
      const api = window.pywebview?.api;
      const result = await api.get_local_watchdog_settings();
      if (!result.ok) throw new Error('Watchdog unavailable');
      const settings = result.settings;
      const fields = slot.querySelector('#local-watchdog-fields');
      fields.innerHTML = '<label><input type="checkbox" data-local-setting="enabled"> Local Watchdog (flood alerts)</label> ' +
        [['threshold', 'msg/min', 1000], ['duration', 'minutes', 60], ['cooldown', 'cooldown min', 120]].map(([key,label,max]) =>
          `<label><input style="width:64px" type="number" min="1" max="${max}" data-local-setting="${key}"> ${label}</label>`).join(' ');
      fields.querySelectorAll('input').forEach(input => {
        const key = input.dataset.localSetting;
        if (key === 'enabled') input.checked = settings[key]; else input.value = settings[key];
      });
      fields.onchange = async () => {
        const values = {};
        fields.querySelectorAll('input').forEach(input => { values[input.dataset.localSetting] = input.type === 'checkbox' ? input.checked : Number(input.value); });
        try {
          const saved = await api.set_local_watchdog_settings(values);
          status.textContent = saved.ok ? 'Saved' : 'Unable to save';
        } catch (_) { status.textContent = 'Unable to save'; }
      };
      const health = await api.get_local_analyzer_status();
      status.textContent = health.watcher_running ? 'Clipboard watcher running' : 'Clipboard watcher stopped';
    } catch (_) { status.textContent = 'Local tools unavailable'; }
  };
})();
