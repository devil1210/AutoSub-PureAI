let currentConfig = {};
let allHistoryRecords = [];
let _seriesCatalog = [];
let currentProfileData = null;
let profileMode = 'visual';

const ICON_TRASH = '<svg fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" style="width:13px; height:13px;"><path stroke-linecap="round" stroke-linejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>';
const ICON_REFRESH = '<svg fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" style="width:13px; height:13px;"><path stroke-linecap="round" stroke-linejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>';

function showToast(msg) {
  const t = document.getElementById('toast');
  t.innerText = msg;
  t.style.display = 'block';
  setTimeout(() => { t.style.display = 'none'; }, 3200);
}

function switchTab(tabId) {
  document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.nav-tab').forEach(el => el.classList.remove('active'));
  document.getElementById('tab-' + tabId).classList.add('active');
  document.getElementById('tab-btn-' + tabId).classList.add('active');

  if (tabId === 'history') loadHistory();
  if (tabId === 'profiles') loadProfilesCatalog();
  if (tabId === 'settings') loadSettingsForm();
}

function togglePasswordVisibility(elemId) {
  const el = document.getElementById(elemId);
  el.type = el.type === 'password' ? 'text' : 'password';
}

// === TELEMETRÍA Y POLLING ===
async function pollStatus() {
  try {
    const res = await fetch('/api/status');
    const data = await res.json();
    currentConfig = data.config || {};

    // GPU status
    const pc = data.pc_gpu || {};
    const gpuDot = document.getElementById('gpu-dot');
    const gpuText = document.getElementById('gpu-status-text');
    if (pc.online) {
      gpuDot.style.background = '#10b981';
      gpuText.innerText = 'Online (' + pc.active_model + ')';
    } else {
      gpuDot.style.background = '#ef4444';
      gpuText.innerText = 'Apagada / Offline';
    }
    document.getElementById('gpu-model-sub').innerText = 'IP: ' + (pc.ip || '192.168.1.12');

    // Metrics
    const m = data.metrics || {};
    document.getElementById('current-file-text').innerText = m.currently_processing || 'Ninguno';
    document.getElementById('current-time-sub').innerText = m.current_elapsed_formatted || '0s';
    document.getElementById('jobs-done-text').innerText = m.completed_jobs_count || 0;
    document.getElementById('total-time-sub').innerText = 'Tiempo total: ' + (m.total_processing_time_formatted || '0s');
    document.getElementById('queue-count-text').innerText = m.active_queue_count || 0;
    document.getElementById('retry-count-sub').innerText = (data.pending_retry_count || 0) + ' reintentos';

    // Badges
    document.getElementById('badge-history-count').innerText = m.history_count || 0;
    document.getElementById('badge-profiles-count').innerText = m.profiles_count || 0;

    // Progress Bar
    const pBox = document.getElementById('progress-box');
    if (m.currently_processing && m.currently_processing !== 'Ninguno') {
      pBox.style.display = 'block';
      const stageBadge = document.getElementById('progress-stage-badge');
      if (stageBadge) {
        stageBadge.innerText = m.current_stage_badge || 'En curso';
        if (m.current_stage_badge === 'Extracción') {
          stageBadge.className = 'badge badge-blue';
        } else if (m.current_stage_badge === 'Diarización') {
          stageBadge.className = 'badge badge-amber';
        } else if (m.current_stage_badge === 'Traducción') {
          stageBadge.className = 'badge badge-purple';
        } else if (m.current_stage_badge === 'Continuidad') {
          stageBadge.className = 'badge badge-blue';
        } else if (m.current_stage_badge === 'Control QA') {
          stageBadge.className = 'badge badge-green';
        } else {
          stageBadge.className = 'badge badge-purple';
        }
      }

      const pTitle = document.getElementById('progress-title');
      if (pTitle) {
        pTitle.innerText = m.current_stage || (m.total_batches > 0 ? ('Lote ' + m.current_batch + ' de ' + m.total_batches) : 'Procesando...');
      }

      const pct = m.progress_percent !== undefined ? m.progress_percent : 0;
      document.getElementById('progress-percentage').innerText = pct + '%';
      document.getElementById('progress-bar').style.width = pct + '%';

      const pLeft = document.getElementById('progress-sub-left');
      const pRight = document.getElementById('progress-sub-right');
      if (pLeft) {
        pLeft.innerText = m.current_stage_detail || (m.total_batches > 0 ? ('Lote ' + m.current_batch + ' de ' + m.total_batches) : 'Preparando...');
      }
      if (pRight) {
        pRight.innerText = m.current_elapsed_formatted || '0s';
      }
    } else {
      pBox.style.display = 'none';
    }

    // Quick switches
    document.getElementById('local-only-switch').checked = currentConfig.local_only !== false;
    document.getElementById('proofread-switch').checked = currentConfig.proofread !== false;
    document.getElementById('fast-mode-switch').checked = currentConfig.fast_mode !== false;
    document.getElementById('quick-batch-size').value = currentConfig.batch_size || 100;

    const modeLbl = document.getElementById('local-mode-label');
    if (currentConfig.local_only) {
      modeLbl.className = 'badge badge-blue';
      modeLbl.innerText = 'Solo Local';
    } else {
      modeLbl.className = 'badge badge-green';
      modeLbl.innerText = 'Fallback Nube';
    }

    // Active Queue
    renderActiveQueue(data.active_jobs_queue || []);

  } catch (err) {
    console.warn("Poll status error", err);
  }
}

function renderActiveQueue(queueItems) {
  const tbody = document.getElementById('active-queue-tbody');
  if (!queueItems || queueItems.length === 0) {
    tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; color:var(--text-muted); padding:16px;">No hay archivos en la cola.</td></tr>';
    return;
  }
  tbody.innerHTML = queueItems.map(item => `
    <tr>
      <td><span class="badge badge-purple">#${item.index}</span></td>
      <td>
        <strong>${item.file_name}</strong>
        ${item.context_only ? '<span class="badge badge-blue" style="margin-left:6px; font-size:10px;">🧠 Solo Contexto</span>' : (item.target_languages ? `<span class="badge badge-green" style="margin-left:6px; font-size:10px;">🌐 ${item.target_languages.join(', ')}</span>` : '')}
      </td>
      <td style="color:var(--text-muted); font-size:12px;">${item.path}</td>
      <td>
        <button class="btn btn-danger btn-sm" onclick="cancelQueueItem('${item.path.replace(/'/g, "\\'")}')">Eliminar</button>
      </td>
    </tr>
  `).join('');
}

async function cancelQueueItem(path) {
  await fetch('/api/cancel_queue_item', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({path: path})
  });
  showToast('Elemento eliminado de la cola');
  pollStatus();
}

async function cancelCurrentJob() {
  if (confirm('¿Seguro que deseas cancelar la traducción en curso?')) {
    await fetch('/api/cancel', {method: 'POST'});
    showToast('Cancelación solicitada...');
  }
}

async function toggleQuickConfig(key) {
  const val = document.getElementById(key.replace('_', '-') + '-switch').checked;
  const payload = {};
  payload[key] = val;
  await fetch('/api/config', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload)
  });
  showToast('Configuración actualizada');
  pollStatus();
}

async function updateBatchSize(val) {
  await fetch('/api/config', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({batch_size: parseInt(val)})
  });
  showToast('Tamaño de lote cambiado a ' + val);
}

// === MEDIA CATALOG (Series & Películas) ===
async function loadMediaCatalog() {
  try {
    const res = await fetch('/api/series_list');
    const data = await res.json();
    _seriesCatalog = data.catalog || [];
    const sel = document.getElementById('media-catalog-select');
    sel.innerHTML = '<option value="">-- Seleccionar Serie o Película --</option>' +
      _seriesCatalog.map(item => `<option value="${item.path.replace(/"/g, '&quot;')}">[${item.category}] ${item.name}</option>`).join('');
  } catch (err) {
    console.error(err);
  }
}

async function onCatalogItemSelected() {
  const path = document.getElementById('media-catalog-select').value;
  const container = document.getElementById('media-files-list');
  updateMainCuratorButton();
  if (!path) {
    container.innerHTML = '<div style="color:var(--text-muted); font-size:13px; padding:10px;">Selecciona una serie o película arriba.</div>';
    return;
  }
  container.innerHTML = '<div style="color:var(--text-muted); font-size:13px; padding:10px;">Cargando archivos...</div>';
  try {
    const res = await fetch('/api/series_files?path=' + encodeURIComponent(path));
    const data = await res.json();
    const files = data.files || [];
    if (files.length === 0) {
      container.innerHTML = '<div style="color:var(--text-muted); font-size:13px; padding:10px;">No se encontraron videos .mkv/.mp4.</div>';
      return;
    }
    container.innerHTML = files.map((f, i) => {
      const subBadge = f.has_sub
        ? '<span class="badge badge-green" style="font-size:10px;">Tiene Sub</span>'
        : '<span class="badge badge-amber" style="font-size:10px;">Sin Sub</span>';
      return `
        <label class="check-item">
          <input type="checkbox" class="media-file-chk" value="${f.path.replace(/"/g, '&quot;')}">
          <div class="check-item-name">
            <div>${f.name}</div>
            <div style="font-size:10px; color:var(--text-muted); display:flex; gap:6px; margin-top:2px;">
              <span>${f.size_mb} MB</span> ${subBadge}
            </div>
          </div>
        </label>
      `;
    }).join('');
  } catch (err) {
    container.innerHTML = '<div style="color:red; font-size:13px; padding:10px;">Error cargando archivos: ' + err + '</div>';
  }
}

function selectChecklist(select) {
  document.querySelectorAll('.media-file-chk').forEach(c => c.checked = select);
}

async function enqueueSelectedMedia(contextOnly = false) {
  const chks = Array.from(document.querySelectorAll('.media-file-chk:checked')).map(c => c.value);
  if (chks.length === 0) {
    alert('Por favor selecciona al menos un archivo de la lista.');
    return;
  }
  const targetLangs = Array.from(document.querySelectorAll('.enqueue-target-chk:checked')).map(c => c.value);
  if (!contextOnly && targetLangs.length === 0) {
    alert('Por favor selecciona al menos un idioma destino para la traducción.');
    return;
  }
  const res = await fetch('/api/batch_enqueue', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      paths: chks,
      force: true,
      context_only: !!contextOnly,
      target_languages: targetLangs
    })
  });
  const data = await res.json();
  showToast(contextOnly
    ? `¡${data.enqueued_count || chks.length} archivo(s) encolados para Solo Contexto!`
    : `¡${data.enqueued_count || chks.length} archivo(s) agregados a la cola (${targetLangs.join(', ')})!`);
  pollStatus();
}

// === TAB 2: HISTORIAL ===
async function loadHistory() {
  try {
    const res = await fetch('/api/history');
    const data = await res.json();
    allHistoryRecords = data.history || [];
    document.getElementById('hist-total-badge').innerText = allHistoryRecords.length + ' registros';
    renderHistoryTable(allHistoryRecords);
  } catch (err) {
    console.error(err);
  }
}

function renderHistoryTable(records) {
  const tbody = document.getElementById('history-tbody');
  if (!records || records.length === 0) {
    tbody.innerHTML = '<tr><td colspan="9" style="text-align:center; color:var(--text-muted); padding:16px;">No hay registros en el historial todavía.</td></tr>';
    return;
  }
  tbody.innerHTML = records.map(r => {
    let durColor = 'badge-green';
    if (r.duration_sec > 180) durColor = 'badge-purple';
    else if (r.duration_sec > 60) durColor = 'badge-blue';

    const isContextOnly = (r.format === 'Contexto' || r.status === 'Contexto Extraído' || (r.format && r.format.startsWith('Contexto')));
    const proofBadge = isContextOnly
      ? '<span class="badge badge-purple">🧠 Contexto</span>'
      : (r.proofread ? '<span class="badge badge-green">Sí</span>' : '<span class="badge badge-gray">No</span>');

    let qaBadge = '<span class="badge badge-gray">-</span>';
    if (r.qa_score !== undefined && r.qa_score !== null) {
      let bClass = 'badge-green';
      if (r.qa_score < 70) bClass = 'badge-red';
      else if (r.qa_score < 80) bClass = 'badge-amber';
      else if (r.qa_score < 90) bClass = 'badge-yellow';
      qaBadge = `<span class="badge ${bClass}" title="${r.qa_summary || ''}">${r.qa_badge || (r.qa_score + '%')}</span>`;
    }

    return `
      <tr>
        <td style="font-size:12px; color:var(--text-muted);">${r.completed_at || '-'}</td>
        <td><strong>${r.title || '-'}</strong></td>
        <td title="${r.path || ''}" style="word-break:break-all;">
          ${r.file_name}
          ${isContextOnly ? '<span class="badge badge-blue" style="font-size:10px; margin-left:4px;">Solo Contexto</span>' : ''}
        </td>
        <td><span class="badge ${durColor}">${r.duration_formatted || r.duration_sec + 's'}</span></td>
        <td>${r.lines_count || 0}</td>
        <td style="font-size:12px;">${r.model || 'Auto'}</td>
        <td>${proofBadge}</td>
        <td>${qaBadge}</td>
        <td>
          <button class="btn btn-secondary btn-sm" onclick="reprocessSingleFile('${(r.path || '').replace(/'/g, "\\'")}')">
            ${ICON_REFRESH} Reprocesar
          </button>
        </td>
      </tr>
    `;
  }).join('');
}

function filterHistory() {
  const q = document.getElementById('hist-search').value.toLowerCase().trim();
  if (!q) {
    renderHistoryTable(allHistoryRecords);
    return;
  }
  const filtered = allHistoryRecords.filter(r =>
    (r.title && r.title.toLowerCase().includes(q)) ||
    (r.file_name && r.file_name.toLowerCase().includes(q))
  );
  renderHistoryTable(filtered);
}

async function reprocessSingleFile(filePath) {
  if (!filePath) return;
  const res = await fetch('/api/batch_enqueue', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({paths: [filePath], force: true})
  });
  showToast('¡Archivo reenviado a la cola para reprocesar!');
  pollStatus();
}

async function clearHistory() {
  if (confirm('¿Deseas vaciar todo el registro de historial?')) {
    await fetch('/api/history/clear', {method: 'POST'});
    allHistoryRecords = [];
    renderHistoryTable([]);
    document.getElementById('hist-total-badge').innerText = '0 registros';
    showToast('Historial vaciado');
  }
}

// === TAB 3: PERFILES Y CURADOR NOCTURNO ===
let allLoadedProfiles = [];

async function loadProfilesCatalog() {
  try {
    const res = await fetch('/api/profiles');
    const data = await res.json();
    allLoadedProfiles = data.profiles || [];
    const sel = document.getElementById('profile-selector');
    if (sel) {
      sel.innerHTML = '<option value="">-- Seleccionar Perfil --</option>' +
        allLoadedProfiles.map(p => `<option value="${p.name}">[${p.characters_count} chars] ${p.name}</option>`).join('');
    }
    renderCuratorSeriesPills(allLoadedProfiles);
    updateProfileCuratorSwitch();
  } catch (err) {
    console.error(err);
  }
}

function onCuratorAddSelect(seriesName) {
  if (!seriesName) return;
  toggleCuratorSeries(seriesName, true);
  const sel = document.getElementById('curator-add-select');
  if (sel) sel.value = '';
}

function renderCuratorSeriesPills(profiles) {
  const container = document.getElementById('curator-active-chips') || document.getElementById('curator-series-pills');
  const addSelect = document.getElementById('curator-add-select');
  const whitelist = currentConfig.curator_whitelist || [];

  const countBadge = document.getElementById('curator-whitelist-count-badge');
  if (countBadge) {
    countBadge.innerText = `${whitelist.length} ${whitelist.length === 1 ? 'serie activa' : 'series activas'}`;
    countBadge.className = whitelist.length > 0 ? 'badge badge-green' : 'badge badge-gray';
  }

  // Populate Add Select with series not in whitelist
  if (addSelect && profiles && profiles.length > 0) {
    const unselected = profiles
      .filter(p => !whitelist.includes(p.name))
      .sort((a, b) => a.name.localeCompare(b.name));

    let optionsHtml = '<option value="">➕ Añadir serie a revisión nocturna...</option>';
    if (unselected.length === 0) {
      optionsHtml += '<option value="" disabled>(Todas las series ya están añadidas)</option>';
    } else {
      unselected.forEach(p => {
        const escVal = p.name.replace(/"/g, '&quot;');
        optionsHtml += `<option value="${escVal}">+ ${p.name}</option>`;
      });
    }
    addSelect.innerHTML = optionsHtml;
    addSelect.value = '';
  }

  if (!container) return;

  if (whitelist.length === 0) {
    container.innerHTML = '<span style="color:var(--text-muted); font-size:12px; font-style:italic;">Ninguna serie activa en el curador nocturno. Selecciona una en el desplegable superior para incluirla.</span>';
    return;
  }

  container.innerHTML = whitelist.map(name => {
    const escName = name.replace(/'/g, "\\'");
    const dispName = name.replace(/</g, '&lt;').replace(/>/g, '&gt;');
    return `
      <div style="display:inline-flex; align-items:center; gap:6px; background:rgba(16,185,129,0.15); border:1px solid rgba(16,185,129,0.45); color:#34d399; padding:4px 10px; border-radius:18px; font-size:12px; font-weight:600; box-shadow:0 1px 3px rgba(0,0,0,0.2);">
        <span style="font-size:11px;">✓</span>
        <span>${dispName}</span>
        <button type="button" onclick="toggleCuratorSeries('${escName}', false)"
          style="background:transparent; border:none; color:rgba(255,255,255,0.6); cursor:pointer; font-size:13px; font-weight:bold; line-height:1; padding:0 3px; margin-left:4px; display:inline-flex; align-items:center; border-radius:50%;"
          onmouseover="this.style.color='#f87171'" onmouseout="this.style.color='rgba(255,255,255,0.6)'"
          title="Quitar '${dispName}' de la revisión nocturna">
          ✕
        </button>
      </div>
    `;
  }).join('');
}

async function toggleCuratorSeries(seriesName, enable) {
  if (!seriesName) return;
  try {
    const res = await fetch('/api/curator_toggle', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({series: seriesName, enabled: enable})
    });
    const data = await res.json();
    if (data.status === 'ok') {
      currentConfig.curator_whitelist = data.curator_whitelist || [];
      showToast(enable ? `🌙 '${seriesName}' activada para remasterización nocturna` : `⚪ '${seriesName}' desactivada`);
      renderCuratorSeriesPills(allLoadedProfiles);
      updateProfileCuratorSwitch();
      updateMainCuratorButton();
    }
  } catch (err) {
    console.error("Error toggle curator series:", err);
  }
}

function updateProfileCuratorSwitch() {
  const sel = document.getElementById('profile-selector');
  const wrap = document.getElementById('prof-curator-wrap');
  if (!sel || !wrap) return;
  const name = sel.value;
  if (!name) {
    wrap.style.display = 'none';
    return;
  }
  wrap.style.display = 'inline-flex';
  const whitelist = currentConfig.curator_whitelist || [];
  const isWhitelisted = whitelist.includes(name);
  const sw = document.getElementById('prof-curator-switch');
  const badge = document.getElementById('prof-curator-badge');
  if (sw) sw.checked = isWhitelisted;
  if (badge) {
    badge.innerText = isWhitelisted ? 'Activa' : 'Inactiva';
    badge.className = isWhitelisted ? 'badge badge-green' : 'badge badge-gray';
  }
}

function toggleCuratorCurrentProfile(checked) {
  const name = document.getElementById('profile-selector').value;
  if (!name) return;
  toggleCuratorSeries(name, checked);
}

function getSelectedCatalogSeriesName() {
  const sel = document.getElementById('media-catalog-select');
  if (!sel || !sel.value) return null;
  const opt = sel.options[sel.selectedIndex];
  if (!opt) return null;
  const rawText = opt.innerText || '';
  const m = rawText.match(/^\[.*?\]\s*(.*)$/);
  return m ? m[1].trim() : rawText.trim();
}

function updateMainCuratorButton() {
  const btn = document.getElementById('btn-curator-quick');
  if (!btn) return;
  const seriesName = getSelectedCatalogSeriesName();
  if (!seriesName) {
    btn.style.display = 'none';
    return;
  }
  btn.style.display = 'inline-flex';
  const whitelist = currentConfig.curator_whitelist || [];
  const isWhitelisted = whitelist.includes(seriesName);
  const icon = document.getElementById('curator-quick-icon');
  const txt = document.getElementById('curator-quick-text');
  if (isWhitelisted) {
    btn.className = 'btn btn-success btn-sm';
    if (icon) icon.innerText = '🌙';
    if (txt) txt.innerText = 'Curador 3 AM (Activa)';
    btn.title = `Haz clic para DESACTIVAR '${seriesName}' del curador nocturno`;
  } else {
    btn.className = 'btn btn-secondary btn-sm';
    if (icon) icon.innerText = '⚪';
    if (txt) txt.innerText = 'Activar Curador 3 AM';
    btn.title = `Haz clic para ACTIVAR '${seriesName}' en el curador nocturno`;
  }
}

function toggleCuratorMainSeries() {
  const seriesName = getSelectedCatalogSeriesName();
  if (!seriesName) return;
  const whitelist = currentConfig.curator_whitelist || [];
  const isWhitelisted = whitelist.includes(seriesName);
  toggleCuratorSeries(seriesName, !isWhitelisted);
}

async function onProfileSelected() {
  const name = document.getElementById('profile-selector').value;
  updateProfileCuratorSwitch();
  if (!name) return;
  try {
    const res = await fetch('/api/profile?name=' + encodeURIComponent(name));
    const data = await res.json();
    if (data.status === 'ok') {
      currentProfileData = data.profile || {};
      renderProfileForm(currentProfileData);
    }
  } catch (err) {
    console.error(err);
  }
}

function renderProfileForm(p) {
  document.getElementById('prof-series-title').value = p.series_title || '';
  document.getElementById('prof-official-title').value = p.official_title || '';
  document.getElementById('prof-setting').value = p.setting || '';
  document.getElementById('prof-tone').value = p.tone_instructions || '';

  // Personajes
  const cTbody = document.getElementById('prof-characters-tbody');
  const chars = p.characters || [];
  cTbody.innerHTML = chars.map((c, idx) => `
    <tr>
      <td><input type="text" class="char-name" value="${(c.name || '').replace(/"/g, '&quot;')}"></td>
      <td>
        <select class="char-gender">
          <option value="Femenino" ${c.gender === 'Femenino' ? 'selected' : ''}>Femenino</option>
          <option value="Masculino" ${c.gender === 'Masculino' ? 'selected' : ''}>Masculino</option>
          <option value="Desconocido" ${c.gender === 'Desconocido' ? 'selected' : ''}>Desconocido</option>
        </select>
      </td>
      <td><input type="text" class="char-pers" value="${(c.personality || '').replace(/"/g, '&quot;')}"></td>
      <td><button class="btn btn-danger btn-sm" onclick="this.closest('tr').remove()">${ICON_TRASH}</button></td>
    </tr>
  `).join('');

  // Glosario
  const gTbody = document.getElementById('prof-glossary-tbody');
  const glossary = p.glossary || {};
  gTbody.innerHTML = Object.entries(glossary).map(([k, v]) => `
    <tr>
      <td><input type="text" class="gloss-key" value="${k.replace(/"/g, '&quot;')}"></td>
      <td><input type="text" class="gloss-val" value="${String(v).replace(/"/g, '&quot;')}"></td>
      <td><button class="btn btn-danger btn-sm" onclick="this.closest('tr').remove()">${ICON_TRASH}</button></td>
    </tr>
  `).join('');

  // Episodios y Continuidad
  const epContainer = document.getElementById('prof-episodes-container');
  const episodes = p.episodes || [];
  if (episodes.length === 0) {
    epContainer.innerHTML = '<div style="text-align:center; padding:18px; color:var(--text-muted); font-size:12px; background:var(--surface-subtle); border:1px dashed var(--border); border-radius:8px;">No hay capítulos registrados todavía para este perfil. Al procesar episodios de esta serie, su contexto narrativo se sintetizará y guardará aquí automáticamente.</div>';
  } else {
    epContainer.innerHTML = episodes.map((ep, idx) => `
      <div class="episode-context-card" style="background:var(--surface-subtle); border:1px solid var(--border); border-radius:8px; padding:12px;">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; gap:8px; flex-wrap:wrap;">
          <div style="display:flex; align-items:center; gap:8px;">
            <input type="text" class="ep-name" value="${(ep.episode || '').replace(/"/g, '&quot;')}" style="font-weight:700; width:130px; padding:4px 8px; font-size:12px;" placeholder="Episodio">
            <input type="text" class="ep-file" value="${(ep.file_name || '').replace(/"/g, '&quot;')}" style="width:230px; padding:4px 8px; font-size:11px; color:#94a3b8;" placeholder="Archivo">
            <span class="ep-date" style="font-size:11px; color:var(--text-muted);">${ep.updated_at || ep.processed_at || ''}</span>
          </div>
          <button class="btn btn-danger btn-sm" onclick="this.closest('.episode-context-card').remove()">${ICON_TRASH}</button>
        </div>
        <textarea class="ep-summary" rows="3" style="width:100%; font-size:12px; resize:vertical;" placeholder="Resumen del capítulo, giros de trama, dinámicas de personajes...">${(ep.summary || '').replace(/</g, '&lt;').replace(/>/g, '&gt;')}</textarea>
      </div>
    `).join('');
  }

  // JSON Raw
  document.getElementById('prof-json-raw').value = JSON.stringify(p, null, 2);
}

function addEpisodeContextRow() {
  const container = document.getElementById('prof-episodes-container');
  if (container.querySelector('div[style*="dashed"]')) {
    container.innerHTML = '';
  }
  const card = document.createElement('div');
  card.className = 'episode-context-card';
  card.style = 'background:var(--surface-subtle); border:1px solid var(--border); border-radius:8px; padding:12px;';
  const now = new Date().toISOString().replace('T', ' ').substring(0, 16);
  card.innerHTML = `
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; gap:8px; flex-wrap:wrap;">
      <div style="display:flex; align-items:center; gap:8px;">
        <input type="text" class="ep-name" placeholder="Ej. S01E01" style="font-weight:700; width:130px; padding:4px 8px; font-size:12px;">
        <input type="text" class="ep-file" placeholder="Nombre de archivo (opcional)" style="width:230px; padding:4px 8px; font-size:11px; color:#94a3b8;">
        <span class="ep-date" style="font-size:11px; color:var(--text-muted);">${now}</span>
      </div>
      <button class="btn btn-danger btn-sm" onclick="this.closest('.episode-context-card').remove()">${ICON_TRASH}</button>
    </div>
    <textarea class="ep-summary" rows="3" style="width:100%; font-size:12px; resize:vertical;" placeholder="Escribe el resumen del capítulo o contexto clave para los siguientes..."></textarea>
  `;
  container.prepend(card);
}

function addCharacterRow() {
  const tbody = document.getElementById('prof-characters-tbody');
  const tr = document.createElement('tr');
  tr.innerHTML = `
    <td><input type="text" class="char-name" placeholder="Nombre"></td>
    <td>
      <select class="char-gender">
        <option value="Femenino">Femenino</option>
        <option value="Masculino">Masculino</option>
        <option value="Desconocido">Desconocido</option>
      </select>
    </td>
    <td><input type="text" class="char-pers" placeholder="Rol y estilo de habla"></td>
    <td><button class="btn btn-danger btn-sm" onclick="this.closest('tr').remove()">${ICON_TRASH}</button></td>
  `;
  tbody.appendChild(tr);
}

function addGlossaryRow() {
  const tbody = document.getElementById('prof-glossary-tbody');
  const tr = document.createElement('tr');
  tr.innerHTML = `
    <td><input type="text" class="gloss-key" placeholder="Término en Inglés"></td>
    <td><input type="text" class="gloss-val" placeholder="Traducción Español Latino"></td>
    <td><button class="btn btn-danger btn-sm" onclick="this.closest('tr').remove()">${ICON_TRASH}</button></td>
  `;
  tbody.appendChild(tr);
}

function toggleProfileMode() {
  const vis = document.getElementById('profile-visual-container');
  const jsn = document.getElementById('profile-json-container');
  const btn = document.getElementById('btn-toggle-profile-mode');

  if (profileMode === 'visual') {
    // Sincronizar de visual a JSON
    collectProfileFromVisual();
    document.getElementById('prof-json-raw').value = JSON.stringify(currentProfileData, null, 2);
    vis.style.display = 'none';
    jsn.style.display = 'block';
    btn.innerText = '🎨 Modo Guiado';
    profileMode = 'json';
  } else {
    // Sincronizar de JSON a visual
    try {
      currentProfileData = JSON.parse(document.getElementById('prof-json-raw').value);
      renderProfileForm(currentProfileData);
      jsn.style.display = 'none';
      vis.style.display = 'block';
      btn.innerText = '💻 Modo JSON';
      profileMode = 'visual';
    } catch (e) {
      alert('Error en formato JSON: ' + e.message);
    }
  }
}

function collectProfileFromVisual() {
  const seriesTitle = document.getElementById('prof-series-title').value.trim();
  const officialTitle = document.getElementById('prof-official-title').value.trim();
  const setting = document.getElementById('prof-setting').value.trim();
  const tone = document.getElementById('prof-tone').value.trim();

  const characters = [];
  document.querySelectorAll('#prof-characters-tbody tr').forEach(tr => {
    const name = tr.querySelector('.char-name').value.trim();
    const gender = tr.querySelector('.char-gender').value;
    const pers = tr.querySelector('.char-pers').value.trim();
    if (name) characters.push({name, gender, personality: pers});
  });

  const glossary = {};
  document.querySelectorAll('#prof-glossary-tbody tr').forEach(tr => {
    const k = tr.querySelector('.gloss-key').value.trim();
    const v = tr.querySelector('.gloss-val').value.trim();
    if (k) glossary[k] = v;
  });

  const episodes = [];
  document.querySelectorAll('#prof-episodes-container .episode-context-card').forEach(card => {
    const epName = card.querySelector('.ep-name').value.trim();
    const epFile = card.querySelector('.ep-file').value.trim();
    const epDate = card.querySelector('.ep-date').innerText.trim();
    const epSummary = card.querySelector('.ep-summary').value.trim();
    if (epName || epSummary) {
      episodes.push({
        episode: epName || 'Capítulo',
        file_name: epFile,
        summary: epSummary,
        updated_at: epDate || new Date().toISOString().replace('T', ' ').substring(0, 16)
      });
    }
  });

  currentProfileData = {
    series_title: seriesTitle,
    official_title: officialTitle,
    setting: setting,
    characters: characters,
    glossary: glossary,
    tone_instructions: tone,
    episodes: episodes
  };
}

async function saveCurrentProfile() {
  if (profileMode === 'visual') {
    collectProfileFromVisual();
  } else {
    try {
      currentProfileData = JSON.parse(document.getElementById('prof-json-raw').value);
    } catch (e) {
      alert('Error de sintaxis en JSON: ' + e.message);
      return;
    }
  }

  const name = currentProfileData.series_title;
  if (!name) {
    alert('Por favor especifica un título para el perfil.');
    return;
  }

  const res = await fetch('/api/profile/save', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name: name, profile: currentProfileData})
  });
  const data = await res.json();
  if (data.status === 'ok') {
    showToast(`¡Perfil "${name}" guardado exitosamente!`);
    loadProfilesCatalog();
  } else {
    alert('Error guardando perfil');
  }
}

async function deleteCurrentProfile() {
  const sel = document.getElementById('profile-selector');
  const name = sel.value;
  if (!name) return;
  if (confirm(`¿Estás seguro de eliminar el perfil "${name}"?`)) {
    await fetch('/api/profile/delete', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: name})
    });
    showToast(`Perfil "${name}" eliminado`);
    loadProfilesCatalog();
    startNewProfile();
  }
}

function startNewProfile() {
  document.getElementById('profile-selector').value = '';
  currentProfileData = {
    series_title: 'Nueva Serie',
    official_title: '',
    setting: '',
    characters: [],
    glossary: {},
    tone_instructions: 'Español latino neutro.'
  };
  renderProfileForm(currentProfileData);
}

async function promptGenerateProfile() {
  const title = prompt('Ingresa el nombre del Anime o Película a consultar en Internet (Jikan/MyAnimeList):');
  if (!title) return;
  showToast('Generando perfil con IA y lore web...');
  const res = await fetch('/api/refresh_profile', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({series: title})
  });
  const data = await res.json();
  if (data.status === 'ok') {
    showToast('¡Perfil generado exitosamente!');
    await loadProfilesCatalog();
    document.getElementById('profile-selector').value = title;
    onProfileSelected();
  }
}

// === TAB 4: CONFIGURACIÓN ===
function renderLibrariesList(libs) {
  const container = document.getElementById('cfg-libraries-list');
  if (!container) return;
  container.innerHTML = '';
  if (!libs || libs.length === 0) {
    libs = [
      {name: 'Anime', path: '/data/tvshows'},
      {name: 'Películas', path: '/data/movies'}
    ];
  }
  libs.forEach(l => {
    addLibraryRow(l.name || '', l.path || '');
  });
}

function addLibraryRow(name = '', path = '') {
  const container = document.getElementById('cfg-libraries-list');
  if (!container) return;
  const div = document.createElement('div');
  div.className = 'library-row';
  div.style.cssText = 'display:flex; gap:10px; align-items:center; background:rgba(255,255,255,0.03); padding:8px 10px; border-radius:6px; border:1px solid var(--border);';
  div.innerHTML = `
    <div style="flex:1; max-width:220px;">
      <input type="text" class="lib-name-input" placeholder="Nombre (Ej: Anime)" value="${name.replace(/"/g, '&quot;')}" style="width:100%; font-weight:600;">
    </div>
    <div style="flex:2;">
      <input type="text" class="lib-path-input" placeholder="Ruta en servidor (Ej: /data/tvshows)" value="${path.replace(/"/g, '&quot;')}" style="width:100%;">
    </div>
    <button type="button" class="btn btn-danger btn-sm" onclick="removeLibraryRow(this)" title="Eliminar esta biblioteca" style="padding:6px 10px;">${ICON_TRASH}</button>
  `;
  container.appendChild(div);
}

function removeLibraryRow(btn) {
  const row = btn.closest('.library-row');
  const allRows = document.querySelectorAll('.library-row');
  if (allRows.length <= 1) {
    alert('Debes mantener al menos una biblioteca configurada.');
    return;
  }
  if (row) row.remove();
}

function loadSettingsForm() {
  document.getElementById('cfg-ollama-ip').value = currentConfig.ollama_ip || '';
  document.getElementById('cfg-ollama-port').value = currentConfig.ollama_port || 11434;
  const gKeys = currentConfig.gemini_api_keys;
  if (Array.isArray(gKeys) && gKeys.length > 0) {
    document.getElementById('cfg-gemini-key').value = gKeys.join(', ');
  } else {
    document.getElementById('cfg-gemini-key').value = currentConfig.gemini_api_key || '';
  }
  document.getElementById('cfg-cloud-model').value = currentConfig.cloud_model || 'gemini-3.5-flash-lite';
  const elModel = document.getElementById('cfg-elephant-model');
  if (elModel) {
    elModel.value = currentConfig.elephant_model || 'gemini-4-hydrogen';
  }
  document.getElementById('cfg-or-key').value = currentConfig.openrouter_api_key || '';
  document.getElementById('cfg-enable-or').checked = currentConfig.enable_openrouter === true;

  const srcPrio = currentConfig.source_priority || ['ja', 'en', 'fr', 'de', 'es', 'it', 'pt', 'ru', 'zh'];
  document.getElementById('cfg-source-priority').value = Array.isArray(srcPrio) ? srcPrio.join(', ') : srcPrio;

  const targetLangs = currentConfig.target_languages || ['es-419'];
  document.querySelectorAll('.cfg-target-chk').forEach(chk => {
    chk.checked = targetLangs.includes(chk.value);
  });

  let libs = currentConfig.libraries;
  if (!libs || !Array.isArray(libs) || libs.length === 0) {
    const rawPaths = currentConfig.watch_paths || ['/data/tvshows', '/data/movies'];
    libs = [];
    rawPaths.forEach(p => {
      if (typeof p === 'string' && p.includes(';')) {
        const parts = p.split(';');
        libs.push({path: parts[0].trim(), name: parts[1].trim()});
      } else if (typeof p === 'object' && p.path) {
        libs.push(p);
      } else {
        const sPath = String(p).trim();
        const sName = sPath.toLowerCase().includes('movie') ? 'Películas' : 'Anime';
        libs.push({path: sPath, name: sName});
      }
    });
  }
  renderLibrariesList(libs);

  loadOllamaModels();
}

async function loadOllamaModels() {
  try {
    const res = await fetch('/api/models');
    const data = await res.json();
    const models = data.models || ['qwen3.5:9b', 'qwen3:14b', 'qwen3:8b'];
    const sel = document.getElementById('cfg-local-model');
    sel.innerHTML = models.map(m => `<option value="${m}">${m}</option>`).join('');
    if (currentConfig.local_model) {
      sel.value = currentConfig.local_model;
    }
  } catch (err) {
    console.warn(err);
  }
}

async function saveAllSettings() {
  const libRows = document.querySelectorAll('.library-row');
  const libraries = [];
  const watchPaths = [];
  libRows.forEach(r => {
    const n = r.querySelector('.lib-name-input').value.trim();
    const p = r.querySelector('.lib-path-input').value.trim();
    if (p) {
      const finalName = n || (p.toLowerCase().includes('movie') ? 'Películas' : 'Series');
      libraries.push({name: finalName, path: p});
      watchPaths.push(`${p};${finalName}`);
    }
  });

  const sourcePriorityRaw = document.getElementById('cfg-source-priority').value;
  const sourcePriority = sourcePriorityRaw.split(',').map(s => s.trim().toLowerCase()).filter(s => s.length > 0);
  const targetLanguages = Array.from(document.querySelectorAll('.cfg-target-chk:checked')).map(c => c.value);

  const payload = {
    ollama_ip: document.getElementById('cfg-ollama-ip').value.trim(),
    ollama_port: parseInt(document.getElementById('cfg-ollama-port').value) || 11434,
    local_model: document.getElementById('cfg-local-model').value,
    gemini_api_key: document.getElementById('cfg-gemini-key').value.trim(),
    cloud_model: document.getElementById('cfg-cloud-model').value,
    elephant_model: document.getElementById('cfg-elephant-model') ? document.getElementById('cfg-elephant-model').value : 'gemini-4-hydrogen',
    openrouter_api_key: document.getElementById('cfg-or-key').value.trim(),
    enable_openrouter: document.getElementById('cfg-enable-or').checked,
    libraries: libraries,
    watch_paths: watchPaths,
    source_priority: sourcePriority.length > 0 ? sourcePriority : ['ja', 'en', 'fr', 'de', 'es', 'it', 'pt', 'ru', 'zh'],
    target_languages: targetLanguages.length > 0 ? targetLanguages : ['es-419']
  };

  const res = await fetch('/api/config', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload)
  });
  const data = await res.json();
  if (data.status === 'ok') {
    currentConfig = data.config;
    showToast('¡Configuración guardada y aplicada!');
    loadMediaCatalog();
  }
}

// Inicialización
window.addEventListener('DOMContentLoaded', () => {
  pollStatus();
  setInterval(pollStatus, 3000);
  loadMediaCatalog();
  loadProfilesCatalog();
});
