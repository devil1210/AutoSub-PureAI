#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sync_web.py - Servidor Web UI para Sincronización Automática de Audio Latino

Interfaz moderna inspirada en AutoSub para seleccionar series, capítulos de Nyaa
e inyectar pistas de audio de Telegram con ajuste de desfase milimétrico.
"""

import os
import sys
import json
import uuid
import time
import shutil
import threading
import subprocess
import urllib.parse
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

# Importar funciones de sincronización de sync_anime_dub
from tools.sync_anime_dub import (
    MKVMERGE_BIN, FFMPEG_BIN, check_prerequisites,
    get_mkv_tracks, get_audio_track_id, calculate_offset,
    sync_single_episode, extract_episode_number
)

PORT = 5056
CONFIG_FILE = Path(__file__).parent / "sync_web_config.json"

DEFAULT_CONFIG = {
    "anime_root_dir": r"E:\Descargas",
    "telegram_root_dir": r"E:\Descargas",
    "trim_seconds": 360,
    "default_track": False,
    "track_name": "Español Latino"
}

# Estado de trabajos en segundo plano
jobs_lock = threading.Lock()
active_jobs = {}


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


def open_file_dialog_windows(initial_dir=""):
    """Abre el diálogo nativo de Windows para seleccionar un archivo."""
    ps_script = f'''
    Add-Type -AssemblyName System.Windows.Forms
    $d = New-Object System.Windows.Forms.OpenFileDialog
    $d.Filter = "Video y Audio (*.mp4;*.mkv;*.m4v;*.webm;*.aac;*.m4a)|*.mp4;*.mkv;*.m4v;*.webm;*.aac;*.m4a|Todos los archivos (*.*)|*.*"
    $d.Title = "Seleccionar archivo de Telegram con doblaje latino"
    $d.TopMost = $true
    if ("{initial_dir}" -and (Test-Path "{initial_dir}")) {{ $d.InitialDirectory = "{initial_dir}" }}
    if ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {{
        [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
        Write-Output $d.FileName
    }}
    '''
    try:
        res = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", ps_script],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        return res.stdout.strip()
    except Exception:
        return ""


def open_folder_dialog_windows(initial_dir=""):
    """Abre el diálogo nativo de Windows para seleccionar una carpeta."""
    ps_script = f'''
    Add-Type -AssemblyName System.Windows.Forms
    $d = New-Object System.Windows.Forms.FolderBrowserDialog
    $d.Description = "Seleccionar carpeta raíz de series o anime"
    if ("{initial_dir}" -and (Test-Path "{initial_dir}")) {{ $d.SelectedPath = "{initial_dir}" }}
    if ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {{
        [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
        Write-Output $d.SelectedPath
    }}
    '''
    try:
        res = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", ps_script],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        return res.stdout.strip()
    except Exception:
        return ""


HTML_PAGE = """<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AutoDub Sync Studio | Doblaje Latino Inteligente</title>
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
    .container { max-width: 1300px; margin: 0 auto; }
    
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
    }
    .btn-primary { background: linear-gradient(135deg, #0284c7, #38bdf8); color: white; }
    .btn-primary:hover { filter: brightness(1.15); transform: translateY(-1px); }
    .btn-secondary { background: rgba(255, 255, 255, 0.08); color: var(--text); border: 1px solid var(--border); }
    .btn-secondary:hover { background: rgba(255, 255, 255, 0.15); }
    .btn-success { background: linear-gradient(135deg, #059669, #10b981); color: white; }
    .btn-sm { padding: 6px 12px; font-size: 12px; }

    select, input[type="text"] {
      background: #0f172a; border: 1px solid var(--border); color: var(--text);
      padding: 9px 14px; border-radius: 8px; font-size: 13px; width: 100%;
      outline: none; transition: border-color 0.2s;
    }
    select:focus, input[type="text"]:focus { border-color: var(--border-focus); }

    .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }

    /* Series Dropdown & Controls */
    .controls-row { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
    .controls-row select { flex: 1; min-width: 280px; }

    /* Table styling */
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

    /* File picker field */
    .dub-input-group { display: flex; gap: 6px; align-items: center; width: 100%; min-width: 340px; }
    .dub-input-group input { font-size: 12px; padding: 7px 10px; font-family: monospace; }

    /* Modal / Progress overlay */
    #sync-modal {
      display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%;
      background: rgba(0, 0, 0, 0.75); backdrop-filter: blur(8px);
      z-index: 9999; justify-content: center; align-items: center;
    }
    .modal-box {
      background: #111827; border: 1px solid var(--border);
      border-radius: 16px; padding: 28px; width: 560px; max-width: 90%;
      box-shadow: 0 20px 40px rgba(0, 0, 0, 0.6);
    }
    .progress-bar-wrap {
      width: 100%; height: 8px; background: #1e293b;
      border-radius: 4px; overflow: hidden; margin: 16px 0;
    }
    .progress-bar-fill {
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
  </style>
</head>
<body>

<div class="container">
  <header>
    <div class="logo-box">
      <div class="logo-icon">🎙️</div>
      <div class="logo-title">
        <h1>AutoDub Sync Studio</h1>
        <p>Alineación acústica milimétrica e inyección de audio latino en videos de Nyaa 1080p</p>
      </div>
    </div>
    <div class="status-badge">
      <div class="status-dot"></div>
      <span>MKVToolNix + FFmpeg Listos</span>
    </div>
  </header>

  <!-- Card: Configuración de Carpetas -->
  <div class="card">
    <div class="card-title">📂 Carpeta Raíz de Animes / Series</div>
    <div style="display:flex; gap:12px; align-items:center;">
      <input type="text" id="cfg-anime-dir" placeholder="E:\\Descargas">
      <button class="btn btn-secondary" onclick="browseAnimeFolder()">📁 Examinar...</button>
      <button class="btn btn-primary" onclick="loadSeriesCatalog()">🔄 Cargar Series</button>
    </div>
  </div>

  <!-- Card: Selector de Serie y Capítulos -->
  <div class="card">
    <div class="card-title">📺 Selección de Serie y Episodios</div>
    <div class="controls-row">
      <select id="series-select" onchange="onSeriesSelected()">
        <option value="">-- Seleccionar Serie --</option>
      </select>
      <button class="btn btn-secondary btn-sm" onclick="loadSeriesCatalog()">🔄 Refrescar</button>
    </div>

    <div class="table-container" style="margin-top:16px;">
      <table>
        <thead>
          <tr>
            <th style="width:70px;">Cap</th>
            <th>Archivo de Nyaa (1080p)</th>
            <th style="width:230px;">Pistas Detectadas</th>
            <th>Archivo con Audio Latino (Telegram)</th>
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

<!-- Modal de Progreso de Sincronización -->
<div id="sync-modal">
  <div class="modal-box">
    <h3 id="modal-title" style="margin-bottom:8px; font-size:18px;">⚡ Sincronizando Audio...</h3>
    <p id="modal-desc" style="font-size:13px; color:var(--text-muted); margin-bottom:12px;">Analizando correlación de audio MFCC...</p>
    
    <div class="progress-bar-wrap">
      <div class="progress-bar-fill"></div>
    </div>

    <div class="log-box" id="modal-logs">Iniciando proceso...</div>

    <div style="margin-top:18px; text-align:right;">
      <button class="btn btn-secondary btn-sm" id="modal-close-btn" style="display:none;" onclick="closeModal()">Cerrar</button>
    </div>
  </div>
</div>

<script>
let currentConfig = {};
let currentEpisodes = [];

window.addEventListener('DOMContentLoaded', async () => {
  await loadConfig();
  await loadSeriesCatalog();
});

async function loadConfig() {
  const res = await fetch('/api/config');
  currentConfig = await res.json();
  document.getElementById('cfg-anime-dir').value = currentConfig.anime_root_dir || 'E:\\\\Descargas';
}

async function browseAnimeFolder() {
  const res = await fetch('/api/browse_dir', { method: 'POST' });
  const data = await res.json();
  if (data.path) {
    document.getElementById('cfg-anime-dir').value = data.path;
    await fetch('/api/config', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ anime_root_dir: data.path })
    });
    loadSeriesCatalog();
  }
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
  const tbody = document.getElementById('episodes-tbody');
  if (!sPath) {
    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; color:var(--text-muted); padding:24px;">Selecciona una serie arriba para listar los episodios.</td></tr>';
    return;
  }
  tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; color:var(--text-muted); padding:24px;">Analizando pistas de los episodios con MKVToolNix...</td></tr>';

  try {
    const res = await fetch('/api/episodes?path=' + encodeURIComponent(sPath));
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
            <input type="text" id="dub-path-${idx}" placeholder="Ruta del video/audio de Telegram..." value="${ep.suggested_dub || ''}">
            <button class="btn btn-secondary btn-sm" onclick="browseDubFile(${idx})">📂</button>
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

async function browseDubFile(idx) {
  const res = await fetch('/api/browse_file', { method: 'POST' });
  const data = await res.json();
  if (data.path) {
    document.getElementById(`dub-path-${idx}`).value = data.path;
  }
}

async function startSyncJob(idx) {
  const ep = currentEpisodes[idx];
  const dubInput = document.getElementById(`dub-path-${idx}`);
  const dubPath = dubInput.value.trim();

  if (!dubPath) {
    alert('Por favor selecciona o escribe la ruta del archivo con audio en español (Telegram).');
    return;
  }

  // Abrir modal
  const modal = document.getElementById('sync-modal');
  const logs = document.getElementById('modal-logs');
  const title = document.getElementById('modal-title');
  const desc = document.getElementById('modal-desc');
  const closeBtn = document.getElementById('modal-close-btn');

  modal.style.display = 'flex';
  closeBtn.style.display = 'none';
  title.innerText = `⚡ Sincronizando ${ep.name}`;
  desc.innerText = 'Calculando correlación acústica de MFCCs con FFmpeg y mkvmerge...';
  logs.innerText = `Iniciando proceso...\nBase: ${ep.name}\nDoblaje: ${dubPath}\n`;

  try {
    const res = await fetch('/api/sync', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        nyaa_file: ep.path,
        dub_file: dubPath
      })
    });
    const result = await res.json();
    
    if (result.status === 'ok') {
      title.innerText = '✅ ¡Sincronización Exitosa!';
      desc.innerText = `Desfase: ${result.offset_sec > 0 ? '+' : ''}${result.offset_sec}s (${result.delay_ms} ms) | Confianza: ${result.score.toFixed(2)}`;
      logs.innerText += `\n[OK] Desfase temporal: ${result.delay_ms} ms\n[OK] Pista de audio inyectada con éxito.\nArchivo creado:\n-> ${result.output_file}\n`;
      closeBtn.style.display = 'inline-block';
      // Refrescar lista de episodios
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
    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        clean_path = parsed.path

        if clean_path == "/" or clean_path == "/index.html":
            html_bytes = HTML_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html_bytes)))
            self.end_headers()
            self.wfile.write(html_bytes)
            return

        if clean_path == "/api/config":
            self.send_json(load_config())
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
            episodes = []
            
            if target_path and os.path.exists(target_path):
                files_to_check = []
                if os.path.isfile(target_path):
                    files_to_check.append(Path(target_path))
                else:
                    p = Path(target_path)
                    exts = {".mkv", ".mp4", ".m4v"}
                    for f in sorted(p.iterdir()):
                        if f.is_file() and f.suffix.lower() in exts:
                            files_to_check.append(f)

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

                    episodes.append({
                        "name": f.name,
                        "path": str(f.resolve()),
                        "size_mb": sz_mb,
                        "ep_num": ep_num,
                        "has_jp_audio": has_jp_audio,
                        "has_es_sub": has_es_sub,
                        "has_latino_audio": has_latino_audio
                    })

            self.send_json({"episodes": episodes})
            return

        self.send_error(404, "Not Found")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
        try:
            payload = json.loads(body)
        except Exception:
            payload = {}

        clean_path = urllib.parse.urlparse(self.path).path

        if clean_path == "/api/config":
            cfg = load_config()
            cfg.update(payload)
            save_config(cfg)
            self.send_json({"status": "ok", "config": cfg})
            return

        if clean_path == "/api/browse_dir":
            selected = open_folder_dialog_windows(payload.get("initial", ""))
            self.send_json({"path": selected})
            return

        if clean_path == "/api/browse_file":
            selected = open_file_dialog_windows(payload.get("initial", ""))
            self.send_json({"path": selected})
            return

        if clean_path == "/api/sync":
            nyaa_file = payload.get("nyaa_file", "").strip()
            dub_file = payload.get("dub_file", "").strip()
            trim = payload.get("trim", 360)

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

                self.send_json({
                    "status": "ok",
                    "offset_sec": round(offset_sec, 3),
                    "delay_ms": delay_ms,
                    "score": round(score, 2),
                    "output_file": out_file
                })
            except Exception as e:
                self.send_json({"status": "error", "message": str(e)}, status=500)
            return

        self.send_error(404, "Not Found")


def run_web_server(port=PORT, open_browser=True):
    check_prerequisites()
    server_address = ("0.0.0.0", port)
    httpd = HTTPServer(server_address, DubSyncWebHandler)
    url = f"http://localhost:{port}"
    print("\n" + "=" * 60)
    print(f"🎙️ AutoDub Sync Studio Web UI disponible en:")
    print(f"   -> {url}")
    print("=" * 60 + "\n")

    if open_browser:
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[!] Deteniendo servidor web...")
        httpd.server_close()


if __name__ == "__main__":
    run_web_server()
