import json
import os
from autosub.config import HISTORY_FILE
from autosub.core.context import history_lock

def format_duration(seconds):
    try:
        s = int(seconds)
        m, s = divmod(s, 60)
        h, m = divmod(m, 60)
        return f"{h}h {m}m {s}s" if h > 0 else (f"{m}m {s}s" if m > 0 else f"{s}s")
    except Exception:
        return f"{seconds}s"

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
                if isinstance(d, list):
                    return d
        except Exception as e:
            print(f"[History] Error leyendo {HISTORY_FILE}: {e}", flush=True)
    return []

def save_history(records):
    with history_lock:
        try:
            with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(records[:1000], f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[History] Error guardando {HISTORY_FILE}: {e}", flush=True)

def append_history_record(record):
    with history_lock:
        hist = load_history()
        hist.insert(0, record)
        save_history(hist)
