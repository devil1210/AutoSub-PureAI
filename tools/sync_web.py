#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sync_web.py - Servidor Web UI para Sincronización Automática de Audio Latino

Interfaz moderna inspirada en AutoSub para seleccionar series, capítulos de Nyaa
e inyectar pistas de audio de Telegram con ajuste de desfase milimétrico.
Compatible con Proxmox LXC (Alpine Linux) y Windows.
Soporta subida directa de archivos desde el navegador del PC a Proxmox.
"""

import os
import re
import sys
import json
import uuid
import time
import shutil
import threading
import subprocess
import urllib.parse
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

# Importar funciones de sincronización de sync_anime_dub
try:
    from tools.sync_anime_dub import (
        MKVMERGE_BIN, FFMPEG_BIN, check_prerequisites,
        get_mkv_tracks, get_audio_track_id, calculate_offset,
        sync_single_episode, extract_episode_number
    )
except ImportError:
    from sync_anime_dub import (
        MKVMERGE_BIN, FFMPEG_BIN, check_prerequisites,
        get_mkv_tracks, get_audio_track_id, calculate_offset,
        sync_single_episode, extract_episode_number
    )

try:
    from tools.telegram_notifier import send_episode_rich_notification
except ImportError:
    try:
        from telegram_notifier import send_episode_rich_notification
    except ImportError:
        def send_episode_rich_notification(file_path, payload=None):
            return False

PORT = 5056
CONFIG_FILE = Path(__file__).parent / "sync_web_config.json"

IS_WINDOWS = os.name == "nt"

# Detección inteligente de rutas por defecto
if not IS_WINDOWS and os.path.exists("/data/tvshows"):
    DEFAULT_ANIME = "/data/tvshows"
    DEFAULT_TELEGRAM = "/data/downloads"
elif os.path.exists("E:\\Descargas"):
    DEFAULT_ANIME = r"E:\Descargas"
    DEFAULT_TELEGRAM = r"E:\Descargas"
else:
    DEFAULT_ANIME = str(Path.home())
    DEFAULT_TELEGRAM = str(Path.home())

DEFAULT_CONFIG = {
    "anime_root_dir": DEFAULT_ANIME,
    "telegram_root_dir": DEFAULT_TELEGRAM,
    "trim_seconds": 360,
    "default_track": False,
    "track_name": "Español Latino",
    "delete_after_mux": True
}


def load_config():
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return {**DEFAULT_CONFIG, **json.load(f)}
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()


def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[!] Error guardando config: {e}")


HTML_PAGE = """<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AutoDub Studio Proxmox | Doblaje Latino Inteligente</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #0b0f19;
      --card-bg: rgba(17, 24, 39, 0.85);
      --card-hover: rgba(30, 41, 59, 0.7);
      --border: rgba(255, 255, 255, 0.08);
      --border-focus: #38bdf8;
      --text: #f1f5f9;
      --text-muted: #94a3b8;
      --primary: #38bdf8;
      --primary-hover: #0284c7;
      --success: #10b981;
      --warning: #f59e0b;
      --danger: #ef4444;
      --purple: #a855f7;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Inter', system-ui, -apple-system, sans-serif;
      background: radial-gradient(circle at 50% 0%, #1e1b4b 0%, var(--bg) 70%);
      color: var(--text);
      min-height: 100vh;
      padding: 24px;
    }
    .container { max-width: 1350px; margin: 0 auto; }
    
    header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 24px;
      padding-bottom: 20px;
      border-bottom: 1px solid var(--border);
    }
    .logo-box { display: flex; align-items: center; gap: 14px; }
    .logo-icon {
      font-size: 32px;
      background: linear-gradient(135deg, #38bdf8, #a855f7);
      width: 52px; height: 52px;
      display: flex; align-items: center; justify-content: center;
      border-radius: 14px;
      box-shadow: 0 4px 20px rgba(56, 189, 248, 0.35);
    }
    .logo-title h1 { font-size: 22px; font-weight: 800; letter-spacing: -0.5px; }
    .logo-title p { font-size: 13px; color: var(--text-muted); margin-top: 2px; }
    
    .status-badge {
      display: inline-flex; align-items: center; gap: 8px;
      background: rgba(16, 185, 129, 0.15); border: 1px solid rgba(16, 185, 129, 0.3);
      color: #34d399; padding: 6px 14px; border-radius: 20px; font-size: 12px; font-weight: 600;
    }
    .status-dot { width: 8px; height: 8px; border-radius: 50%; background: #34d399; box-shadow: 0 0 10px #34d399; }

    .card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 20px;
      margin-bottom: 20px;
      backdrop-filter: blur(12px);
    }
    .card-title {
      font-size: 16px; font-weight: 700; display: flex; align-items: center;
      gap: 10px; margin-bottom: 16px; color: #e2e8f0;
    }
    
    .btn {
      display: inline-flex; align-items: center; gap: 6px;
      padding: 8px 16px; border-radius: 8px; font-size: 13px; font-weight: 600;
      border: none; cursor: pointer; transition: all 0.2s ease;
      white-space: nowrap;
    }
    .btn-primary { background: linear-gradient(135deg, #0284c7, #38bdf8); color: white; }
    .btn-primary:hover { filter: brightness(1.15); transform: translateY(-1px); }
    .btn-secondary { background: rgba(255, 255, 255, 0.08); color: var(--text); border: 1px solid var(--border); }
    .btn-secondary:hover { background: rgba(255, 255, 255, 0.15); }
    .btn-success { background: linear-gradient(135deg, #059669, #10b981); color: white; }
    .btn-purple { background: linear-gradient(135deg, #7c3aed, #a855f7); color: white; }
    .btn-purple:hover { filter: brightness(1.15); transform: translateY(-1px); }
    .btn-sm { padding: 6px 12px; font-size: 12px; }

    select, input[type="text"] {
      background: #0f172a; border: 1px solid var(--border); color: var(--text);
      padding: 9px 14px; border-radius: 8px; font-size: 13px; width: 100%;
      outline: none; transition: border-color 0.2s;
    }
    select:focus, input[type="text"]:focus { border-color: var(--border-focus); }

    .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }

    .controls-row { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
    .controls-row select { flex: 1; min-width: 280px; }

    .table-container { overflow-x: auto; margin-top: 10px; border-radius: 10px; border: 1px solid var(--border); }
    table { width: 100%; border-collapse: collapse; text-align: left; font-size: 13px; }
    th { background: rgba(15, 23, 42, 0.8); padding: 12px 14px; color: var(--text-muted); font-weight: 600; border-bottom: 1px solid var(--border); }
    td { padding: 12px 14px; border-bottom: 1px solid rgba(255, 255, 255, 0.04); vertical-align: middle; }
    tr:hover td { background: var(--card-hover); }

    .badge {
      display: inline-flex; align-items: center; padding: 3px 8px;
      border-radius: 6px; font-size: 11px; font-weight: 600; text-transform: uppercase;
    }
    .badge-green { background: rgba(16, 185, 129, 0.15); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.3); }
    .badge-blue { background: rgba(56, 189, 248, 0.15); color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.3); }
    .badge-gray { background: rgba(148, 163, 184, 0.1); color: #94a3b8; border: 1px solid rgba(148, 163, 184, 0.2); }
    .badge-purple { background: rgba(168, 85, 247, 0.15); color: #c084fc; border: 1px solid rgba(168, 85, 247, 0.3); }

    .dub-input-group { display: flex; gap: 6px; align-items: center; width: 100%; min-width: 380px; }
    .dub-input-group input { font-size: 12px; padding: 7px 10px; font-family: monospace; }

    /* Modales */
    .modal-overlay {
      display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%;
      background: rgba(0, 0, 0, 0.75); backdrop-filter: blur(8px);
      z-index: 9999; justify-content: center; align-items: center;
    }
    .modal-box {
      background: #111827; border: 1px solid var(--border);
      border-radius: 16px; padding: 24px; width: 620px; max-width: 95%;
      box-shadow: 0 20px 40px rgba(0, 0, 0, 0.6);
    }
    .progress-bar-wrap {
      width: 100%; height: 10px; background: #1e293b;
      border-radius: 5px; overflow: hidden; margin: 16px 0;
    }
    .progress-bar-fill {
      width: 0%; height: 100%; background: linear-gradient(90deg, #38bdf8, #a855f7);
      transition: width 0.2s ease;
    }
    .progress-bar-indeterminate {
      width: 100%; height: 100%; background: linear-gradient(90deg, #38bdf8, #a855f7);
      animation: indeterminate 1.8s infinite linear;
      transform-origin: 0% 50%;
    }
    @keyframes indeterminate {
      0% { transform: translateX(0) scaleX(0.1); }
      50% { transform: translateX(50%) scaleX(0.5); }
      100% { transform: translateX(100%) scaleX(0.1); }
    }
    .log-box {
      background: #090d16; border: 1px solid rgba(255,255,255,0.06);
      border-radius: 8px; padding: 12px; font-family: monospace; font-size: 12px;
      color: #94a3b8; max-height: 180px; overflow-y: auto; white-space: pre-wrap;
    }
    .fs-list {
      max-height: 350px; overflow-y: auto; background: #090d16;
      border: 1px solid var(--border); border-radius: 8px; margin: 12px 0;
    }
    .fs-item {
      padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.04);
      cursor: pointer; display: flex; align-items: center; gap: 10px; font-size: 13px;
    }
    .fs-item:hover { background: rgba(56, 189, 248, 0.1); }
  </style>
</head>
<body>

<div class="container">
  <header>
    <div class="logo-box">
      <div class="logo-icon">🎙️</div>
      <div class="logo-title">
        <h1>AutoDub Studio Proxmox</h1>
        <p>Alineación acústica de precisión e inyección de audio latino en tu disco de 8TB</p>
      </div>
    </div>
    <div style="display:flex; gap:10px; align-items:center;">
      <a href="http://192.168.1.125:5055" target="_blank" class="btn btn-secondary btn-sm" style="text-decoration:none;">
        📺 Abrir AutoSub (Subtítulos)
      </a>
      <div class="status-badge">
        <div class="status-dot"></div>
        <span>LXC 125 Activo</span>
      </div>
    </div>
  </header>

  <!-- Card: Configuración de Carpetas en el Disco de 8TB -->
  <div class="card">
    <div class="card-title">💾 Ubicación de Medios (Disco 8TB en Proxmox)</div>
    <div class="grid-2">
      <div>
        <label style="font-size:12px; font-weight:600; color:var(--text-muted); display:block; margin-bottom:6px;">
          Carpeta de Animes / Series (1080p Nyaa):
        </label>
        <div style="display:flex; gap:8px;">
          <input type="text" id="cfg-anime-dir" placeholder="/data/tvshows">
          <button class="btn btn-secondary btn-sm" onclick="openFsBrowser('anime')">📁 Explorar</button>
        </div>
      </div>
      <div>
        <label style="font-size:12px; font-weight:600; color:var(--text-muted); display:block; margin-bottom:6px;">
          Carpeta de Descargas en Proxmox (Opcional):
        </label>
        <div style="display:flex; gap:8px;">
          <input type="text" id="cfg-dub-dir" placeholder="/data/downloads">
          <button class="btn btn-secondary btn-sm" onclick="openFsBrowser('dub')">📁 Explorar</button>
        </div>
      </div>
    </div>
    <div style="margin-top:14px; display:flex; justify-content:space-between; align-items:center;">
      <label style="font-size:12px; color:var(--text-muted); display:inline-flex; align-items:center; gap:6px; cursor:pointer;">
        <input type="checkbox" id="cfg-delete-after" checked>
        Limpiar archivo subido temporal de Telegram tras muxear con éxito
      </label>
      <button class="btn btn-primary btn-sm" onclick="saveAndReload()">💾 Guardar y Cargar Catálogo</button>
    </div>
  </div>

  <!-- Card: Selector de Serie y Capítulos -->
  <div class="card">
    <div class="card-title">📺 Selección de Serie y Episodios</div>
    <div class="controls-row">
      <select id="series-select" onchange="onSeriesSelected()">
        <option value="">-- Seleccionar Serie --</option>
      </select>
      <button class="btn btn-secondary btn-sm" onclick="loadSeriesCatalog()">🔄 Refrescar Series</button>
    </div>

    <div class="table-container" style="margin-top:16px;">
      <table>
        <thead>
          <tr>
            <th style="width:70px;">Cap</th>
            <th>Archivo de Nyaa (1080p)</th>
            <th style="width:230px;">Pistas Detectadas</th>
            <th>Archivo con Audio Latino (Telegram / PC)</th>
            <th style="width:140px; text-align:right;">Acción</th>
          </tr>
        </thead>
        <tbody id="episodes-tbody">
          <tr><td colspan="5" style="text-align:center; color:var(--text-muted); padding:24px;">Selecciona una serie arriba para listar los episodios.</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</div>

<!-- Modal: Explorador de Archivos en Proxmox -->
<div id="fs-modal" class="modal-overlay">
  <div class="modal-box">
    <h3 style="font-size:16px; margin-bottom:6px;" id="fs-modal-title">📁 Explorador de Disco (Proxmox)</h3>
    <div style="font-size:12px; color:var(--primary); font-family:monospace; margin-bottom:8px;" id="fs-cur-path">/data</div>
    
    <div class="fs-list" id="fs-items-container">
      <div style="padding:16px; text-align:center; color:var(--text-muted);">Cargando archivos...</div>
    </div>

    <div style="display:flex; justify-content:space-between; align-items:center; margin-top:12px;">
      <button class="btn btn-secondary btn-sm" onclick="closeFsModal()">Cancelar</button>
      <button class="btn btn-success btn-sm" id="fs-select-dir-btn" onclick="selectCurrentFsDir()">Seleccionar Esta Carpeta</button>
    </div>
  </div>
</div>

<!-- Modal: Progreso y Subida -->
<div id="sync-modal" class="modal-overlay">
  <div class="modal-box">
    <h3 id="modal-title" style="margin-bottom:8px; font-size:18px;">⚡ Sincronizando Audio...</h3>
    <p id="modal-desc" style="font-size:13px; color:var(--text-muted); margin-bottom:12px;">Iniciando proceso...</p>
    
    <div class="progress-bar-wrap">
      <div id="modal-progress-bar" class="progress-bar-indeterminate"></div>
    </div>

    <div class="log-box" id="modal-logs">Iniciando...</div>

    <div style="margin-top:18px; text-align:right;">
      <button class="btn btn-secondary btn-sm" id="modal-close-btn" style="display:none;" onclick="closeModal()">Cerrar</button>
    </div>
  </div>
</div>

<script>
let currentConfig = {};
let currentEpisodes = [];
let fsTargetInput = null;
let currentFsBrowsePath = '/data';

window.addEventListener('DOMContentLoaded', async () => {
  await loadConfig();
  await loadSeriesCatalog();
});

async function loadConfig() {
  const res = await fetch('/api/config');
  currentConfig = await res.json();
  document.getElementById('cfg-anime-dir').value = currentConfig.anime_root_dir || '/data/tvshows';
  document.getElementById('cfg-dub-dir').value = currentConfig.telegram_root_dir || '/data/downloads';
  document.getElementById('cfg-delete-after').checked = currentConfig.delete_after_mux !== false;
}

async function saveAndReload() {
  const animeDir = document.getElementById('cfg-anime-dir').value.trim();
  const dubDir = document.getElementById('cfg-dub-dir').value.trim();
  const delAfter = document.getElementById('cfg-delete-after').checked;
  await fetch('/api/config', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ anime_root_dir: animeDir, telegram_root_dir: dubDir, delete_after_mux: delAfter })
  });
  await loadSeriesCatalog();
}

async function loadSeriesCatalog() {
  const rootDir = document.getElementById('cfg-anime-dir').value.trim();
  const sel = document.getElementById('series-select');
  sel.innerHTML = '<option value="">Cargando series...</option>';
  
  try {
    const res = await fetch('/api/series?root=' + encodeURIComponent(rootDir));
    const data = await res.json();
    const series = data.series || [];
    
    if (series.length === 0) {
      sel.innerHTML = '<option value="">No se encontraron carpetas o series en esta ruta</option>';
      return;
    }
    
    sel.innerHTML = '<option value="">-- Seleccionar Serie (' + series.length + ' encontradas) --</option>' +
      series.map(s => `<option value="${s.path}">${s.name}</option>`).join('');
  } catch (err) {
    sel.innerHTML = '<option value="">Error cargando catálogo</option>';
  }
}

async function onSeriesSelected() {
  const sPath = document.getElementById('series-select').value;
  const dubDir = document.getElementById('cfg-dub-dir').value.trim();
  const tbody = document.getElementById('episodes-tbody');
  
  if (!sPath) {
    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; color:var(--text-muted); padding:24px;">Selecciona una serie arriba para listar los episodios.</td></tr>';
    return;
  }
  tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; color:var(--text-muted); padding:24px;">Analizando pistas de los episodios con MKVToolNix...</td></tr>';

  try {
    const res = await fetch(`/api/episodes?path=${encodeURIComponent(sPath)}&dub_dir=${encodeURIComponent(dubDir)}`);
    const data = await res.json();
    currentEpisodes = data.episodes || [];

    if (currentEpisodes.length === 0) {
      tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; color:var(--text-muted); padding:24px;">No se encontraron videos en esta carpeta.</td></tr>';
      return;
    }

    renderEpisodesTable();
  } catch (err) {
    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; color:var(--danger); padding:24px;">Error inspeccionando episodios.</td></tr>';
  }
}

function renderEpisodesTable() {
  const tbody = document.getElementById('episodes-tbody');
  tbody.innerHTML = currentEpisodes.map((ep, idx) => {
    const epBadge = ep.ep_num ? `<span class="badge badge-purple">#${ep.ep_num}</span>` : `<span class="badge badge-gray">-</span>`;
    
    const jpBadge = ep.has_jp_audio 
      ? '<span class="badge badge-blue" title="Audio Japonés presente">🇯🇵 Audio JP</span>' 
      : '<span class="badge badge-gray">Sin Audio JP</span>';
      
    const subBadge = ep.has_es_sub 
      ? '<span class="badge badge-green" title="Subtítulos en español presentes">💬 Sub ES</span>' 
      : '<span class="badge badge-gray">Sin Sub ES</span>';

    const latBadge = ep.has_latino_audio
      ? '<span class="badge badge-green" title="Pista de audio latino ya sincronizada">🎙️ Latino OK</span>'
      : '<span class="badge badge-gray">Falta Latino</span>';

    return `
      <tr>
        <td>${epBadge}</td>
        <td>
          <strong style="color:#f8fafc;">${ep.name}</strong>
          <div style="font-size:11px; color:var(--text-muted); margin-top:2px;">${ep.size_mb} MB</div>
        </td>
        <td>
          <div style="display:flex; gap:6px; flex-wrap:wrap;">
            ${jpBadge}
            ${subBadge}
            ${latBadge}
          </div>
        </td>
        <td>
          <div class="dub-input-group">
            <input type="text" id="dub-path-${idx}" placeholder="Ruta en Proxmox o sube desde tu PC..." value="${ep.suggested_dub || ''}">
            <button class="btn btn-secondary btn-sm" onclick="pickDubFileForEp(${idx})" title="Explorar en disco de Proxmox">📁 Servidor</button>
            <button class="btn btn-purple btn-sm" onclick="triggerPcUpload(${idx})" title="Subir archivo descargado en tu PC de Telegram">⬆️ Subir de mi PC</button>
            <input type="file" id="file-input-${idx}" style="display:none;" onchange="handlePcFileSelected(${idx})" accept="video/*,audio/*,.mkv,.mp4,.m4v,.webm,.aac,.m4a">
          </div>
        </td>
        <td style="text-align:right;">
          <button class="btn btn-primary btn-sm" onclick="startSyncJob(${idx})">
            ⚡ Sincronizar
          </button>
        </td>
      </tr>
    `;
  }).join('');
}

// Disparar selector de archivos de Windows/PC
function triggerPcUpload(idx) {
  const fileInput = document.getElementById(`file-input-${idx}`);
  fileInput.click();
}

async function handlePcFileSelected(idx) {
  const fileInput = document.getElementById(`file-input-${idx}`);
  if (!fileInput.files || fileInput.files.length === 0) return;
  const file = fileInput.files[0];
  const ep = currentEpisodes[idx];

  const modal = document.getElementById('sync-modal');
  const pBar = document.getElementById('modal-progress-bar');
  const logs = document.getElementById('modal-logs');
  const title = document.getElementById('modal-title');
  const desc = document.getElementById('modal-desc');
  const closeBtn = document.getElementById('modal-close-btn');

  modal.style.display = 'flex';
  closeBtn.style.display = 'none';
  pBar.className = 'progress-bar-fill';
  pBar.style.width = '0%';
  title.innerText = `⬆️ Subiendo ${file.name} a Proxmox`;
  desc.innerText = `Subiendo archivo desde tu PC (${(file.size / (1024*1024)).toFixed(1)} MB)...`;
  logs.innerText = `Iniciando transferencia local PC -> Proxmox...\n`;

  // Subir vía XMLHttpRequest con barra de progreso
  const xhr = new XMLHttpRequest();
  xhr.open('POST', `/api/upload?filename=${encodeURIComponent(file.name)}`, true);

  xhr.upload.onprogress = (e) => {
    if (e.lengthComputable) {
      const pct = Math.round((e.loaded / e.total) * 100);
      pBar.style.width = pct + '%';
      const loadedMB = (e.loaded / (1024*1024)).toFixed(1);
      const totalMB = (e.total / (1024*1024)).toFixed(1);
      desc.innerText = `Subiendo: ${pct}% (${loadedMB} / ${totalMB} MB)`;
    }
  };

  xhr.onload = async () => {
    if (xhr.status === 200) {
      const data = JSON.parse(xhr.responseText);
      const uploadedPath = data.path;
      document.getElementById(`dub-path-${idx}`).value = uploadedPath;

      pBar.className = 'progress-bar-indeterminate';
      pBar.style.width = '100%';
      title.innerText = `⚡ Sincronizando Audio en Proxmox...`;
      desc.innerText = `Calculando desfase acústico de ${file.name}...`;
      logs.innerText += `[OK] Subida completada en ${uploadedPath}\nIniciando correlación FFT...\n`;

      // Proceder de inmediato a la sincronización
      await executeSync(ep, uploadedPath, data.is_uploaded);
    } else {
      title.innerText = '❌ Error al Subir Archivo';
      desc.innerText = 'No se pudo transferir el archivo a Proxmox.';
      logs.innerText += `\n[ERROR] Código HTTP ${xhr.status}: ${xhr.responseText}\n`;
      closeBtn.style.display = 'inline-block';
    }
  };

  xhr.onerror = () => {
    title.innerText = '❌ Error de Conexión';
    desc.innerText = 'Fallo en la conexión de red local con Proxmox.';
    closeBtn.style.display = 'inline-block';
  };

  xhr.send(file);
}

// Explorador de archivos del servidor Proxmox
function openFsBrowser(type) {
  fsTargetInput = (type === 'anime') ? 'cfg-anime-dir' : 'cfg-dub-dir';
  document.getElementById('fs-select-dir-btn').style.display = 'inline-block';
  document.getElementById('fs-modal-title').innerText = '📁 Seleccionar Carpeta en Proxmox';
  const startPath = document.getElementById(fsTargetInput).value.trim() || '/data';
  loadFsPath(startPath);
  document.getElementById('fs-modal').style.display = 'flex';
}

function pickDubFileForEp(idx) {
  fsTargetInput = `dub-path-${idx}`;
  document.getElementById('fs-select-dir-btn').style.display = 'none';
  document.getElementById('fs-modal-title').innerText = '🎵 Seleccionar Archivo de Doblaje en Proxmox';
  const startPath = document.getElementById('cfg-dub-dir').value.trim() || '/data/downloads';
  loadFsPath(startPath);
  document.getElementById('fs-modal').style.display = 'flex';
}

async function loadFsPath(dirPath) {
  currentFsBrowsePath = dirPath;
  document.getElementById('fs-cur-path').innerText = dirPath;
  const container = document.getElementById('fs-items-container');
  container.innerHTML = '<div style="padding:16px; text-align:center; color:var(--text-muted);">Cargando...</div>';

  try {
    const res = await fetch('/api/browse_fs?path=' + encodeURIComponent(dirPath));
    const data = await res.json();
    const items = data.items || [];
    
    let html = '';
    if (data.parent) {
      html += `<div class="fs-item" onclick="loadFsPath('${data.parent.replace(/'/g, "\\'")}')">
        <span style="font-size:16px;">⬆️</span> <strong>.. (Carpeta Superior)</strong>
      </div>`;
    }

    html += items.map(item => {
      const icon = item.is_dir ? '📁' : (item.name.endsWith('.mkv') || item.name.endsWith('.mp4') ? '🎬' : '📄');
      const action = item.is_dir 
        ? `loadFsPath('${item.path.replace(/'/g, "\\'")}')`
        : `selectFsItem('${item.path.replace(/'/g, "\\'")}')`;
      return `
        <div class="fs-item" onclick="${action}">
          <span style="font-size:16px;">${icon}</span>
          <div style="flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">
            <strong style="color:${item.is_dir ? '#38bdf8' : '#f1f5f9'};">${item.name}</strong>
            ${item.size ? `<span style="font-size:11px; color:#64748b; margin-left:8px;">(${item.size})</span>` : ''}
          </div>
        </div>
      `;
    }).join('');

    container.innerHTML = html || '<div style="padding:16px; text-align:center; color:var(--text-muted);">Carpeta vacía</div>';
  } catch (err) {
    container.innerHTML = `<div style="padding:16px; text-align:center; color:var(--danger);">${err.message}</div>`;
  }
}

function selectFsItem(filePath) {
  if (fsTargetInput) {
    document.getElementById(fsTargetInput).value = filePath;
  }
  closeFsModal();
}

function selectCurrentFsDir() {
  if (fsTargetInput) {
    document.getElementById(fsTargetInput).value = currentFsBrowsePath;
  }
  closeFsModal();
}

function closeFsModal() {
  document.getElementById('fs-modal').style.display = 'none';
}

async function startSyncJob(idx) {
  const ep = currentEpisodes[idx];
  const dubInput = document.getElementById(`dub-path-${idx}`);
  const dubPath = dubInput.value.trim();

  if (!dubPath) {
    alert('Por favor escribe la ruta o pulsa "⬆️ Subir de mi PC" para cargar el archivo descargado.');
    return;
  }

  const modal = document.getElementById('sync-modal');
  const pBar = document.getElementById('modal-progress-bar');
  const logs = document.getElementById('modal-logs');
  const title = document.getElementById('modal-title');
  const desc = document.getElementById('modal-desc');
  const closeBtn = document.getElementById('modal-close-btn');

  modal.style.display = 'flex';
  closeBtn.style.display = 'none';
  pBar.className = 'progress-bar-indeterminate';
  title.innerText = `⚡ Sincronizando ${ep.name}`;
  desc.innerText = 'Calculando correlación acústica en Proxmox con FFmpeg y mkvmerge...';
  logs.innerText = `Iniciando proceso...\nBase: ${ep.name}\nDoblaje: ${dubPath}\n`;

  await executeSync(ep, dubPath, false);
}

async function executeSync(ep, dubPath, isUploaded) {
  const modal = document.getElementById('sync-modal');
  const logs = document.getElementById('modal-logs');
  const title = document.getElementById('modal-title');
  const desc = document.getElementById('modal-desc');
  const closeBtn = document.getElementById('modal-close-btn');
  const delAfter = document.getElementById('cfg-delete-after').checked;

  try {
    const res = await fetch('/api/sync', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        nyaa_file: ep.path,
        dub_file: dubPath,
        delete_after: isUploaded && delAfter
      })
    });
    const result = await res.json();
    
    if (result.status === 'ok') {
      title.innerText = '✅ ¡Sincronización Exitosa!';
      desc.innerText = `Desfase: ${result.offset_sec > 0 ? '+' : ''}${result.offset_sec}s (${result.delay_ms} ms) | Confianza: ${result.score.toFixed(2)}`;
      logs.innerText += `\n[OK] Desfase detectado: ${result.delay_ms} ms\n[OK] Pista de audio inyectada con éxito sin perder calidad.\nArchivo creado en el disco de 8TB:\n-> ${result.output_file}\n`;
      if (result.deleted_temp) {
        logs.innerText += `[OK] Archivo temporal de subida eliminado para ahorrar espacio.\n`;
      }
      closeBtn.style.display = 'inline-block';
      setTimeout(onSeriesSelected, 1500);
    } else {
      title.innerText = '❌ Error en la Sincronización';
      desc.innerText = result.message || 'Ocurrió un error inesperado.';
      logs.innerText += `\n[ERROR] ${result.message}\n`;
      closeBtn.style.display = 'inline-block';
    }
  } catch (err) {
    title.innerText = '❌ Error de Conexión';
    desc.innerText = err.message;
    logs.innerText += `\n[ERROR] ${err.message}\n`;
    closeBtn.style.display = 'inline-block';
  }
}

function closeModal() {
  document.getElementById('sync-modal').style.display = 'none';
}
</script>

</body>
</html>
"""


class DubSyncWebHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_HEAD(self):
        self.do_GET()

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        clean_path = parsed.path

        if clean_path in ("/", "/index.html"):
            html_bytes = HTML_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html_bytes)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(html_bytes)
            return

        if clean_path == "/api/config":
            self.send_json(load_config())
            return

        if clean_path == "/api/browse_fs":
            qs = urllib.parse.parse_qs(parsed.query)
            target = qs.get("path", ["/data"])[0].strip() or "/data"
            p = Path(target)
            if not p.exists():
                p = Path("/data") if Path("/data").exists() else Path.home()

            items = []
            parent = str(p.parent) if p.parent != p else None

            try:
                for entry in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                    if entry.name.startswith("."):
                        continue
                    if entry.is_dir():
                        items.append({"name": entry.name, "path": str(entry), "is_dir": True})
                    elif entry.suffix.lower() in (".mkv", ".mp4", ".m4v", ".webm", ".aac", ".m4a"):
                        sz = f"{round(entry.stat().st_size / (1024*1024), 1)} MB"
                        items.append({"name": entry.name, "path": str(entry), "is_dir": False, "size": sz})
            except Exception as e:
                self.send_json({"error": str(e)}, status=500)
                return

            self.send_json({"path": str(p), "parent": parent, "items": items})
            return

        if clean_path == "/api/series":
            qs = urllib.parse.parse_qs(parsed.query)
            root_dir = qs.get("root", [""])[0].strip() or load_config().get("anime_root_dir", "")
            series_list = []
            if os.path.exists(root_dir):
                for entry in sorted(os.listdir(root_dir)):
                    full_p = os.path.join(root_dir, entry)
                    if os.path.isdir(full_p) and not entry.startswith("."):
                        series_list.append({"name": entry, "path": full_p})
                    elif os.path.isfile(full_p) and entry.lower().endswith((".mkv", ".mp4")):
                        series_list.append({"name": entry, "path": full_p, "is_file": True})
            self.send_json({"series": series_list})
            return

        if clean_path == "/api/episodes":
            qs = urllib.parse.parse_qs(parsed.query)
            target_path = qs.get("path", [""])[0].strip()
            dub_dir = qs.get("dub_dir", [""])[0].strip() or load_config().get("telegram_root_dir", "")
            episodes = []

            # Escanear dub_dir para sugerencias automáticas de capítulos
            dub_map = {}
            if dub_dir and os.path.exists(dub_dir):
                exts = {".mkv", ".mp4", ".m4v", ".webm", ".aac", ".m4a"}
                for root, _, files in os.walk(dub_dir):
                    for f in files:
                        if Path(f).suffix.lower() in exts:
                            ep_num = extract_episode_number(f)
                            if ep_num is not None and ep_num not in dub_map:
                                dub_map[ep_num] = os.path.join(root, f)

            if target_path and os.path.exists(target_path):
                files_to_check = []
                if os.path.isfile(target_path):
                    files_to_check.append(Path(target_path))
                else:
                    exts = {".mkv", ".mp4", ".m4v"}
                    for root, _, files in os.walk(target_path):
                        for f in sorted(files):
                            if Path(f).suffix.lower() in exts:
                                files_to_check.append(Path(root) / f)

                for f in files_to_check:
                    try:
                        tracks_info = get_mkv_tracks(str(f))
                        tracks = tracks_info.get("tracks", [])
                    except Exception:
                        tracks = []

                    has_jp_audio = False
                    has_es_sub = False
                    has_latino_audio = False

                    for t in tracks:
                        t_type = t.get("type", "")
                        props = t.get("properties", {})
                        lang = (props.get("language", "") or props.get("language_ietf", "")).lower()
                        name = (props.get("track_name", "")).lower()

                        if t_type == "audio":
                            if lang in ("ja", "jpn", "japanese"):
                                has_jp_audio = True
                            if "lat" in name or "latino" in name or lang in ("es-419", "spa", "es"):
                                if "lat" in name or "latino" in name or lang == "es-419":
                                    has_latino_audio = True
                        elif t_type == "subtitles":
                            if lang in ("es", "spa", "es-419") or "español" in name or "spanish" in name:
                                has_es_sub = True

                    sz_mb = round(f.stat().st_size / (1024 * 1024), 1)
                    ep_num = extract_episode_number(f.name)
                    suggested_dub = dub_map.get(ep_num, "")

                    episodes.append({
                        "name": f.name,
                        "path": str(f.resolve()),
                        "size_mb": sz_mb,
                        "ep_num": ep_num,
                        "has_jp_audio": has_jp_audio,
                        "has_es_sub": has_es_sub,
                        "has_latino_audio": has_latino_audio,
                        "suggested_dub": suggested_dub
                    })

            self.send_json({"episodes": episodes})
            return

        self.send_error(404, "Not Found")

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        clean_path = parsed.path

        # Endpoint de subida directa de archivos desde el navegador (PC -> Proxmox)
        if clean_path == "/api/upload":
            qs = urllib.parse.parse_qs(parsed.query)
            filename = qs.get("filename", ["telegram_temp.mp4"])[0]
            safe_name = os.path.basename(filename)

            upload_dir = Path("/data/downloads/telegram_uploads") if Path("/data/downloads").exists() else Path("./telegram_uploads")
            upload_dir.mkdir(parents=True, exist_ok=True)
            dest = upload_dir / safe_name

            length = int(self.headers.get("Content-Length", 0))
            remaining = length

            try:
                with open(dest, "wb") as f:
                    while remaining > 0:
                        chunk_size = min(remaining, 64 * 1024)
                        chunk = self.rfile.read(chunk_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        remaining -= len(chunk)

                self.send_json({
                    "status": "ok",
                    "path": str(dest.resolve()),
                    "name": safe_name,
                    "is_uploaded": True
                })
            except Exception as e:
                self.send_json({"status": "error", "message": f"Error escribiendo archivo subido: {e}"}, status=500)
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
        try:
            payload = json.loads(body)
        except Exception:
            payload = {}

        if clean_path == "/api/config":
            cfg = load_config()
            cfg.update(payload)
            save_config(cfg)
            self.send_json({"status": "ok", "config": cfg})
            return

        if clean_path == "/api/sync":
            nyaa_file = payload.get("nyaa_file", "").strip()
            dub_file = payload.get("dub_file", "").strip()
            trim = payload.get("trim", 360)
            delete_after = payload.get("delete_after", False)

            if not nyaa_file or not dub_file:
                self.send_json({"status": "error", "message": "Faltan rutas de archivo"}, status=400)
                return

            p = Path(nyaa_file)
            out_file = str(p.parent / f"{p.stem} [Dual Audio].mkv")

            try:
                # 1. Pista de audio
                dub_audio_id = get_audio_track_id(dub_file, prefer_lang="spa")

                # 2. Desfase
                offset_sec, score = calculate_offset(nyaa_file, dub_file, trim=trim)
                delay_ms = int(round(offset_sec * 1000))

                # 3. Muxing
                cmd = [
                    MKVMERGE_BIN,
                    "-o", out_file,
                    nyaa_file,
                    "--no-video",
                    "--no-subtitles",
                    "--no-buttons",
                    "--no-track-tags",
                    "--no-global-tags",
                    "--audio-tracks", str(dub_audio_id),
                    "--sync", f"{dub_audio_id}:{delay_ms}",
                    "--language", f"{dub_audio_id}:spa",
                    "--track-name", f"{dub_audio_id}:Español Latino",
                    dub_file
                ]
                run_res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
                if run_res.returncode not in (0, 1):
                    self.send_json({"status": "error", "message": f"mkvmerge falló:\n{run_res.stderr}"}, status=500)
                    return

                deleted_temp = False
                if delete_after and os.path.exists(dub_file):
                    try:
                        os.remove(dub_file)
                        deleted_temp = True
                    except Exception:
                        pass

                self.send_json({
                    "status": "ok",
                    "offset_sec": round(offset_sec, 3),
                    "delay_ms": delay_ms,
                    "score": round(score, 2),
                    "output_file": out_file,
                    "deleted_temp": deleted_temp
                })
            except Exception as e:
                self.send_json({"status": "error", "message": str(e)}, status=500)
            return

        if clean_path == "/api/sonarr_webhook":
            if payload.get("eventType") == "Test":
                self.send_json({"status": "ok", "message": "AutoDub webhook test successful"})
                return

            # Responder de inmediato a Sonarr (200 OK) para que libere la cola y complete el hardlink sin demoras.
            # La sincronización acústica pesada se ejecuta en un hilo en segundo plano.
            threading.Thread(target=handle_sonarr_webhook, args=(payload,), daemon=True).start()
            self.send_json({"status": "queued", "message": "AutoDub sync job queued in background"})
            return

        self.send_error(404, "Not Found")


def handle_sonarr_webhook(payload: dict) -> dict:
    """
    Procesa eventos de Sonarr (On File Import / On File Upgrade) en segundo plano.
    Asegura aislamiento total por serie, temporada y capítulo, evitando mezclar series distintas.
    """
    event_type = payload.get("eventType", "")
    print(f"\n[Sonarr Webhook] Recibido evento: '{event_type}'")

    if event_type == "Test":
        return {"status": "ok", "message": "AutoDub webhook test successful"}

    valid_events = ("Download", "Upgrade", "EpisodeFileImport", "FileImport", "FileUpgrade", "Import")
    if event_type not in valid_events:
        print(f"[Sonarr Webhook] Evento '{event_type}' omitido (no es importación de archivo).")
        return {"status": "ignored", "reason": f"Event type '{event_type}' not handled"}

    episode_file = payload.get("episodeFile", {})
    new_file_path = episode_file.get("path")
    if not new_file_path or not os.path.exists(new_file_path):
        return {"status": "ignored", "reason": "No episode file path found on disk"}

    new_size_mb = os.path.getsize(new_file_path) / (1024 * 1024)
    print(f"[Sonarr Webhook] Nuevo archivo: {new_file_path} ({round(new_size_mb, 1)} MB)")

    # Metadatos de la serie para aislamiento estricto
    series_info = payload.get("series", {})
    series_title = series_info.get("title", "").strip().lower()
    series_path = series_info.get("path", "").strip().lower()
    series_folder_name = os.path.basename(series_path) if series_path else series_title

    # Palabras clave de la serie para búsqueda en /downloads (palabras de >= 4 caracteres)
    title_words = [w.lower() for w in re.findall(r'[a-zA-Z0-9]+', series_title) if len(w) >= 4]

    episodes = payload.get("episodes", [])
    season_num = episodes[0].get("seasonNumber") if episodes else None
    db_ep_num = episodes[0].get("episodeNumber") if episodes else None
    file_ep_num = extract_episode_number(os.path.basename(new_file_path))
    target_eps = {ep for ep in (db_ep_num, file_ep_num) if ep is not None}

    def is_candidate_matching_series(cand_path):
        p_str = str(cand_path).lower()
        # 1. Si está dentro de la carpeta de la serie en tvshows
        if series_path and series_path in p_str:
            return True
        # 2. Si está en .recycle bajo la subcarpeta de la serie
        if ".recycle" in p_str and (series_folder_name in p_str or any(w in p_str for w in title_words)):
            return True
        # 3. Si está en /data/downloads, debe coincidir con alguna palabra clave de la serie
        if "/downloads" in p_str:
            fname = os.path.basename(p_str).lower()
            if any(w in fname for w in title_words):
                return True
        return False

    def is_candidate_matching_season(cand_path):
        if season_num is None:
            return True
        p_str = str(cand_path).lower()
        s_match = re.search(r'season\s*(\d+)', p_str)
        if s_match and int(s_match.group(1)) != season_num:
            return False
        s_match2 = re.search(r's(\d{1,2})e\d+', os.path.basename(cand_path), re.IGNORECASE)
        if s_match2 and int(s_match2.group(1)) != season_num:
            return False
        return True

    def find_latino_audio(file_path):
        try:
            info = get_mkv_tracks(file_path)
            for t in info.get("tracks", []):
                if t.get("type") == "audio":
                    props = t.get("properties", {})
                    lang = (props.get("language", "") or props.get("language_ietf", "")).lower()
                    name = (props.get("track_name", "")).lower()
                    if "lat" in name or "latino" in name or lang in ("es-419", "spa", "es"):
                        t_name = props.get("track_name") or "Español Latino"
                        return t["id"], t_name
        except Exception:
            pass
        return None, None

    new_latino_id, new_latino_name = find_latino_audio(new_file_path)

    # Buscar candidatos filtrando estrictamente por la misma serie y temporada
    candidates = []

    # A) deletedFiles enviados por Sonarr
    for df in payload.get("deletedFiles", []):
        p = df.get("path")
        if p and os.path.exists(p) and is_candidate_matching_series(p) and is_candidate_matching_season(p):
            candidates.append(p)

    # B) Papelera de reciclaje común
    for r_dir in (Path("/data/downloads/.recycle"), Path("/data/.recycle")):
        if r_dir.exists():
            for root, _, files in os.walk(r_dir):
                for f in files:
                    if f.lower().endswith((".mkv", ".mp4")):
                        full_p = os.path.join(root, f)
                        if is_candidate_matching_series(full_p) and is_candidate_matching_season(full_p):
                            candidates.append(full_p)

    # C) /data/downloads y D) Misma carpeta de la temporada
    for search_dir in (Path("/data/downloads"), Path(new_file_path).parent):
        if search_dir.exists():
            for f in search_dir.iterdir():
                if f.is_file() and f.suffix.lower() == ".mkv" and str(f) != new_file_path:
                    f_ep = extract_episode_number(f.name)
                    if f_ep in target_eps and is_candidate_matching_series(str(f)) and is_candidate_matching_season(str(f)):
                        candidates.append(str(f))

    # Determinar qué archivo es la base de video y qué archivo es la fuente de doblaje
    light_ref = None
    dub_source = None
    dub_audio_id = None
    dub_audio_name = "Español Latino"
    delete_dub_after = False

    # Caso 1: El nuevo archivo es el pesado con Latino, y un candidato es el video ligero
    if new_latino_id is not None and new_size_mb >= 350:
        for cand in candidates:
            if os.path.exists(cand):
                sz = os.path.getsize(cand) / (1024 * 1024)
                if 80 <= sz <= 600:
                    light_ref = cand
                    dub_source = new_file_path
                    dub_audio_id = new_latino_id
                    dub_audio_name = new_latino_name
                    break

    # Caso 2: El nuevo archivo es el ligero Judas/ToonsHub, y un candidato en la papelera/downloads tiene el Latino
    elif new_size_mb < 600:
        for cand in candidates:
            if os.path.exists(cand):
                c_lat_id, c_lat_name = find_latino_audio(cand)
                if c_lat_id is not None:
                    light_ref = new_file_path
                    dub_source = cand
                    dub_audio_id = c_lat_id
                    dub_audio_name = c_lat_name
                    delete_dub_after = True
                    break

    if not light_ref or not dub_source or dub_audio_id is None:
        if new_latino_id is not None:
            msg = f"Audio latino detectado en {new_file_path}, pero no se encontró versión ligera previa de la misma serie/temporada."
        else:
            msg = f"El archivo nuevo es ligero ({round(new_size_mb, 1)} MB), pero no se encontró fuente con audio latino de la misma serie."
        print(f"[Sonarr Webhook] {msg}")

        # Notificar a Telegram la llegada del episodio importado
        time.sleep(2)
        try:
            send_episode_rich_notification(new_file_path, payload)
        except Exception as ex:
            print(f"[Sonarr Webhook] Error enviando notificacion RichMessage: {ex}")

        return {"status": "ok", "reason": msg, "action": "imported_as_is"}

    print(f"[Sonarr Webhook] Sincronizando: Video base [{light_ref}] + Audio latino [{dub_source}] (Pista {dub_audio_id}: {dub_audio_name})")

    # Sincronización y Muxing con comprobación estricta de confianza acústica
    tmp_out = None
    try:
        offset_sec, score = calculate_offset(light_ref, dub_source, trim=360)
        delay_ms = int(round(offset_sec * 1000))
        print(f"[Sonarr Webhook] Desfase calculado: {offset_sec:.3f}s ({delay_ms}ms), confianza Z: {score:.2f}")

        if score < 3.5:
            print(f"[Sonarr Webhook] ⚠️ Confianza acústica insuficiente (Z={score:.2f} < 3.5). Descartando para no mezclar audios erróneos.")
            return {"status": "rejected", "reason": f"Acoustic confidence too low ({score:.2f})"}

        tmp_out = f"{new_file_path}.tmp_autodub.mkv"
        cmd = [
            MKVMERGE_BIN,
            "-o", tmp_out,
            light_ref,
            "--no-video",
            "--no-subtitles",
            "--no-buttons",
            "--no-track-tags",
            "--no-global-tags",
            "--audio-tracks", str(dub_audio_id),
            "--sync", f"{dub_audio_id}:{delay_ms}",
            "--language", f"{dub_audio_id}:spa",
            "--track-name", f"{dub_audio_id}:{dub_audio_name}",
            dub_source
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if res.returncode not in (0, 1) or not os.path.exists(tmp_out):
            raise RuntimeError(f"mkvmerge falló: {res.stderr}")

        # Reemplazar archivo en tvshows
        shutil.move(tmp_out, new_file_path)
        try:
            os.chown(new_file_path, 1000, 1000)
            os.chmod(new_file_path, 0o644)
        except Exception:
            pass

        # Si el audio provenía de un archivo pesado en la papelera, limpiarlo
        if delete_dub_after and os.path.exists(dub_source):
            try:
                os.remove(dub_source)
                print(f"[Sonarr Webhook] Archivo pesado temporal eliminado de la papelera: {dub_source}")
            except Exception:
                pass

        final_sz = round(os.path.getsize(new_file_path) / (1024 * 1024), 1)
        print(f"[Sonarr Webhook] ¡Éxito! Archivo final optimizado: {final_sz} MB con Audio Dual en {new_file_path}")

        # Notificar a Telegram la finalización exitosa del Dual Audio
        try:
            send_episode_rich_notification(new_file_path, payload)
        except Exception as ex:
            print(f"[Sonarr Webhook] Error enviando notificacion RichMessage: {ex}")

        return {
            "status": "ok",
            "action": "synchronized_dual_audio",
            "final_size_mb": final_sz,
            "offset_sec": round(offset_sec, 3),
            "confidence_z": round(score, 2)
        }
    except Exception as e:
        print(f"[Sonarr Webhook] Error en proceso: {e}")
        if tmp_out and os.path.exists(tmp_out):
            try:
                os.remove(tmp_out)
            except Exception:
                pass
        return {"status": "error", "message": str(e)}


def run_web_server(port=PORT, open_browser=False):
    check_prerequisites()
    server_address = ("0.0.0.0", port)
    httpd = ThreadingHTTPServer(server_address, DubSyncWebHandler)
    url = f"http://0.0.0.0:{port}"
    print("\n" + "=" * 60)
    print(f"🎙️ AutoDub Studio Proxmox disponible en:")
    print(f"   -> http://192.168.1.125:{port} (Red Local)")
    print(f"   -> http://localhost:{port}")
    print("=" * 60 + "\n")

    if open_browser and IS_WINDOWS:
        try:
            import webbrowser
            webbrowser.open(f"http://localhost:{port}")
        except Exception:
            pass

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[!] Deteniendo servidor web...")
        httpd.server_close()


if __name__ == "__main__":
    run_web_server()
