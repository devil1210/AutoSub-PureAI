import collections
import datetime
from http.server import BaseHTTPRequestHandler
import json
import mimetypes
import os
import threading
import time
import urllib.parse
from autosub.config import app_config, save_config, get_ollama_ip, PROFILES_DIR
from autosub.core.context import (
    currently_processing,
    current_file_start_time,
    current_batch_num,
    current_total_batches,
    total_processing_time_sec,
    completed_jobs_count,
    cancel_current_flag,
    processing_lock,
    cancel_lock,
    config_lock,
    queue_lock,
    job_queue,
    retry_queue
)
from autosub.core.history import load_history, save_history, format_duration
from autosub.core.queue import save_persisted_queue
from autosub.core.watcher import get_parsed_watch_paths
from autosub.engine.elephant import get_or_create_series_profile
from autosub.providers.ollama import is_ollama_alive, get_ollama_models
from autosub.subtitles.mkv import check_mkvtoolnix

WEB_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(WEB_DIR, "static")
TEMPLATES_DIR = os.path.join(WEB_DIR, "templates")

def build_status_dict():
    with processing_lock:
        current = currently_processing
        st = current_file_start_time
        b_num = current_batch_num
        t_batches = current_total_batches
        tot_time = total_processing_time_sec
        jobs_done = completed_jobs_count

    elapsed_current = time.time() - st if current and st > 0 else 0.0
    progress_pct = int((b_num / t_batches) * 100) if t_batches > 0 else 0

    pc_online = is_ollama_alive(timeout=0.6, use_cache=True)

    with queue_lock:
        retry_items = list(retry_queue.values())

    active_jobs_list = []
    with job_queue.mutex:
        for idx, item in enumerate(job_queue.queue):
            c_only = item[2] if len(item) > 2 else False
            t_langs = item[3] if len(item) > 3 else None
            active_jobs_list.append({
                "index": idx + 1,
                "file_name": os.path.basename(item[0]),
                "path": item[0],
                "force": item[1],
                "context_only": c_only,
                "target_languages": t_langs
            })

    profiles_list = []
    if os.path.exists(PROFILES_DIR):
        for fn in sorted(os.listdir(PROFILES_DIR)):
            if fn.endswith(".json"):
                profiles_list.append(fn[:-5])

    with cancel_lock:
        is_cancelling = cancel_current_flag

    history_list = load_history()
    mkv_ok, mkvmerge_p, mkvextract_p = check_mkvtoolnix()

    return {
        "status": "online",
        "service": "AutoSub Pure AI v4.0",
        "system": {
            "mkvtoolnix_installed": mkv_ok,
            "mkvmerge_path": mkvmerge_p,
            "mkvextract_path": mkvextract_p
        },
        "config": app_config,
        "pc_gpu": {
            "online": pc_online,
            "active_model": app_config.get("local_model", "qwen3.5:9b"),
            "ip": get_ollama_ip(),
        },
        "metrics": {
            "currently_processing": current or "Ninguno",
            "current_elapsed_sec": elapsed_current,
            "current_elapsed_formatted": format_duration(elapsed_current),
            "current_batch": b_num,
            "total_batches": t_batches,
            "progress_percent": progress_pct,
            "total_processing_time_sec": tot_time,
            "total_processing_time_formatted": format_duration(tot_time),
            "completed_jobs_count": jobs_done,
            "active_queue_count": job_queue.qsize(),
            "profiles_count": len(profiles_list),
            "profiles": profiles_list,
            "cancelling": is_cancelling,
            "history_count": len(history_list)
        },
        "active_jobs_queue": active_jobs_list,
        "pending_retry_count": len(retry_items),
        "pending_retry_queue": retry_items,
    }

class AutoSubHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        accept = self.headers.get("Accept", "")
        parsed = urllib.parse.urlparse(self.path)
        clean_path = parsed.path
        is_json_api = clean_path.startswith("/api/") or "?format=json" in self.path

        # Rutas estáticas desacopladas
        if clean_path.startswith("/static/"):
            rel_path = clean_path[len("/static/"):].lstrip("/")
            file_path = os.path.abspath(os.path.join(STATIC_DIR, rel_path))
            # Prevenir path traversal
            if file_path.startswith(STATIC_DIR) and os.path.isfile(file_path):
                mime_type, _ = mimetypes.guess_type(file_path)
                mime_type = mime_type or "application/octet-stream"
                try:
                    with open(file_path, "rb") as f:
                        content = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", mime_type)
                    self.send_header("Content-Length", str(len(content)))
                    self.send_header("Cache-Control", "no-cache, must-revalidate")
                    self.end_headers()
                    self.wfile.write(content)
                    return
                except Exception as e:
                    self.send_response(500)
                    self.end_headers()
                    return
            self.send_response(404)
            self.end_headers()
            return

        if clean_path == "/api/status" or ("application/json" in accept and is_json_api) or "?format=json" in self.path:
            status_data = build_status_dict()
            self.send_json(status_data)
            return

        if clean_path == "/api/history":
            records = load_history()
            self.send_json({"history": records})
            return

        if clean_path == "/api/profiles":
            profiles = []
            if os.path.exists(PROFILES_DIR):
                for fn in sorted(os.listdir(PROFILES_DIR)):
                    if fn.endswith(".json"):
                        p_name = fn[:-5]
                        f_path = os.path.join(PROFILES_DIR, fn)
                        try:
                            with open(f_path, "r", encoding="utf-8") as f:
                                p_data = json.load(f)
                                profiles.append({
                                    "name": p_name,
                                    "official_title": p_data.get("official_title") or p_name,
                                    "characters_count": len(p_data.get("characters", [])),
                                    "glossary_count": len(p_data.get("glossary", {})),
                                    "modified": datetime.datetime.fromtimestamp(os.path.getmtime(f_path)).strftime("%Y-%m-%d %H:%M")
                                })
                        except Exception:
                            profiles.append({"name": p_name, "official_title": p_name, "characters_count": 0, "glossary_count": 0})
            self.send_json({"profiles": profiles})
            return

        if clean_path == "/api/profile":
            params = urllib.parse.parse_qs(parsed.query)
            name = params.get("name", [""])[0].strip()
            safe_name = os.path.basename(name).replace(".json", "")
            p_path = os.path.join(PROFILES_DIR, f"{safe_name}.json")
            if os.path.exists(p_path):
                with open(p_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "name": safe_name, "profile": data}, ensure_ascii=False).encode("utf-8"))
            else:
                self.send_response(404)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "not_found", "name": safe_name}, ensure_ascii=False).encode("utf-8"))
            return

        if clean_path == "/api/models":
            models = get_ollama_models()
            self.send_json({"models": models})
            return

        if clean_path.startswith("/api/series_list"):
            catalog = []
            for w_dir, cat_name in get_parsed_watch_paths():
                if os.path.exists(w_dir):
                    for entry in sorted(os.listdir(w_dir)):
                        full_p = os.path.join(w_dir, entry)
                        if os.path.isdir(full_p) and not entry.startswith("."):
                            catalog.append({
                                "name": entry,
                                "category": cat_name,
                                "path": full_p
                            })
                        elif os.path.isfile(full_p) and entry.lower().endswith((".mkv", ".mp4")):
                            catalog.append({
                                "name": entry,
                                "category": cat_name,
                                "path": full_p,
                                "is_single_file": True
                            })
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"catalog": catalog, "total": len(catalog)}, ensure_ascii=False).encode("utf-8"))
            return

        if clean_path.startswith("/api/series_files"):
            params = urllib.parse.parse_qs(parsed.query)
            target_path = params.get("path", [""])[0].strip()
            series_name = params.get("series", [""])[0].strip()

            files_list = []
            if not target_path and series_name:
                for w_dir, _ in get_parsed_watch_paths():
                    cand = os.path.join(w_dir, series_name)
                    if os.path.exists(cand):
                        target_path = cand
                        break

            if target_path and os.path.exists(target_path):
                if os.path.isfile(target_path):
                    base_no_ext, _ = os.path.splitext(target_path)
                    has_sub = any(os.path.exists(f"{base_no_ext}{ext}") for ext in [".es.srt", ".es-419.srt", ".es.ass"])
                    try:
                        sz_mb = round(os.path.getsize(target_path) / (1024 * 1024), 1)
                    except Exception:
                        sz_mb = 0
                    files_list.append({
                        "name": os.path.basename(target_path),
                        "rel_path": os.path.basename(target_path),
                        "path": target_path,
                        "has_sub": has_sub,
                        "size_mb": sz_mb
                    })
                else:
                    for root, _, files in os.walk(target_path):
                        for f in sorted(files):
                            if f.lower().endswith((".mkv", ".mp4")):
                                f_full = os.path.join(root, f)
                                base_no_ext, _ = os.path.splitext(f_full)
                                has_sub = any(os.path.exists(f"{base_no_ext}{ext}") for ext in [".es.srt", ".es-419.srt", ".es.ass"])
                                rel_path = os.path.relpath(f_full, target_path)
                                try:
                                    sz_mb = round(os.path.getsize(f_full) / (1024 * 1024), 1)
                                except Exception:
                                    sz_mb = 0
                                files_list.append({
                                    "name": f,
                                    "rel_path": rel_path,
                                    "path": f_full,
                                    "has_sub": has_sub,
                                    "size_mb": sz_mb
                                })

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"files": files_list}, ensure_ascii=False).encode("utf-8"))
            return

        # Renderizar index.html desacoplado
        template_file = os.path.join(TEMPLATES_DIR, "index.html")
        if os.path.exists(template_file):
            with open(template_file, "rb") as f:
                html_bytes = f.read()
        else:
            html_bytes = b"<h1>AutoSub v4.0 - Template not found</h1>"

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html_bytes)))
        self.end_headers()
        self.wfile.write(html_bytes)

    def do_POST(self):
        import autosub.core.context as ctx

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        try:
            payload = json.loads(body)
        except Exception:
            payload = {}

        clean_path = urllib.parse.urlparse(self.path).path

        if clean_path == "/api/cancel":
            with cancel_lock:
                ctx.cancel_current_flag = True
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"cancelling"}')
            return

        if clean_path == "/api/cancel_queue_item":
            path_to_remove = payload.get("path", "").strip()
            if path_to_remove:
                with queue_lock:
                    if path_to_remove in retry_queue:
                        del retry_queue[path_to_remove]
                        save_persisted_queue()
                with job_queue.mutex:
                    job_queue.queue = collections.deque(item for item in job_queue.queue if item[0] != path_to_remove)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
            return

        if clean_path == "/api/history/clear":
            save_history([])
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
            return

        if clean_path == "/api/config":
            with config_lock:
                for k in ["local_only", "local_model", "cloud_model", "enable_openrouter", "batch_size",
                          "fast_mode", "proofread", "audit_existing", "ollama_ip", "ollama_port", "gemini_api_key",
                          "openrouter_api_key", "watch_paths", "libraries", "source_priority", "target_languages"]:
                    if k in payload:
                        app_config[k] = payload[k]
                save_config(app_config)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "config": app_config}).encode("utf-8"))
            return

        if clean_path == "/api/profile/save":
            name = payload.get("name", "").strip()
            p_data = payload.get("profile", {})
            if name and isinstance(p_data, dict):
                safe_name = os.path.basename(name).replace(".json", "")
                p_path = os.path.join(PROFILES_DIR, f"{safe_name}.json")
                with open(p_path, "w", encoding="utf-8") as f:
                    json.dump(p_data, f, ensure_ascii=False, indent=2)
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "name": safe_name}).encode("utf-8"))
                return
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"error","message":"Nombre o datos invalidos"}')
            return

        if clean_path == "/api/profile/delete":
            name = payload.get("name", "").strip()
            if name:
                safe_name = os.path.basename(name).replace(".json", "")
                p_path = os.path.join(PROFILES_DIR, f"{safe_name}.json")
                if os.path.exists(p_path):
                    try:
                        os.remove(p_path)
                    except Exception:
                        pass
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
            return

        if clean_path == "/api/refresh_profile":
            series = payload.get("series", "").strip()
            if series:
                prof, p_path = get_or_create_series_profile(series, force_refresh=True)
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "status": "ok",
                    "path": p_path,
                    "characters_count": len(prof.get("characters", [])),
                    "glossary_count": len(prof.get("glossary", {}))
                }, ensure_ascii=False).encode("utf-8"))
                return

        if clean_path == "/api/batch_enqueue":
            paths = payload.get("paths", [])
            force = bool(payload.get("force", True))
            context_only = bool(payload.get("context_only", False))
            target_languages = payload.get("target_languages")
            if not target_languages or not isinstance(target_languages, list):
                target_languages = None
            enqueued = 0
            if isinstance(paths, list):
                for p in paths:
                    p_clean = str(p).strip()
                    if p_clean and os.path.exists(p_clean):
                        job_queue.put((p_clean, force, context_only, target_languages))
                        enqueued += 1
            mode_lbl = "Solo Contexto" if context_only else f"Traducción {target_languages or app_config.get('target_languages', ['es-419'])}"
            print(f"[AutoSub] Encolados masivamente {enqueued} archivos ({mode_lbl}, force={force}).", flush=True)
            self.send_json({"status": "ok", "enqueued_count": enqueued, "context_only": context_only})
            return

        event_type = payload.get("eventType")
        if event_type == "Test":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok","message":"AutoSub v4.0 operativo"}')
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status":"received"}')

        force = payload.get("force", False)

        if event_type in ["Download", "Upgrade", "EpisodeFileImport", "FileImport"]:
            file_path = payload.get("episodeFile", {}).get("path")
            if file_path:
                job_queue.put((file_path, force))

        elif "path" in payload:
            path = payload["path"]
            def enqueue_path(p, f):
                try:
                    if os.path.isdir(p):
                        for root, _, files in os.walk(p):
                            for file in sorted(files):
                                if file.lower().endswith((".mkv", ".mp4")):
                                    job_queue.put((os.path.join(root, file), f))
                    else:
                        job_queue.put((p, f))
                except Exception as ex:
                    print(f"[AutoSub] Error encolando path '{p}': {ex}", flush=True)
            import threading
            threading.Thread(target=enqueue_path, args=(path, force), daemon=True).start()
