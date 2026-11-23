import json
import os
import time
import collections
from autosub.config import QUEUE_FILE, app_config
from autosub.core.context import job_queue, retry_queue, queue_lock, currently_processing
from autosub.providers.ollama import is_ollama_alive

MAX_RETRY_ATTEMPTS = 5

def load_persisted_queue():
    global retry_queue
    if os.path.exists(QUEUE_FILE):
        try:
            with open(QUEUE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    with queue_lock:
                        retry_queue.clear()
                        retry_queue.update(data)
        except Exception as e:
            print(f"[Queue] Error leyendo cola persistida: {e}", flush=True)

def save_persisted_queue():
    with queue_lock:
        tmp_file = f"{QUEUE_FILE}.tmp"
        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(retry_queue, f, ensure_ascii=False, indent=2)
            os.replace(tmp_file, QUEUE_FILE)
        except Exception as e:
            if os.path.exists(tmp_file):
                try:
                    os.remove(tmp_file)
                except OSError:
                    pass
            print(f"[Queue] Error guardando cola persistida: {e}", flush=True)

def enqueue_retry(video_path, force, series_name, error_reason="", increment_attempt=True, context_only=False, target_languages=None):
    with queue_lock:
        if video_path not in retry_queue:
            retry_queue[video_path] = {
                "path": video_path,
                "force": force,
                "series_name": series_name,
                "attempts": 1 if increment_attempt else 0,
                "last_error": error_reason,
                "context_only": context_only,
                "target_languages": target_languages,
                "next_retry_at": time.time() + (60 if increment_attempt else 15)
            }
        else:
            if increment_attempt:
                retry_queue[video_path]["attempts"] += 1
            att = retry_queue[video_path]["attempts"]
            retry_queue[video_path]["last_error"] = error_reason
            if context_only:
                retry_queue[video_path]["context_only"] = context_only
            if target_languages:
                retry_queue[video_path]["target_languages"] = target_languages
            if att > MAX_RETRY_ATTEMPTS:
                print(f"[AutoSub] ⚠️ Archivo superó el máximo de {MAX_RETRY_ATTEMPTS} reintentos: {video_path} (Razón: {error_reason})", flush=True)
                retry_queue[video_path]["status"] = "Descartado"
                retry_queue[video_path]["next_retry_at"] = 0
                save_persisted_queue()
                return
            backoff = 15 if not increment_attempt else min(300, 30 * (2 ** min(att, 4)))
            retry_queue[video_path]["next_retry_at"] = time.time() + backoff
        save_persisted_queue()

def sort_jobs_canonically(items):
    """
    Ordena una lista de trabajos respetando el orden cronológico/canónico de emisión:
    1. Agrupa por serie preservando el orden relativo de llegada de cada serie (FIFO entre series).
    2. Dentro de cada serie, ordena estrictamente por temporada y episodio (seasonNumber, episodeNumber, filename).
    """
    if not items:
        return []

    try:
        from autosub.engine.elephant import extract_series_name
    except ImportError:
        def extract_series_name(p):
            return os.path.basename(os.path.dirname(p))

    try:
        from autosub.core.sonarr import extract_season_episode_fallback
    except ImportError:
        def extract_season_episode_fallback(p, s=""):
            import re
            m = re.search(r'[sS](\d+)[eE](\d+)', p)
            return (int(m.group(1)), int(m.group(2))) if m else (9999, 9999)

    series_first_seen = {}
    item_keys = []

    for idx, it in enumerate(items):
        vpath = it[0] if isinstance(it, (tuple, list)) else str(it)
        s_name = extract_series_name(vpath).lower() if vpath else ""
        if s_name not in series_first_seen:
            series_first_seen[s_name] = len(series_first_seen)
        
        s_num, ep_num = extract_season_episode_fallback(vpath, s_name) if vpath else (9999, 9999)
        item_keys.append((series_first_seen[s_name], s_num, ep_num, os.path.basename(vpath), idx, it))

    item_keys.sort(key=lambda x: (x[0], x[1], x[2], x[3], x[4]))
    return [x[5] for x in item_keys]

def reorder_job_queue():
    """Reordena la cola de trabajos de forma canónica y segura bajo el mutex de la cola."""
    with job_queue.mutex:
        if len(job_queue.queue) > 1:
            sorted_items = sort_jobs_canonically(list(job_queue.queue))
            job_queue.queue = collections.deque(sorted_items)

def check_quota_guard():
    """
    Si estamos en la ventana crítica de 04:30 AM a 05:05 AM, pausa el worker
    para evitar que tareas en cola consuman la cuota diaria fresca que Google reinicia a las 05:00 AM.
    """
    import datetime
    now = datetime.datetime.now()
    if (now.hour == 4 and now.minute >= 30) or (now.hour == 5 and now.minute < 5):
        print(f"[QuotaGuard] 🛑 Ventana crítica de corte (04:30 - 05:05 AM). Hora actual: {now.strftime('%H:%M:%S')}. "
              f"Pausando cola para proteger el reinicio de cuota de las 05:00 AM...", flush=True)
        while True:
            time.sleep(15)
            n = datetime.datetime.now()
            if not ((n.hour == 4 and n.minute >= 30) or (n.hour == 5 and n.minute < 5)):
                break
        print(f"[QuotaGuard] 🌅 Cuota de Google reiniciada con éxito (05:00 AM). Reanudando cola con cuota 100% fresca.", flush=True)

def queue_worker(process_file_fn):
    while True:
        try:
            reorder_job_queue()
            item = job_queue.get()
            check_quota_guard()
            if len(item) == 4:
                v_path, force, context_only, target_langs = item
            elif len(item) == 3:
                v_path, force, context_only = item
                target_langs = None
            else:
                v_path, force = item
                context_only = False
                target_langs = None
            process_file_fn(v_path, force=force, context_only=context_only, target_languages=target_langs)
            job_queue.task_done()
        except Exception as e:
            print(f"[Worker Error] {e}", flush=True)
        time.sleep(0.5)

def retry_worker():
    while True:
        try:
            now = time.time()
            due_items = []
            with queue_lock:
                for path, item in list(retry_queue.items()):
                    next_t = item.get("next_retry_at", 0)
                    if next_t > 0 and now >= next_t:
                        due_items.append((
                            path,
                            item.get("force", False),
                            item.get("context_only", False),
                            item.get("target_languages")
                        ))

            for path, force, c_only, t_langs in due_items:
                if app_config.get("local_only", True) and not is_ollama_alive(timeout=1.0):
                    break
                in_job_q = any(path == item[0] for item in list(job_queue.queue))
                if not in_job_q and currently_processing != os.path.basename(path):
                    job_queue.put((path, force, c_only, t_langs))

            if due_items:
                reorder_job_queue()
        except Exception as e:
            print(f"[Retry Worker Error] {e}", flush=True)
        time.sleep(5)
