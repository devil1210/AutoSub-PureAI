import json
import re
import time
from autosub.config import MAX_GEMINI_RPM, MIN_GEMINI_INTERVAL
from autosub.core.context import next_gemini_lock, gemini_rate_lock, gemini_request_timestamps

_next_gemini_allowed = 0.0
_model_cooldowns = {}

def get_next_gemini_allowed():
    global _next_gemini_allowed
    return _next_gemini_allowed

def get_model_cooldown(model_name: str) -> float:
    return _model_cooldowns.get(model_name, 0.0)

def set_model_cooldown(model_name: str, delay_seconds: float):
    candidate = time.time() + delay_seconds
    if candidate > _model_cooldowns.get(model_name, 0.0):
        _model_cooldowns[model_name] = candidate
        print(f"[Gemini Cooldown] Modelo '{model_name}' bloqueado por {delay_seconds:.0f}s "
              f"(hasta {time.strftime('%H:%M:%S', time.localtime(candidate))})", flush=True)

def set_gemini_cooldown(delay_seconds):
    global _next_gemini_allowed
    # Solo aplicar cooldown global si es una pausa corta de RPM (<= 120s)
    if delay_seconds <= 120:
        with next_gemini_lock:
            candidate = time.time() + delay_seconds
            if candidate > _next_gemini_allowed:
                _next_gemini_allowed = candidate
                print(f"[Gemini Cooldown] Pausa global de {delay_seconds:.0f}s "
                      f"(hasta {time.strftime('%H:%M:%S', time.localtime(_next_gemini_allowed))})", flush=True)

def get_retry_delay_from_error(err_body):
    try:
        obj = json.loads(err_body)
        for detail in obj.get("error", {}).get("details", []):
            if detail.get("@type") == "type.googleapis.com/google.rpc.RetryInfo":
                delay_str = detail.get("retryDelay", "")
                m = re.match(r"(\d+)s", delay_str)
                if m:
                    return int(m.group(1))
    except Exception:
        pass
    return None

def wait_for_gemini_slot():
    """Garantiza que nunca se superen 10 RPM en ninguna ventana móvil de 60 segundos."""
    with gemini_rate_lock:
        now = time.time()
        while gemini_request_timestamps and now - gemini_request_timestamps[0] >= 60.0:
            gemini_request_timestamps.pop(0)

        if len(gemini_request_timestamps) >= MAX_GEMINI_RPM:
            wait_time = 60.0 - (now - gemini_request_timestamps[0]) + 0.5
            if wait_time > 0:
                print(f"[Gemini RateLimiter] 🛡️ Protección de Cuota: alcanzadas {len(gemini_request_timestamps)}/{MAX_GEMINI_RPM} RPM. Pausando {wait_time:.1f}s para no saturar el límite...", flush=True)
                time.sleep(wait_time)
                now = time.time()
                while gemini_request_timestamps and now - gemini_request_timestamps[0] >= 60.0:
                    gemini_request_timestamps.pop(0)

        if gemini_request_timestamps:
            elapsed = now - gemini_request_timestamps[-1]
            if elapsed < MIN_GEMINI_INTERVAL:
                time.sleep(MIN_GEMINI_INTERVAL - elapsed)
                now = time.time()

        gemini_request_timestamps.append(now)
